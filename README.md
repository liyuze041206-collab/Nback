# GSPM-Net 本地脑电研究工作台

网络交互讲解与 0-back / 2-back EEG 分析，React + TypeScript + Three.js + Recharts 前端，FastAPI + PyTorch + NumPy + SciPy 后端。只监听本机，运行时不需要外部账号、API 或云服务。

## 快速启动

环境：Windows、Python 3.11–3.13、Node.js 22.13 或更新版本。

1. 首次运行双击 **install.bat**。安装依赖需要网络，Python 依赖装入本项目 `.venv`，不会修改全局 Python 配置。前端依据 package-lock 安装并构建。
2. 双击唯一启动入口 **start.bat**，打开网络原理首页，在右上角设置菜单切换到 EEG 分类。分析页面切走后保留运行状态。服务地址为 **http://127.0.0.1:8000/portal.html**。网络原理与 EEG 分类也可通过 右上角设置菜单中的 **网络原理** / **EEG 分类** 互相切换。
3. 停止时在启动窗口按 **Ctrl+C**。再次启动可读取此前保存在本机的记录与校准档案；服务中断的未完成任务会标记失败，可重新分析。

GitHub 仓库包含 26 折模型权重与对应独立验证时序参数，下载到新电脑后先运行 install.bat，再运行 start.bat。当前本机环境已安装，可直接启动。`.venv`、`node_modules` 和前端构建产物不上传，install.bat 会安装依赖并构建。若端口 8000 已被占用，启动脚本会提示，不会结束其他程序。

## GitHub 下载与一键上传

仓库：[liyuze041206-collab/Nback](https://github.com/liyuze041206-collab/Nback)。项目直接位于仓库根目录，权重位于 `backend/models/folds/sub-XX/encoder.pt`，每折配置为同目录 `manifest.json`。

推荐使用 Git 克隆（Git for Windows）：

```powershell
git clone https://github.com/liyuze041206-collab/Nback.git
cd Nback
install.bat
start.bat
```

双击 **update_github.bat** 会自动构建网站、提交本地修改、同步远端 main 并上传，然后核对远端提交编号。默认提交说明包含当前时间；也可以运行 `update_github.bat -Message "修改说明"`。需要 Git for Windows 和已登录的 GitHub 凭据；首次 Git 推送如提示登录，按 Git Credential Manager 的浏览器登录完成即可。脚本不保存密码或令牌，也不强制覆盖远端历史。发生远端合并冲突时会恢复本地提交并显示提示。

`runtime` 中的脑电上传记录、校准及分析结果、本地 Python/Node 环境、缓存与 archive 备份不上传。26 个模型文件和配置会一起提交。下载 ZIP 也可以安装启动，但 ZIP 不含 Git 历史，一键上传需使用 Git 克隆的目录。只改页面时也应完成正常构建再上传。

## 网络探索

平台的 **网络原理** 视图保留四区布局：A 完整推理流程、B 频谱–通道门控编码器、C 少样本原型适配、D 因果 Markov 滤波。架构用 HTML/SVG 元素重构，点击模块可查看 16 个详情，论文依据和必要的数据说明保留。独立 `/architecture.html` 与离线 `gspm_network_atlas.html` 也可使用。

右上角圆形设置按钮统一提供“网络原理 / EEG 分类”、动效开关和深浅主题。设置保存到 `gspm.preferences.v1`，切换页面与刷新后保持一致；首次动效默认遵循系统减少动态效果设置。关闭动效后网络与波形静止，模型分析和逐窗结果继续运行；Pause 是单独的手动播放暂停。切换视图保留文件、详情、分析任务和当前窗口。

新品牌标志与字母素材位于 `assets/brand`，生成工具和完整提示词见该目录 README。团队 BMP 无损转 PNG，保留原透明度。两个页面底部统一署名“思维轨迹出品”。

## 演示与真实分析

平台的 **EEG 分类** 视图对应 `/demo`，默认提供两个明确标注的确定性合成 EEG 示例；兼容的独立 `/demo` 地址也保留。点击 Start Analysis 后，后端先用选定的真实 GSPM-Net 检查点完成支持集校准、原型分类和时序递推，再把概率返回给页面；页面只在收到模型结果后播放对应窗口的结构动画。示例不是实测 EEG，也不能用于宣称准确率。上传模式会进入同一真实推理链路，并要求独立的 0-back、2-back 支持记录和查询记录。

React 工作台的 `/lab` 保留独立的本地 EEG 管理、校准、分析和导出流程；它的演示卡片仍是用于说明界面操作的预设演示，不等同于 `/demo` 的真实权重示例。

真实链路已接入 **26 折 MATB-SSL 检查点**，并按目标被试选择留出折。已完成 137 个窗口的实际 NPZ 上传链路检查；这项检查不等于重新复现论文汇总准确率。接入与折选择说明见 `backend/models/README.md`。

接入实际模型后：

1. 切换“本地 EEG”，导入 CSV、NPZ 或 SET + FDT；检查采样率、单位、记录长度及通道。模板及字段说明见 `examples/README.md`。
2. 导入同一用户、同一 session 的两段已标注支持记录，分别对应 0-back 与 2-back。建立个人校准档案时选择这两段记录及目标被试对应的检查点。若用户编号为论文被试 `sub-XX`，只能选同名留出折；其他新用户可以明确指定一折试用，不适用论文汇总成绩。应用默认每类前 20 个有效 2 秒窗口（至少 40 秒有效窗口内容），可调至 2–100；不足时直接提示。
3. 另行导入**不与支持样本重叠**的查询记录。选择档案后用户/session 自动填入。服务会阻止复用相同记录或内容完全相同的信号，但无法识别任意裁剪或重编码后的部分重叠，也无法从 EEG 本身鉴定用户身份；需保证标签和记录归属正确。
4. 选择连续分窗或刺激事件分窗，校准和查询的方式必须一致。事件类型可筛选，手动事件数组会覆盖原事件。
5. 点击开始分析，查看进度、逐窗概率、状态时间线及处理摘要，导出 CSV / JSON。新权重包中各折独立验证选出的时序参数已经配置；若单独替换为没有时序参数的模型，则仅输出原型分类，并显示“时序模块未启用”。

校准的标准化统计量和高斯原型只来自支持集。每次分析从保存的校准基线复制状态，运行中的伪标签原型更新不写回校准档案。查询真值不参与预测、更新或归一化。每个任务按时间排序处理查询窗口。

应用设置“当前 session 每类 20 个”与论文“每类总计 20 个、跨三个 session 按 7/7/6 分配”不同，不能直接引用论文成绩。87.72% ± 11.03% 是原论文报告的被试间均值与标准差，本网站没有重新训练或复现该成绩。两类输出是模型概率，未经过临床置信度校准。

## 本地数据与删除

记录、档案、任务结果保存在 `runtime/recordings`、`runtime/calibrations`、`runtime/analyses`。接口仅接收生成的记录 ID，不接受任意本机路径。上传后保存对齐并换算为 μV 的数值和元数据，不额外保留原上传文件副本。

界面中记录、校准档案和历史结果都可删除。支持记录被档案引用时先删档案，正在处理的对象需等待任务完成。删除查询记录不会自动删掉既有分析结果，需要在历史记录中分别删除。服务默认仅监听 127.0.0.1，拒绝非本机 Origin；无需账号。

## 源码结构与开发

| 目录 | 内容 |
| --- | --- |
| frontend/src | 首页、三维场景、分析工作台、接口与演示数据 |
| frontend/components/ui | Sites React 基础中的 shadcn 组件 |
| frontend/public/research | 用户提供的原研究图 |
| backend/gspm | 原网络、输入预处理、校准与推理 |
| backend/app.py | 本地 API、任务与档案管理、静态网页服务 |
| backend/models/folds | 26 折检查点及各折配置；原权重包保留不动 |
| backend/tests | 文件校验、模型状态、隔离和因果递推测试 |
| examples | 可下载的合成 CSV / NPZ 模板 |
| scripts | Windows 安装启动、单页演示启动及原研究数值核对 |
| docs | 来源、验收记录、页面截图 |
| archive/legacy-pages | 已归档的旧版独立网页与原始备份 |

前端基于 Sites 本地 React 基础整理为 Vite SPA，无需部署 Sites 或 Cloudflare。开发调试时，在 backend 目录运行 `..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000`，另在 frontend 目录运行 `npm run dev`。5173 前端代理 `/api` 与 `/demo` 到 8000；门户 HTML、架构图和共享主题由 `frontend/sync-atlas.mjs` 同步至构建目录。

在项目根目录运行 `.venv\Scripts\python.exe -m pytest -q`；在 frontend 目录运行 `npm run build`。浏览器测试使用系统 Edge：在 frontend 目录执行 `node qa.mjs` 检查 React 工作台；执行 `node qa-atlas.mjs` 检查架构图，执行 `node qa-portal.mjs` 检查统一门户、切换和文件选择保留；执行 `node qa-brand.mjs` 检查两种主题、三种宽度、全部模块详情、设置保留、粒子及动效状态；执行 `node qa-prepared-upload.mjs` 验证真实上传推理。浏览器检查需对应页面服务已经启动。可选原研究核对：`.venv\Scripts\python.exe scripts\verify_research.py <研究代码目录>`，需要原研究环境的 pandas / scikit-learn，仅供数值对照，不是运行网站的依赖。

接口文档：http://127.0.0.1:8000/docs 。主要接口包括 GET status、GET demo/examples、POST/GET demo/analyses，以及 POST/GET/DELETE recordings、POST/GET/DELETE calibrations、POST/GET/DELETE analyses 和分析结果 export。CSV 导出为 UTF-8 BOM，JSON 包含模型版本、校准记录引用和处理摘要。

研究来源与限制见 `docs/PROVENANCE.md`，实际验收记录见 `docs/VALIDATION.md`。

