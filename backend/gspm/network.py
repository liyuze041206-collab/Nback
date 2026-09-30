# Source: existing GSPM-Net research ssl_common.py (2026-09-29).
# Kept unchanged below for checkpoint and numeric compatibility.
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn


def load_cache(path: Path) -> dict:
    z = np.load(path, allow_pickle=True)
    n = len(z["y"])
    return {
        "X": z["X"].astype(np.float32),
        "y": z["y"].astype(np.int64),
        "subjects": z["subject"].astype(str),
        "sessions": z["session"].astype(str),
        "tasks": z["task"].astype(str),
        "sources": z["source"].astype(str) if "source" in z.files else z["task"].astype(str),
        "bands": [str(x) for x in z["bands"].tolist()],
        "channels": [str(x) for x in z["keep_channels"].tolist()],
        "epoch_start_sec": z["epoch_start_sec"].astype(np.float64) if "epoch_start_sec" in z.files else np.arange(n, dtype=np.float64),
    }


def split_calibration(
    test_idx: np.ndarray,
    y: np.ndarray,
    n_per_class: int,
    seed: int,
    sessions: np.ndarray,
    epoch_start_sec: np.ndarray,
    mode: str = "session_start",
) -> tuple[np.ndarray, np.ndarray]:
    if mode not in {"random", "session_start"}:
        raise ValueError(f"unknown calibration mode: {mode}")
    rng = np.random.default_rng(seed)
    selected = []
    for cls in (0, 1):
        idx = test_idx[y[test_idx] == cls].copy()
        if mode == "random":
            rng.shuffle(idx)
            chosen = idx[: min(n_per_class, len(idx))]
        else:
            available = sorted(set(sessions[idx].tolist()))
            base, remainder = divmod(n_per_class, len(available))
            parts, used = [], set()
            for position, session in enumerate(available):
                session_idx = idx[sessions[idx] == session]
                order = np.lexsort((session_idx, epoch_start_sec[session_idx]))
                take = base + int(position < remainder)
                picked = session_idx[order][:take]
                parts.append(picked)
                used.update(picked.tolist())
            chosen = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
            target_n = min(n_per_class, len(idx))
            if len(chosen) < target_n:
                remaining = np.asarray([i for i in idx if int(i) not in used], dtype=np.int64)
                order = np.lexsort((remaining, epoch_start_sec[remaining], sessions[remaining]))
                chosen = np.concatenate([chosen, remaining[order][: target_n - len(chosen)]])
        selected.append(chosen)
    calibration = np.sort(np.concatenate(selected).astype(np.int64))
    calibration_set = set(calibration.tolist())
    query = np.asarray([i for i in test_idx if int(i) not in calibration_set], dtype=np.int64)
    return calibration, query


def standardize_by_subject(X: np.ndarray, subjects: np.ndarray) -> np.ndarray:
    output = X.copy()
    for subject in sorted(set(subjects.tolist())):
        idx = np.where(subjects == subject)[0]
        mean = output[idx].mean(axis=0, keepdims=True)
        std = output[idx].std(axis=0, keepdims=True)
        output[idx] = (output[idx] - mean) / (std + 1e-6)
    return output.astype(np.float32)


def standardize_by_train(X: np.ndarray, train_idx: np.ndarray) -> np.ndarray:
    mean = X[train_idx].mean(axis=0, keepdims=True)
    std = X[train_idx].std(axis=0, keepdims=True)
    return ((X - mean) / (std + 1e-6)).astype(np.float32)


class ResidualDenseBlock(nn.Module):
    def __init__(self, dim: int, hidden: int, dropout: float):
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(dim),
            nn.Linear(dim, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, dim),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return x + self.net(x)


class EnhancedGatedSpectralChannelNet(nn.Module):
    """Single-stream enhanced spectral-channel gated encoder."""

    def __init__(self, n_bands: int, n_channels: int, feat_dim=96, hidden=128, dropout=0.25, ablation="full"):
        super().__init__()
        self.n_bands = n_bands
        self.n_channels = n_channels
        self.feat_dim = feat_dim
        self.ablation = ablation
        flat_dim, rank = n_bands * n_channels, 16
        gate_hidden = max(64, hidden)
        self.band_gate = nn.Sequential(
            nn.LayerNorm(n_bands * 2), nn.Linear(n_bands * 2, gate_hidden), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(gate_hidden, n_bands), nn.Tanh(),
        )
        self.channel_gate = nn.Sequential(
            nn.LayerNorm(n_channels * 2), nn.Linear(n_channels * 2, gate_hidden), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(gate_hidden, n_channels), nn.Tanh(),
        )
        self.band_channel_gate = nn.Sequential(
            nn.LayerNorm(flat_dim), nn.Linear(flat_dim, gate_hidden), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(gate_hidden, flat_dim), nn.Tanh(),
        )
        self.low_rank_band = nn.Parameter(torch.randn(n_bands, rank) * 0.02)
        self.low_rank_channel = nn.Parameter(torch.randn(n_channels, rank) * 0.02)
        self.rank_project = nn.Sequential(nn.LayerNorm(rank), nn.Linear(rank, rank), nn.GELU())
        enhanced_in = flat_dim * 5 + n_bands * 2 + n_channels * 2 + rank + 2
        layers = [nn.LayerNorm(enhanced_in), nn.Linear(enhanced_in, hidden), nn.GELU(), nn.Dropout(dropout)]
        layers.extend(ResidualDenseBlock(hidden, hidden * 2, dropout) for _ in range(3))
        layers.extend([nn.LayerNorm(hidden), nn.Linear(hidden, feat_dim), nn.GELU()])
        self.encoder = nn.Sequential(*layers)
        self.raw_skip = nn.Sequential(nn.LayerNorm(flat_dim), nn.Linear(flat_dim, feat_dim), nn.GELU())
        self.skip_alpha = nn.Parameter(torch.tensor(0.25))
        self.classifier = nn.Sequential(nn.LayerNorm(feat_dim), nn.Dropout(dropout), nn.Linear(feat_dim, 2))

    def forward(self, x, return_features: bool = False):
        band_mean = x.mean(dim=2)
        band_std = x.std(dim=2, unbiased=False)
        channel_mean = x.mean(dim=1)
        channel_std = x.std(dim=1, unbiased=False)
        flat = x.flatten(1)
        bg = self.band_gate(torch.cat([band_mean, band_std], dim=1))
        cg = self.channel_gate(torch.cat([channel_mean, channel_std], dim=1))
        bcg = self.band_channel_gate(flat).reshape(len(x), self.n_bands, self.n_channels)
        if self.ablation == "no_band_gate":
            bg = torch.zeros_like(bg)
        elif self.ablation == "no_channel_gate":
            cg = torch.zeros_like(cg)
        elif self.ablation == "no_joint_gate":
            bcg = torch.zeros_like(bcg)
        separable = x * (1.0 + 0.5 * bg.unsqueeze(2)) * (1.0 + 0.5 * cg.unsqueeze(1))
        joint = x * (1.0 + 0.5 * bcg)
        low_rank_map = torch.tanh(self.low_rank_band @ self.low_rank_channel.T)
        if self.ablation == "no_low_rank":
            low_rank_map = torch.zeros_like(low_rank_map)
        bilinear = x * (1.0 + 0.5 * low_rank_map.unsqueeze(0))
        rank_stats = self.rank_project(channel_mean @ self.low_rank_channel)
        band_centered = x - band_mean.unsqueeze(2)
        global_mean = flat.mean(dim=1, keepdim=True)
        global_std = flat.std(dim=1, unbiased=False, keepdim=True)
        feature = self.encoder(
            torch.cat(
                [
                    flat, separable.flatten(1), joint.flatten(1), bilinear.flatten(1),
                    band_centered.flatten(1), bg, cg, band_std, channel_std,
                    rank_stats, global_mean, global_std,
                ],
                dim=1,
            )
        )
        if self.ablation != "no_raw_skip":
            feature = feature + self.skip_alpha * self.raw_skip(flat)
        logits = self.classifier(feature)
        return (logits, feature) if return_features else logits


class BandChannelAdapter(nn.Module):
    def __init__(self, bands: int, channels: int):
        super().__init__()
        self.band_gamma = nn.Parameter(torch.zeros(bands))
        self.band_beta = nn.Parameter(torch.zeros(bands))
        self.channel_gamma = nn.Parameter(torch.zeros(channels))
        self.channel_beta = nn.Parameter(torch.zeros(channels))

    def forward(self, x):
        gamma = self.band_gamma[:, None] + self.channel_gamma[None, :]
        beta = self.band_beta[:, None] + self.channel_beta[None, :]
        return x * (1.0 + 0.1 * torch.tanh(gamma)) + 0.1 * torch.tanh(beta)


def softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


def gaussian_state(features: np.ndarray, labels: np.ndarray, shrink: float):
    pooled = np.var(features, axis=0) + 1e-4
    means, variances = [], []
    for cls in (0, 1):
        selected = features[labels == cls]
        if not len(selected):
            raise ValueError(f"calibration support has no samples for class {cls}")
        means.append(selected.mean(axis=0))
        local = np.var(selected, axis=0) + 1e-4
        variances.append(shrink * local + (1.0 - shrink) * pooled)
    return np.stack(means), np.stack(variances)


def gaussian_probability(feature, means, variances, temperature):
    score = -0.5 * np.mean(
        (feature[None, :] - means) ** 2 / variances + np.log(variances), axis=1
    )
    return softmax(score[None, :] / temperature)[0], score


def markov_filter(probabilities, transition_probability, emission_power=1.0):
    posterior = np.asarray([0.5, 0.5], dtype=np.float64)
    transition = np.asarray(
        [[1.0 - transition_probability, transition_probability],
         [transition_probability, 1.0 - transition_probability]]
    )
    outputs = []
    for emission in probabilities:
        prior = posterior @ transition
        likelihood = np.maximum(emission, 1e-6) ** emission_power
        posterior = prior * likelihood
        posterior /= posterior.sum()
        outputs.append(posterior.copy())
    return np.asarray(outputs)


def hysteresis_predictions(posteriors, threshold):
    state = int(posteriors[0].argmax())
    predictions = []
    for posterior in posteriors:
        opposite = 1 - state
        if posterior[opposite] >= threshold:
            state = opposite
        predictions.append(state)
    return np.asarray(predictions, dtype=np.int64)
