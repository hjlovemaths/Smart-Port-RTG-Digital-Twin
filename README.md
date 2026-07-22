# Smart Port RTG Digital Twin

智慧港口 RTG（轮胎式集装箱门式起重机）数字孪生项目，使用 Blender 维护视觉源模型，并以 OpenUSD/Omniverse 组织场景、环境和后续 SimReady 逻辑。

## 快速开始

1. 在 Omniverse 中打开 `omniverse/scenes/smart_port.usda`。
2. 场景通过 payload 加载 `omniverse/assets/rtg/RTG_Model.usdc`。
3. 天空、太阳光和港区海面材质位于 `omniverse/scenes/environment.usda`。
4. 需要重新导出时，在 Blender 中运行 `omniverse/scripts/export_usd.py`。

## 目录

- `blender/`：Blender 源模型和原始 USD 导出。
- `omniverse/`：可直接打开的 Omniverse/OpenUSD 内容工程。
- `doc/`：智慧港口数字孪生平台方案文档。

更多 Omniverse 工作流说明见 [`omniverse/README.md`](omniverse/README.md)。
