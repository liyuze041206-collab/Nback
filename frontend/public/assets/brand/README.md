# 品牌资源与生成记录

`team-logo.png` 来自 `已有材料/思维轨迹logo.bmp`，使用 System.Drawing 无损转换。尺寸保持 1358×1159；转换前后解码后的 RGBA 像素 SHA-256 完全一致，包含原透明度。网页使用 object-fit: contain，避免拉伸。

GSPM-Net 深浅版和 A/B/C/D 使用内置 image_gen 工具生成，透明 PNG；未使用 CLI，工具未提供具体模型版本号。没有覆盖原团队标志。深浅标志通过 CSS 根据主题切换，字母仅作为区域图像标识，模块名称与入口仍为可访问的 HTML。

| 文件 | 用途 |
| --- | --- |
| gspm-dark.png | 深色主题产品标志 |
| gspm-light.png | 浅色主题产品标志，与深色版保持同一构图 |
| letter-A-{dark,light}-v2.png | 完整推理流程，深浅主题各一张 |
| letter-B-{dark,light}-v2.png | 频谱–通道门控编码器，深浅主题各一张 |
| letter-C-{dark,light}-v2.png | 少样本原型适配，深浅主题各一张 |
| letter-D-{dark,light}-v2.png | 因果 Markov 滤波，深浅主题各一张 |

当前页面使用第二版字母素材，设计与生成提示词见 `LETTERS-V2.md`。下方保留第一版的生成记录；未使用的第一版图片已清理。

全部生成资源 alpha 范围为 0–255，背景透明。GSPM-Net 拼写及 A/B/C/D 均逐张目视核对，已检查网页中的缩小显示。

## 提示词原文

深色标志：

> Use case: logo-brand. Generate a production-ready transparent PNG wordmark for an EEG neural classification platform. Exact text: GSPM-Net (uppercase GSPM, hyphen, capital N lowercase et). Horizontal compact composition: a restrained thin neural-connection emblem to the left of the wordmark, medium-weight geometric sans serif typography. Deep cobalt and clear blue emblem with one subtle pale-gold connection; pale silver-blue wordmark readable on a very dark navy website. Minimal premium scientific identity, crisp clean contours, very mild glass highlights on the emblem, no glow cloud, no background, no panels, no slogans, no extra text. The whole mark should have narrow transparent padding and be readable at 220 by 55 CSS pixels. Deliver exactly one logo.

浅色标志，引用深色版作为编辑输入：

> Create the light-theme variant of this exact transparent GSPM-Net logo. Preserve the neural emblem, the exact letter shapes, spelling GSPM-Net, alignment, proportions, and transparent canvas. Change ONLY the wordmark lettering from pale silver-blue to deep navy/cobalt blue for excellent readability on white and pale gray. Keep the emblem blue and its small pale-gold accent. Reduce edge glow around lettering. Do not add a background or any new elements. Same visual identity and composition.

字母图像分别调用一次，`[LETTER]` 依次替换为 A、B、C、D：

> Use case: logo-brand. Generate a single capital Latin letter "[LETTER]" as a transparent PNG interface badge, no other characters. Consistent collection style: upright bold geometric sans-serif letter, shallow beveled clear blue glass body, cobalt-blue contour, tiny warm pale-gold specular highlight at upper left, restrained premium scientific aesthetic. Front-on view, centered with equal transparent padding, no platform, no shadow cloud, no frame, no particles, no background, no decorative objects. Exactly one readable letter "[LETTER]". Must remain unmistakable when reduced to 30px tall on both white and dark navy surfaces. Clean minimal glass, not elaborate 3D sculpture.


## 第二版字母徽章

网页当前使用 `letter-{A|B|C|D}-{dark|light}-v2.png` 的深浅两套独立透明素材。旧版字母保留为历史资源。新素材与完整生成提示词见 [LETTERS-V2.md](LETTERS-V2.md)。
