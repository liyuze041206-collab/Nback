"""Deterministic synthetic EEG examples for the standalone HTML demo."""
from __future__ import annotations

import numpy as np

from .signal import CHANNELS


def synthetic_record(kind: str, role: str, seed: int) -> tuple[np.ndarray, dict]:
    if kind not in {"steady", "changing"}:
        raise ValueError("未知演示示例")
    rng = np.random.default_rng(seed)
    sfreq = 500.0
    seconds = 30.0 if role == "query" else 50.0
    n = int(seconds * sfreq)
    t = np.arange(n, dtype=np.float64) / sfreq
    data = np.zeros((62, n), dtype=np.float64)
    spatial = np.linspace(0.55, 1.35, 62)
    phase = np.linspace(0, np.pi * 1.7, 62)
    for i in range(62):
        if role == "support0":
            base = 7.2 + (i % 3) * 0.3
            amp = 2.0
        elif role == "support2":
            base = 16.0 + (i % 4) * 0.25
            amp = 3.2
        elif kind == "steady":
            base = 9.5 + (i % 4) * 0.2
            amp = 2.5
        else:
            base = 8.0 + 5.0 * np.sin(2 * np.pi * t / 30.0) + (i % 3) * .2
            amp = 2.2 + 1.0 * (t > 10) + 1.2 * (t > 20)
        signal = (np.sin(2 * np.pi * base * t + phase[i]) * spatial[i] * amp)
        signal += .65 * np.sin(2 * np.pi * (base / 2.0) * t + phase[i] / 2)
        signal += rng.normal(0, .8, n)
        data[i] = signal
    data -= data.mean(axis=0, keepdims=True)
    return data, {"sfreq": sfreq, "unit": "uV", "channels": CHANNELS, "duration_sec": seconds, "name": f"演示-{kind}-{role}"}


def demo_catalog():
    return [
        {"id": "steady", "name": "示例一 · 平稳信号", "description": "频谱结构保持稳定，适合观察连续窗口输出。", "duration_sec": 30, "sfreq": 500, "synthetic": True},
        {"id": "changing", "name": "示例二 · 变化信号", "description": "频带组成随时间变化，适合观察概率与状态变化。", "duration_sec": 30, "sfreq": 500, "synthetic": True},
    ]


def window_wave(data: np.ndarray, channels: int = 7, sfreq: float = 500.0):
    """Return representative channels grouped into the model's two-second windows."""
    samples = int(round(float(sfreq) * 2))
    if samples < 2:
        return []
    count = data.shape[1] // samples
    return data[:channels, :count * samples].reshape(channels, count, samples).transpose(1, 0, 2).tolist()
