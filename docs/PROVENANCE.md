# 来源与实现边界

原始材料保持不变。新权重来自 `已有材料/代码和权重/outputs/37_MATB跨任务自监督预训练/12_MATB_SSL_24epoch/checkpoints/`；26 折各自的时序参数来自同包 `13_Nback_24epoch/fold_metrics.csv`。新权重的目标/验证被试、排除标志与配置均经过核对。以下位置均相对于用户提供的研究代码目录：

| 网站实现 | 研究来源 |
| --- | --- |
| backend/gspm/network.py | outputs/30_自监督增强Gated_原型DOSA/01_代码/ssl_common.py，除文件头来源注释外保留原文 |
| signal.py 的坏导检查、50 Hz 陷波、1–40 Hz 滤波、幅值裁剪、平均参考、重采样 | 同目录 01_preprocess_raw_eeg.py |
| signal.py 的 Welch log-PSD | outputs/03_脚本与工具/01_训练脚本/train_cog_nback_0v2_welch_center_loso.py |
| 支持集标准化、EMA 预热、时序决策 | outputs/37_MATB跨任务自监督预训练/01_代码/03_evaluate_nback_fewshot_gspm.py |
| 原型概率与伪标签更新 | outputs/30_自监督增强Gated_原型DOSA/01_代码/ssl_gated_source_pretrain.py 的最终协议配置分支 |
| 默认配置 | configs/当前论文协议/03_Nback最终评估参数.json 与 04_Nback预处理参数.json |
| 页面实验数据与图 | 用户的原版论文及“画图”目录中的 Kshot_sensitivity、Temporal_strategy_comparison、SSL_objective_comparison 等 |

网站使用 5×62 自然对数 PSD（N-back 原代码 epsilon=1e-8），冻结编码器产生 96 维特征。高斯原型方差收缩为 0.35、概率温度 0.7、伪标签更新率 0.05。最终协议的 correction_mode=none、adapter_lr=0、stable_weight=0、use_reliability_gate=false，因此网站抽取该分支实现，不运行旧实验中未启用的适配器训练与可靠性门控。

保留 classifier 参数以严格兼容原检查点，但其 logits 不作为网站最终分类；最终分类来自高斯原型。重建头、VICReg 不属于目标推理。复制的 network.py 中旧训练工具函数不用于公开上传路径，上传 NPZ 禁止 pickle。

本网站新增文件校验、通道重排、单位转换、前后端界面、本机任务管理、不可变校准基线、权重版本绑定和模式隔离。研究中的绝对路径和输出目录不是运行依赖。

重要边界：原预处理使用整条记录的双向滤波及稳健统计，因此改变未来原始采样可能改变此前预处理输出。本次“因果一致性”指特征窗口既定之后的原型更新与时序递推，不把整条离线预处理声称为实时因果系统。当前记录内的时序后验不跨分析任务继承。

同一个 session 的不同文件可建立支持与查询关系，不能证明实际采集时间先后；页面与说明要求用户提供独立支持、查询片段。应用每类 20 个支持窗口并不等价于论文跨 session 的 7/7/6 协议。原论文的稳定性提升也不能证明真实连续负荷切换性能。

新包中不存在单一覆盖全部目标被试的权重；网站已逐折接入。固定输入测试对齐原研究实现，同时完成随机合成波形上的真实检查点推理链路。实际被试信号和原协议的端到端效果仍需单独验证。
