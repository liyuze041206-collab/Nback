"""Raw EEG import and feature extraction, adapted from the supplied N-back scripts."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
from scipy.io import loadmat
from scipy.signal import butter, filtfilt, iirnotch, resample_poly, sosfiltfilt, welch

CHANNELS = 'Fp1 Fz F3 F7 FT9 FC5 FC1 C3 T7 CP5 CP1 Pz P3 P7 O1 Oz O2 P4 P8 TP10 CP6 CP2 FCz C4 T8 FT10 FC6 FC2 F4 F8 Fp2 AF7 AF3 AFz F1 F5 FT7 FC3 C1 C5 TP7 CP3 P1 P5 PO7 PO3 POz PO4 PO8 P6 P2 CPz CP4 TP8 C6 C2 FC4 FT8 F6 AF8 AF4 F2'.split()
BANDS = [('theta', 4., 8.), ('alpha', 8., 13.), ('low_beta', 13., 20.), ('high_beta', 20., 30.), ('gamma', 30., 40.)]
FEATURE_CONFIG = dict(sfreq=250, low=1., high=40., notch=50., notch_q=30., order=4,
                      bad_channel_z=8., clip_z=10., epoch_sec=2., welch_sec=1., overlap_sec=.5,
                      log_epsilon=1e-8, unit='uV', bands=BANDS, channels=CHANNELS)
MAX_BYTES = 256 * 1024 * 1024


class InputError(ValueError):
    pass


def robust_sigma(x, axis=None, keepdims=False):
    med = np.median(x, axis=axis, keepdims=True)
    sigma = 1.4826 * np.median(np.abs(x - med), axis=axis, keepdims=True)
    if not keepdims:
        sigma = np.squeeze(sigma, axis=axis)
    return np.maximum(sigma, 1e-8)


def repair_bad_channels(data, threshold=8.):
    scales = robust_sigma(data, axis=1)
    median = np.median(scales)
    bad = np.where((scales > median * threshold) | (scales < median / threshold))[0].tolist()
    if not bad or len(bad) >= len(data) // 2:
        return data, []
    fixed = data.copy()
    good = [i for i in range(len(data)) if i not in bad]
    fixed[bad] = np.median(data[good], axis=0)
    return fixed, bad


def preprocess(data: np.ndarray, sfreq: float):
    data, bad = repair_bad_channels(data.astype(np.float64), 8.)
    if sfreq > 100:
        b, a = iirnotch(50., Q=30., fs=sfreq)
        data = filtfilt(b, a, data, axis=1)
    sos = butter(4, [1., 40.], btype='bandpass', fs=sfreq, output='sos')
    data = sosfiltfilt(sos, data, axis=1)
    med = np.median(data, axis=1, keepdims=True)
    sigma = robust_sigma(data, axis=1, keepdims=True)
    data = np.clip(data, med-10*sigma, med+10*sigma)
    data -= data.mean(axis=0, keepdims=True)
    if abs(sfreq-250.) > 1e-6:
        up, down = 250000, int(round(sfreq*1000))
        divisor = math.gcd(up, down)
        data = resample_poly(data, up//divisor, down//divisor, axis=1)
    return data.astype(np.float32), bad


def welch_features(data, starts, sfreq=250., epsilon=1e-8):
    features = []
    for start in starts:
        seg = data[:, start:start+int(2*sfreq)].astype(np.float32, copy=False)
        seg = seg-seg.mean(axis=1, keepdims=True)
        freqs, psd = welch(seg, fs=sfreq, window='hann', nperseg=int(sfreq),
                          noverlap=int(.5*sfreq), axis=1, detrend=False, scaling='density')
        power = np.zeros((5, 62), dtype=np.float32)
        for bi, (_, low, high) in enumerate(BANDS):
            power[bi] = psd[:, (freqs >= low) & (freqs < high)].mean(axis=1)
        features.append(np.log(power+epsilon))
    return np.asarray(features, dtype=np.float32)


def extract(data, meta, mode='continuous', event_types=None):
    if meta.get('prepared'):
        info = meta['prepared']
        starts = np.arange(len(info['onsets'])) * 500
        return welch_features(data.astype(np.float32), starts), np.asarray(info['onsets']), {
            'windows': len(starts), 'window_mode': 'prepared_events',
            'preprocessing': 'already_preprocessed_no_repeat', 'bad_channels_repaired': [],
            'source': info['source_id']}
    if any(0 < t < meta['duration_sec'] for t in meta.get('boundaries', [])):
        raise InputError('记录中含内部断点，请先按连续片段拆分后导入，避免滤波跨越断点。')
    processed, bad = preprocess(data, meta['sfreq'])
    if mode == 'events':
        events = meta.get('events', [])
        if event_types:
            events = [e for e in events if e['type'] in event_types]
        if not events:
            raise InputError('未找到匹配的刺激事件，请检查事件类型或选择连续分窗。')
        onsets = np.asarray([e['onset_sec'] for e in events], dtype=float)
        if not np.isfinite(onsets).all() or np.any(onsets < 0):
            raise InputError('事件时间必须为非负有限数值。')
        starts = np.unique(np.rint(onsets*250).astype(int))
        invalid = (starts+500 > processed.shape[1])
        if np.any(invalid):
            raise InputError(f'{int(invalid.sum())} 个事件之后不足 2 秒，请修正事件或裁剪记录。')
    elif mode == 'continuous':
        starts = np.arange(0, processed.shape[1]-499, 500, dtype=int)
    else:
        raise InputError('未知分窗方式。')
    if not len(starts):
        raise InputError('有效 EEG 长度不足一个 2 秒窗口。')
    return welch_features(processed, starts), starts/250., {'bad_channels_repaired':[CHANNELS[i] for i in bad], 'windows':len(starts), 'window_mode':mode, 'discarded_tail_sec':round((processed.shape[1] % 500)/250., 3) if mode == 'continuous' else 0, 'preprocessing':'offline_zero_phase'}


def _scalar(value):
    return np.asarray(value).item()


def parse_recording(files: dict[str, bytes], sfreq=None, unit=None, manual_onsets=None):
    if not files or sum(map(len, files.values())) > MAX_BYTES:
        raise InputError('请选择文件；单次总大小不能超过 256 MB。')
    mains = [n for n in files if Path(n).suffix.lower() in {'.csv','.npz','.set'}]
    if len(mains) != 1:
        if all(Path(n).suffix.lower() == '.fdt' for n in files):
            raise InputError('FDT 不能单独读取；请同时选择同一记录的 SET 和 FDT 文件。')
        raise InputError('每次导入一个 CSV、NPZ 或 SET 文件；SET 可附带对应 FDT。')
    name = mains[0]
    extension = Path(name).suffix.lower()
    events = []
    prepared = None
    boundaries = []
    if extension != '.set' and len(files) != 1:
        raise InputError('CSV / NPZ 请单独导入。')
    try:
        if extension == '.csv':
            rows = list(csv.reader(io.StringIO(files[name].decode('utf-8-sig'))))
            if len(rows) < 2:
                raise InputError('CSV 没有数据行。')
            header = [v.strip() for v in rows[0]]
            array = np.asarray(rows[1:], dtype=np.float64)
            if array.ndim != 2 or array.shape[1] != len(header):
                raise InputError('CSV 列数不一致。')
            if 'time_s' in header:
                times = array[:, header.index('time_s')]
                if sfreq and (not np.isfinite(times).all() or not np.allclose(np.diff(times), 1/float(sfreq), atol=1e-5, rtol=1e-3)):
                    raise InputError('time_s 间隔与填写的采样率不一致。')
                array = np.delete(array, header.index('time_s'), axis=1)
                header.remove('time_s')
            data, channels = array.T, header
        elif extension == '.npz':
            with zipfile.ZipFile(io.BytesIO(files[name])) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
                    raise InputError('NPZ 解压后超过大小限制。')
            with np.load(io.BytesIO(files[name]), allow_pickle=False) as z:
                if not {'eeg','sfreq','ch_names','unit'}.issubset(z.files):
                    raise InputError('NPZ 必须包含 eeg、sfreq、ch_names、unit；eeg 形状为 [采样点, 通道]。')
                data = np.asarray(z['eeg'], dtype=np.float64).T
                channels = z['ch_names'].astype(str).tolist()
                sfreq, unit = float(_scalar(z['sfreq'])), str(_scalar(z['unit']))
                if 'processing_stage' in z.files:
                    if str(_scalar(z['processing_stage'])) != 'gspm_preprocessed_epochs_v1':
                        raise InputError('未知预处理数据格式，不能作为原始 EEG 再次处理。')
                    prepared = json.loads(str(_scalar(z['prepared_manifest'])))
                    if not isinstance(prepared, dict):
                        raise InputError('预处理清单格式无效。')
                    if prepared.get('feature_config') != json.loads(json.dumps(FEATURE_CONFIG)):
                        raise InputError('预处理数据的参数或通道顺序与当前模型不一致。')
                    onsets = np.asarray(prepared.get('onsets', []), dtype=float)
                    if (sfreq != 250 or unit != 'uV' or channels != CHANNELS or
                        onsets.ndim != 1 or not len(onsets) or not np.isfinite(onsets).all() or
                        np.any(onsets < 0) or np.any(np.diff(onsets) <= 0) or
                        data.shape != (62, len(onsets)*500)):
                        raise InputError('预处理窗口的采样率、尺寸、单位、通道或时间清单无效。')
                    if (prepared.get('role') not in {'query','support0','support2'} or
                        not all(isinstance(prepared.get(k), str) and prepared[k] for k in ('subject','session','source_id'))):
                        raise InputError('预处理数据缺少被试、session、来源或用途。')
                if 'event_onsets' in z.files:
                    events = [{'onset_sec':float(t),'type':'stimulus'} for t in z['event_onsets']]
        else:
            mat = loadmat(io.BytesIO(files[name]), simplify_cells=True)
            mat = mat.get('EEG', mat)
            if int(mat.get('trials',1)) != 1:
                raise InputError('当前支持连续 EEGLAB 记录（trials=1），请先导出连续记录。')
            sfreq = float(mat['srate'])
            locations = mat['chanlocs']
            if isinstance(locations, dict): locations = [locations]
            channels = [str(ch['labels']) for ch in locations]
            nbchan, pnts = int(mat['nbchan']), int(mat['pnts'])
            raw = mat.get('data')
            if isinstance(raw, np.ndarray):
                data = np.asarray(raw, dtype=np.float64)
            else:
                reference = mat.get('datfile', raw)
                if np.asarray(reference).size == 0 or str(reference) in {'', '[]'}: reference = raw
                referenced = str(reference).replace('\\','/').split('/')[-1]
                matches = [v for k,v in files.items() if k.lower()==referenced.lower()]
                if len(matches) != 1:
                    raise InputError(f'SET 需要对应的 {referenced}，请一起选择上传。')
                if len(matches[0]) != nbchan*pnts*4:
                    raise InputError('FDT 大小与 SET 中的通道数、采样点数不一致。')
                data = np.frombuffer(matches[0], dtype='<f4').reshape(pnts, nbchan).T.astype(np.float64)
            if data.shape != (nbchan,pnts):
                raise InputError('SET 数组与 nbchan / pnts 元数据不一致。')
            raw_events = mat.get('event', [])
            if isinstance(raw_events, dict): raw_events=[raw_events]
            for event in raw_events:
                if isinstance(event,dict) and 'latency' in event:
                    if str(event.get('type','')).lower() == 'boundary':
                        onset = (float(event['latency'])-1)/sfreq
                        if not np.isfinite(onset) or not -.5/sfreq <= onset <= pnts/sfreq:
                            raise InputError('记录边界位置无效。')
                        boundaries.append(max(0., onset))
                        continue
                    events.append({'onset_sec':(float(event['latency'])-1)/sfreq,'type':str(event.get('type','stimulus'))})
    except InputError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError, OSError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise InputError(f'文件格式无法读取，请核对模板或 EEGLAB 导出格式：{type(exc).__name__}') from exc
    if not sfreq or not np.isfinite(float(sfreq)) or not 250 <= float(sfreq) <= 5000:
        raise InputError('需要有效采样率（250–5000 Hz）；本模型不对低采样率记录自动升采样。')
    if unit not in {'uV','mV','V'}:
        raise InputError('请选择明确的信号单位：uV、mV 或 V。')
    if not isinstance(channels,list) or len(set(channels)) != len(channels):
        raise InputError('通道名称缺失或重复。')
    if data.ndim != 2 or data.shape[0] != len(channels):
        raise InputError('通道数量与 EEG 数组形状不匹配；NPZ 必须按 [采样点, 通道] 排列。')
    missing = [ch for ch in CHANNELS if ch not in channels]
    if missing:
        raise InputError('缺少模型必需通道：'+', '.join(missing))
    if not np.isfinite(data).all():
        raise InputError('EEG 含 NaN 或无穷值，请先修正。')
    data = data[[channels.index(ch) for ch in CHANNELS]] * {'uV':1.,'mV':1000.,'V':1e6}[unit]
    if data.shape[1] < math.ceil(2*float(sfreq)):
        raise InputError('记录不足 2 秒。')
    if manual_onsets is not None:
        if prepared:
            raise InputError('预处理包已包含固定刺激窗口，不能额外覆盖事件时间。')
        events = [{'onset_sec':float(t),'type':'stimulus'} for t in manual_onsets]
    if any(not np.isfinite(e['onset_sec']) or e['onset_sec']<0 or e['onset_sec']>=data.shape[1]/float(sfreq) for e in events):
        raise InputError('事件时间必须位于记录范围内。')
    fingerprint = hashlib.sha256(np.ascontiguousarray(data,dtype='<f8').tobytes()+str(float(sfreq)).encode()).hexdigest()
    return data, dict(name=name, sfreq=float(sfreq), unit='uV', original_unit=unit, channels=CHANNELS,
        samples=data.shape[1], duration_sec=data.shape[1]/float(sfreq), events=events,
        event_types=sorted(set(e['type'] for e in events)), fingerprint=fingerprint,
        prepared=prepared, boundaries=boundaries,
        extra_channels_ignored=[ch for ch in channels if ch not in CHANNELS])


def preview(data, sfreq):
    # Bin means provide a compact overview; not a diagnostic waveform.
    n=min(600,data.shape[1]); bounds=np.linspace(0,data.shape[1],n+1,dtype=int)
    rows=[]
    picks=[CHANNELS.index(v) for v in ['Fp1','Fz','C3','Pz','O1']]
    for i in range(n):
        a,b=bounds[i:i+2]
        rows.append({'t':round(a/sfreq,4),**{CHANNELS[k]:round(float(data[k,a:b].mean()),4) for k in picks}})
    return rows
