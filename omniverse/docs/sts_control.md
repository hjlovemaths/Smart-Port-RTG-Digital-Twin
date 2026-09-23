# 岸桥四轴控制

`smart_port.usda` 已组合 `sts_controls.usda`，场景中 7 台同型岸桥可独立控制。
默认选中中间的 `STS_04`（原 `CODEx_STS_Crane1`），其余可在面板下拉框选择。

## 在已经打开的 Omniverse / Isaac Sim 中使用

打开项目场景，在 Script Editor 执行：

```python
import runpy
runpy.run_path(r"D:\dnt\project\Smart Port RTG Digital Twin\omniverse\scripts\sts_control.py", run_name="__main__")
```

如果软件是在此次修改前打开的，脚本会在会话层挂入新的岸桥层。
不需要丢弃未保存修改或强制重载整个场景。再次执行会释放旧面板并恢复初始姿态。

也可以通过项目根目录的 `start_sts_controls.cmd` 启动新的 Isaac Sim 实例，
或给现有启动命令加 `--enable-sts-controls`。

## 面板操作

| 字段 | 含义 | 范围 |
| --- | --- | --- |
| Gantry offset | 大车相对原停机位置、沿码头世界 Y 轴的位移 | 每台独立限制，不越过相邻岸桥的分区 |
| Seaward reach | 小车距前梁铰点的海侧距离，负数为陆侧 | -10 至 12 场景米 |
| Spreader height | 吊具最低点的世界 Z 高度 | 0.3 至 8 场景米 |
| Boom up | 海侧前主梁仰角，0 为水平 | 0 至 80 度 |

输入目标后按相应行的 `Go`，轴会按速度限制连续运动；下方 `Actual` 显示当前值。
默认速度：大车 1、小车 0.8、吊具 0.6 场景米/秒，前梁 4 度/秒。
`Focus` 将视图对准所选岸桥。切换岸桥会保持各台的当前姿态并停止尚未结束的移动。

抬梁顺序：

1. 按 `Park for luffing`，等待小车到 -8、吊具到 7.7。
2. 按 `Raise 80 deg`，或输入所需角度后按 `Go`。
3. 按 `Lower 0 deg` 落梁；完全落平后才能把小车重新开到海侧或降低吊具。

前梁角度大于 0 时，小车必须在陆侧 -6 米以外，吊具高度至少 7.5 米。
联锁同时检查当前姿态和目标姿态，不能用一次同时下发“停车+抬梁”绕过。
`STOP ALL` 立即停止所有已激活的岸桥；`Resume selected` 解除所选岸桥急停，
之后重新下发目标。关闭面板释放控制并恢复原始停机姿态。
控制使用应用更新时钟，即使时间轴未播放也能操作。

## 模型与范围

前梁铰点来自当前模型海侧梁/中梁接缝（局部坐标 0, 0.94, 9.36）。
前梁两侧栏杆、走道、轨道和装饰网格在该平面处分割；后梁、机房和门架固定。
吊具随小车移动，吊绳长度随起升变化；前拉索一端固定在塔顶，另一端随前梁运动。
大车移动只带动岸桥，原有码头轨道和轨枕保持原位。

这是可交互的运动学仿真。距离使用原模型的场景尺度，尚未接入真实 PLC 标定、
载荷动力学、吊具抓箱或船舶/货物碰撞检测；现有简化模型也未做全行程机械干涉校核。
运行时修改写入独立匿名会话层，不保存到 USD 源文件。

## 维护

- `blender/build_sts_control_asset.py`：从现有静态 USDC 拆分生成控制模型。
- `omniverse/scripts/build_sts_control_layer.py`：按当前岸桥位置生成控制场景层。
- `omniverse/scripts/sts_control.py`：运动计算、联锁和控制面板。
- `tests/test_sts_control.py`：真实 USD 层级、变换、绳端点、联锁和清理集成测试，需 pxr 环境。
- `outputs/sts_control/`：工作、移动、停机和抬梁验证姿态，以及预览图。

要回到静态岸桥，可在 Layers 面板静音 `sts_controls.usda`，并关闭控制面板。
