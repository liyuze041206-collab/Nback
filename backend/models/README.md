# 26 折模型及接入规则

已从用户提供的“代码和权重”包复制 26 个 MATB-II 自监督 Enhanced GSC 检查点到 `folds/sub-XX/`。每个子目录有原权重 `encoder.pt` 和独立 `manifest.json`。原包未修改，复制后的 SHA-256 检查记录见 `docs/model-import-validation.json`。

这些权重采用 LOSO 协议。`sub-01` 对应的模型训练时排除 `sub-01` 与独立验证被试 `sub-02`；其他折依次对应各自目标/验证被试。后端核对权重中的目标、验证、来源及隔离标志，严格加载全部 59 个参数张量，不会把任意一折说成覆盖全部被试的通用模型。校准档案绑定模型折、权重文件与 manifest 共同计算的版本哈希。

网站的“建立新校准档案”里选择模型折。用户 ID 使用原论文的 `sub-XX` 时必须选择同名留出折，否则返回错误。全新用户可明确选择一折试用，但不具备与论文 26 折成绩相同的验证依据。真实分类还需要同一用户/session 的 0-back 与 2-back 标注支持信号及独立查询信号；当前没有实际 EEG 文件。

每折 `temporal` 参数来自新权重包的 `13_Nback_24epoch/fold_metrics.csv`，字段是该折独立验证被试选出的 EMA、Markov 和滞回参数，不从目标查询标签选择。manifest 中 `validation_source` 写明来源及验证被试。若未来更换检查点或参数，版本哈希会变化，已有档案将要求重新校准。

`manifest.example.json` 保留用于新权重的手工接入模板。若没有 `folds/`，系统也支持单模型 `encoder.pt` + `manifest.json`，缺失时真实分析返回 409。PyTorch 以 `weights_only=True` 加载检查点。网站不重新训练模型，演示数据不用于实际模型评估。
