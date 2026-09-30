# EEG 文件格式

本目录的 CSV 和 NPZ 是同一段 **8 秒合成信号**，500 Hz、62 通道、μV。仅验证导入与数据格式；不是 COG-BCI 数据，不含可靠的认知负荷标签，不能用于建立有意义的个人原型。

## CSV

UTF-8 编码，第一行为通道名称，每行是一个采样点。必需的 62 个通道名称见模板；顺序可以变化，后端按名称重排。可有 `time_s` 列，此列步长必须与填写的采样率一致。所有其他列必须是数值。导入时明确填写采样率和单位，不从波形猜测。

## NPZ

使用 `numpy.savez_compressed`，禁止 object / pickle 数组：

```python
np.savez_compressed('my_eeg.npz',
    eeg=data,                 # [采样点, 通道]，原始、尚未预处理的数据
    sfreq=np.array(500.),      # Hz
    ch_names=np.array(names), # Unicode 字符串数组，长度与通道数一致
    unit=np.array('uV'),       # uV / mV / V
    event_onsets=np.array([0., 2., 4.]))  # 可选，刺激出现时间，单位秒
```

NPZ 的采样率和单位以文件元数据为准。可选事件的类型统一为 `stimulus`。不接受研究脚本导出的、使用 object 数组的预处理缓存；那种缓存已经滤波，不能再次当原始 EEG 导入。查询文件中额外的 `labels` 等字段不会读取或用于预测。

## EEGLAB SET / FDT

支持项目使用的 MATLAB v5/v7 SET 元数据、连续记录（trials=1），以及配对的小端 float32 FDT，按 `[采样点, 通道]` 存储。也支持 SET 内嵌 EEG 数组。必须同时选择 SET 引用的 FDT；只使用文件名配对，不访问 SET 中写入的任意本机路径。MATLAB v7.3 / HDF5 SET 暂不支持，请在 EEGLAB 中另存为 v7 格式。SET 读取采样率、通道和事件，单位仍需明确填写。

事件 `latency` 按 EEGLAB 1-based 采样点转换为秒。手动填写的秒数数组会覆盖文件事件。选择事件分窗时，事件后必须有完整 2 秒信号。没有事件可以选择连续 2 秒分窗，末尾不足 2 秒的片段会丢弃并记录在处理摘要中。

采样率范围 250–5000 Hz，至少 2 秒，总上传大小最多 256 MB。缺少必需通道、通道重名、NaN / Infinity、FDT 大小错误都会拒绝导入；额外辅助通道会在摘要中说明，不能代替缺失通道。
