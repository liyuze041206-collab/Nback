"""Package the existing offline split for upload; never filter the EEG again."""
from pathlib import Path
import csv
import hashlib
import json
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from gspm.signal import CHANNELS, FEATURE_CONFIG, parse_recording, extract


def main():
    base = ROOT.parent / '真实数据测试'
    destination = base / '网页上传数据'
    rows = list(csv.DictReader((base/'features/window_manifest.csv').open(encoding='utf-8-sig')))
    with np.load(base/'features/all_windows.npz', allow_pickle=False) as archive:
        reference = archive['X'].copy()
    groups = {}
    for row in rows:
        groups.setdefault((row['subject'],row['session'],row['task'],row['split']), []).append(row)
    audit = []
    for (subject,session,task,split), windows in sorted(groups.items()):
        windows.sort(key=lambda row: int(row['start_sample_250hz']))
        source = base/'preprocessed'/f'{subject}_{session}_{task}.npz'
        with np.load(source, allow_pickle=False) as archive:
            data = archive['data']
            eeg = np.concatenate([data[:,int(row['start_sample_250hz']):int(row['start_sample_250hz'])+500] for row in windows], axis=1).T
        provenance = json.loads(source.with_suffix('.json').read_text(encoding='utf-8'))
        role = 'query' if split=='query' else 'support'+task[0]
        metadata = dict(subject=subject, session=session, role=role,
            source_id=provenance['fingerprint']['fdt_sha256'],
            onsets=[int(row['start_sample_250hz'])/250 for row in windows],
            feature_config=FEATURE_CONFIG, amplitude_convention='native_EEGLAB_no_rescaling')
        folder = destination/subject/session
        folder.mkdir(parents=True, exist_ok=True)
        name = f'{subject}_{session}_{"查询" if split=="query" else "支持"}_{task}.npz'
        target = folder/name
        np.savez_compressed(target, eeg=eeg, sfreq=250., ch_names=np.asarray(CHANNELS), unit='uV',
            processing_stage='gspm_preprocessed_epochs_v1',
            prepared_manifest=json.dumps(metadata,ensure_ascii=False))
        loaded, meta = parse_recording({name:target.read_bytes()})
        features,times,_ = extract(loaded, meta)
        expected = reference[[int(row['sample_index']) for row in windows]]
        np.testing.assert_allclose(features,expected,rtol=0,atol=0)
        audit.append(dict(file=str(target.relative_to(destination)),windows=len(windows),
            sha256=hashlib.sha256(target.read_bytes()).hexdigest(),feature_max_error=0))
    (destination/'导出校验.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'使用说明.md').write_text('''# 网页上传数据

1. 重新启动 code/start.bat，打开 http://127.0.0.1:8000/demo。
2. 在“上传 EEG”中选择一个被试/session 文件夹内的“查询_0-back.npz”或“查询_2-back.npz”（文件名含被试/session 前缀）。
3. 等待显示“sub-XX · ses-SX · 已自动匹配”，再点击“开始分析”。概率由真实权重计算，计算完成后播放动画。
4. 后台自动选择目标被试的留出折，并读取同一被试/session 的两类支持文件；不需要手动上传支持或选择模型。

默认本机支持目录为本文件夹。若移动目录，请启动服务前设置 GSPM_SUPPORT_DIR 指向新的“网页上传数据”根目录，保留 sub-XX/ses-SX 子目录与两类支持文件。已有兼容校准会优先复用；没有档案或支持文件时停止并提示。

用户只需提交一个查询 NPZ，不要解压，不要选择原始 FDT。这些文件已经完成预处理，网站只提取 Welch 特征并推理，不重复滤波。原始 SET/FDT、CSV 和普通 NPZ 请使用 /lab。
三次 session 的支持数依次为每类 7、7、6，共每类 20；程序从文件读取，不使用网页普通上传的每类 20 设置。
每份查询对应一份原始任务记录，保留原始刺激时间；查询与支持窗口独立。查询文件名中的 0-back / 2-back 是真实任务标签，不参与模型预测或更新。
本文件夹使用已有离线结果的划分，不重新训练。每个导出包的 Welch 特征已逐项对照原 features/all_windows.npz，最大误差为 0。
原始 preprocessed/*.npz 是中间产物，请使用这里的网页专用包。
''',encoding='utf-8')
    print(json.dumps({'files':len(audit),'windows':sum(x['windows'] for x in audit),'max_feature_error':0,'directory':str(destination)},ensure_ascii=False))


if __name__=='__main__':
    main()
