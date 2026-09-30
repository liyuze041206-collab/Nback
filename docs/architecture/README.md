# GSPM-Net 网络架构介绍

双击 `start.bat` 启动，或在现有服务运行时打开 http://127.0.0.1:8000/architecture.html 。

- 首页按 `整体架构.pptx` 的 A / B / C / D 组织，全图为 HTML / CSS / SVG 元素，没有嵌入架构截图。
- 点击节点进入 16 个模块详情。浏览器前进后退、模块导航、Escape 返回总览均可使用。
- `EEG 分类` 进入原有 `/demo`；分类页新增 `网络架构` 返回入口。原有 `/lab` 导航也可进入架构页。
- 页面动效可暂停，并自动遵循系统减少动态效果设置。手机采用换行布局。
- 说明依据项目原版论文 § III B–E、图 1–2；示意波形不是实测预测。每类 20 个支持窗口按 7 / 7 / 6 分配至三个 session。

源文件为 `gspm_network_atlas.html`，也可以直接用浏览器离线打开。直接打开不启动 EEG 分析服务，实际分类请使用启动入口。

维护：`frontend` 中执行 `npm run build` 会先同步架构页到 `public/architecture.html`，再输出至 `dist`。`node qa-atlas.mjs` 在本机服务上检查 16 个模块、双向跳转、键盘操作、响应式布局、减少动效和离线打开，截图保存在本目录。

旧版页面已归档至 `archive/legacy-pages/`，当前平台不依赖这些文件。

