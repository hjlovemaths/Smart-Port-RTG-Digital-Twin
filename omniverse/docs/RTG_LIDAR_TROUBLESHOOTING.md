# RTG 车载 RTX LiDAR 点云问题记录

记录日期：2026-09-14  
项目：Smart Port RTG Digital Twin  
状态：已完成关键修复；已通过 Isaac Sim 6.0.1 全量场景验证

## 当前目标

在主 RTG 小车架（trolley）上挂载水平 RTX LiDAR 和向下 RTX LiDAR，以主雷达为坐标原点扫描周围及正下方集装箱几何，并输出雷达中心点云图（PLY、PCD、NPY）。同时保留世界坐标累积地图，供多位置拼接使用。

## 最新故障日志

执行异步采集：

```python
await capture_rtg_yard_pointcloud(timeout_frames=600)
```

得到：

```text
TimeoutError("RTX LiDAR produced no points. Press Play and ensure the RTX sensor extension is enabled. Writer status: {'sensorPath': '/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/ANIM_CTRL_RTG_TROLLEY_TRAVEL/RTG_YARD_LIDAR_MOUNT/RTG_YARD_LIDAR', 'outputDir': 'D:\\dnt\\project\\Smart Port RTG Digital Twin\\outputs\\rtg_pointcloud', 'frames': 0, 'scans': 0, 'writerCallbacks': 121, 'writerRenderProducts': 1, 'writerEmptyFrames': 121, 'writerLastRawElementCount': 0, 'latestPointCount': 0, 'accumulatedPointCount': 0, 'latestBundle': None}")
```

## 已确认现象

- `writerCallbacks=121`：采集 writer 正在持续收到渲染回调。
- `writerRenderProducts=1`：LiDAR RenderProduct 已经创建。
- `writerEmptyFrames=121`：所有回调都没有有效点云帧。
- `writerLastRawElementCount=0`：GenericModelOutput 没有有效元素。
- `frames=0`、`scans=0`：没有生成 PLY、PCD、NPY 点云文件。
- 因此问题已经缩小到“RTX LiDAR 没有产生有效 GenericModelOutput”，不是导出格式或超时循环本身。

## 2026-09-15 已加入的修复

`omniverse/scripts/rtg_yard_lidar.py` 现在除了自定义 Replicator writer，还会在采集循环中调用：

```python
sensor.get_data("generic-model-output")
```

direct 数据和 writer 数据共用同一套 GMO 解析、坐标转换和点云导出逻辑。状态中新增：

- `directPolls`：direct sensor 读取尝试次数。
- `directDataFrames`：direct sensor 成功取得并导出的点云帧数。
- `writerMissingGmo`：writer payload 中完全没有 GMO 的帧数。
- `writerZeroElementFrames`：GMO 存在但 `numElements=0` 的帧数。

判断方法：

- `directDataFrames > 0`：RTX sensor 有数据，原问题主要是自定义 writer payload 兼容性。
- `directDataFrames = 0` 且 `writerMissingGmo` 很高：writer 和 sensor 都没有拿到 GMO，继续检查 RTX 节点、配置和渲染设置。
- `directDataFrames = 0` 且 `writerZeroElementFrames` 很高：GMO 已创建但没有命中可见几何，需要检查位置、朝向、量程和场景几何。

## 2026-09-15 新日志结论

最新状态为：

```text
writerCallbacks=119
writerRenderProducts=1
writerEmptyFrames=119
writerLastRawElementCount=0
writerMissingGmo=0
writerZeroElementFrames=119
directPolls=600
directDataFrames=0
```

这表明 GMO 缓冲区存在，但每帧 `numElements` 都是 0；direct sensor 读取也没有点。已确认不是 writer 包装、异步等待或 PLY/PCD 导出问题。

NVIDIA Isaac Sim 的公开 issue 记录了同类 RTX LiDAR 空数据问题，workaround 是设置：

```text
omni:sensor:Core:skipDroppingInvalidPoints = True
```

该属性已加入 `Lidar.create()` 的命名配置、离线 fallback，并在创建 LiDAR 后再次强制写入。由于该 workaround 会保留距离为 0 的无效回波，代码现在会过滤无效距离，并额外记录 `writerInvalidOnlyFrames`。参考：[NVIDIA Isaac Sim issue #110](https://github.com/isaac-sim/IsaacSim/issues/110)。

应用 workaround 后的新日志为 `writerLastRawElementCount=921600`、`writerLastValidPointCount=0`、`writerInvalidOnlyFrames=213`，说明无效点过滤正常工作，但仍未命中几何。

为区分传感器问题和 live 箱区模型问题，已加入临时 session-layer 测试墙：

```python
print(create_rtg_lidar_probe())
```

测试完成后删除：

```python
remove_rtg_lidar_probe()
```

测试墙能产生 `directDataFrames > 0` 时，说明 RTX LiDAR 正常，下一步检查 live 箱区导入模型的可见性、材质和实例化；测试墙仍为 0 点时，继续检查传感器姿态或 RTX 渲染设置。

## 2026-09-15 profile 差分测试

warmup attach 后仍然得到 `writerLastRawElementCount=921600`、`writerLastValidPointCount=0`，因此启动竞态修复尚未解决问题。原先尝试通过 `Lidar(...)` 手工写入单发射器 `emitterState` 数组，但 Isaac Sim 6.0.1 会在 Fabric 转换阶段关闭 Kit。现在 `Single_Emitter_Rotary` 是一个兼容别名，实际调用 Isaac Sim 随附的 schema-valid `Example_Rotary` 3D 配置，不再手工写入发射器数组。

启动时增加参数：

```powershell
--lidar-config Single_Emitter_Rotary
```

该兼容 profile 使用 3D `Example_Rotary`，这样既保留 schema-valid 的启动方式，又能让不同仰角的射线命中箱体侧面；如果只需要水平截面，才显式使用 `Example_Rotary_2D`。

## 2026-09-15 启动竞态修复

用户提供的 Isaac Sim 6.0.1 相关报告显示，多发射器 rotary LiDAR 在暂停阶段创建 `LidarSensor`/RenderProduct 并立即 attach writer，可能永久得到合法 GMO 头但 `numElements=0`。本项目启动时正好在 Play 前创建 runtime sensor。

现在启动阶段只创建和配置 `OmniLidar` USD Prim；`LidarSensor`、RenderProduct 和 writer 延迟到 timeline Play 后 warmup 30 帧，再创建和 attach。采集函数新增 `warmup_frames` 参数，可按需调整：

```python
import asyncio

async def capture_once():
    print(await capture_rtg_yard_pointcloud(
        timeout_frames=600,
        warmup_frames=30,
        min_scans=8,
        output_frame="sensor",
    ))

asyncio.ensure_future(capture_once())
```

因此必须完全重启 Isaac Sim，不能只重新执行旧的 Script Editor 缓存代码。

## 2026-09-15 最终定位与验证结果

已在全量 live 箱区（503 个箱、40 个堆垛）中验证通过：

- `Example_Rotary` 3D 兼容 profile 产生有效 GMO：`921600` 个元素。
- 单圈基础验证实际导出约 `90000` 个点，PLY、PCD、NPY 均生成成功。
- 最新多圈验证连续采集 `8` 个扫描包并导出 `448712` 个点；当前默认采集会等待至少 8 个扫描包。`capture_rtg_yard_pointcloud()` 默认导出雷达坐标系点云，雷达位置为 `(0, 0, 0)`；需要世界坐标地图时传入 `output_frame="world"`，或调用 `save_rtg_yard_map()`。
- 3D 点云的验证高度范围为 `Z=-2.18 到 17.82`；旧的 `Example_Rotary_2D` 输出会把所有点压在雷达高度平面，因此查看器中只会看到零散水平线。
- 输出目录：`outputs/rtg_pointcloud`。

最终修复点：

1. `freeze_rtg_animations()` 不再关闭时间轴自动更新，也不再用 post-update 把时间锁死。它通过 Session Layer 屏蔽 RTG 的动画采样，让时间轴持续前进，RTX LiDAR 可以完成多圈扫描。
2. RTX LiDAR Prim 和运行时安装座不再写入 Session Layer。Session Layer 的 RTX/可渲染 Prim 会造成坏 GMO 或全零点云。
3. 安装座几何标记为 `purpose=guide`，保留可检查的安装位置，但不干扰 LiDAR 射线。
4. `create_rtg_lidar_probe()` 仅用于诊断；正式采集前不要创建探针，或先执行 `remove_rtg_lidar_probe()`。

验证生成的点云头部示例：

```text
element vertex 90041
WIDTH 90041
POINTS 90041
```

代码验证：

- LiDAR、启动器、物理控制器和 inventory 相关测试：`16 passed`。
- 全量测试：`427 passed, 1 failed`；唯一失败是已有的 HUD emissiveColor 数量断言，与本次 LiDAR 修改无关。

## 已遇到并处理的问题

1. 从 `C:\\Windows\\System32` 启动导致脚本路径错误。现在使用绝对路径启动。
2. Script Editor 直接执行顶层 `await` 会报 `SyntaxError: 'await' outside function`。现在使用 `asyncio.ensure_future()` 包装异步采集。
3. Script Editor 内调用同步 `app.update()` 导致 asyncio re-entry 错误。采集函数已改为 `await omni.kit.app.get_app().next_update_async()`。
4. writer 的 `write_metadata` 参数不匹配。已改为 `write_metadata(self)`。
5. live inventory layer 重复创建导致 `A layer already exists with identifier`。已改为复用和清理已有 SDF layer。
6. 在 `SimulationApp` 初始化前导入 `pxr` 导致 `ModuleNotFoundError: No module named 'pxr'`。live inventory 构建已移到 `SimulationApp` 初始化之后。
7. stage 重新加载会使现有 RTX RenderProduct 失效。启动器已改为打开 stage 前准备 live layer，运行中不再 reload stage。
8. LiDAR 最大量程已从原来的约 200 m 调整为 600 m，并加入：

```text
--/app/sensors/nv/lidar/outputBufferOnGPU=false
```

## 场景位置检查结果

- live 箱区容器的纵向 `Y` 坐标约为 `262.575–443.65`。
- 主 RTG 龙门动画初始 `Y` 坐标约为 `16.5775`。
- 新增 `move_primary_rtg_to_bay("3C/002")`：只给主/中间 RTG 的根节点增加静态 Y 偏移，左、右背景 RTG 不移动。
- 建议在创建 LiDAR 之前完成该定位；若已经创建，正式采集开始时也会重新同步雷达位置。验证中主 RTG 门架到达 `Y≈257.84`，随后成功采集 `91359` 个点。
- `move_gantry(...)`、`move_trolley(...)`、`move_hoist(...)` 仍分别控制大车、小车和吊具，整机定位后仍可继续使用。
- 采集开始时会再次读取当前 trolley 世界位姿并同步根节点 RTX LiDAR；因此先执行运动指令、再执行采集也能保持雷达与 RTG 对齐。

## 当前正确启动命令

必须完全关闭旧的 Isaac Sim 进程后重新启动，以避免旧 Python 模块仍驻留在内存中：

```powershell
& "D:\isaacsim\python.bat" "D:\dnt\project\Smart Port RTG Digital Twin\omniverse\scripts\launch_isaac_sim_project.py" --load-live-inventory --enable-rtg-lidar --freeze-rtgs --lidar-config Single_Emitter_Rotary --lidar-far-range 600
```

如果要把中间主 RTG 放到 3C/002 后再扫描，在命令末尾加上：

```powershell
--rtg-bay 3C/002
```

完整示例是在原命令末尾追加 `--rtg-bay 3C/002`。

重新启动后，按 Play，再在 Script Editor 使用以下方式采集；不要直接在 Script Editor 顶层执行 `await`：

```python
import asyncio

async def capture_once():
    try:
        print(await capture_rtg_yard_pointcloud(
            timeout_frames=600,
            min_scans=8,
            output_frame="sensor",
        ))
    except Exception as error:
        print(repr(error))

asyncio.ensure_future(capture_once())
```

启动器带 `--rtg-bay` 时，Script Editor 也会提供三类运动指令：

```python
print(move_gantry(257.84))  # 大车：3C/002 附近的世界 Y 目标
print(move_trolley(1.6))    # 小车
print(move_hoist(14.0))     # 吊具
```

其中 `--rtg-bay` 负责整机搬到 3C，三个 `move_*` 指令负责后续运动部件控制。正式扫描前不要调用 `create_rtg_lidar_probe()`。

### 以 RTG 雷达为中心的输出

采集函数内部同时保存两套坐标：

- `output_frame="sensor"`：雷达坐标系，雷达是原点，适合查看雷达看到的局部扫描。这是默认值。
- `output_frame="world"`：码头世界坐标，适合把 RTG 移到多个箱区位置后拼接地图。

当前启动时会创建两台 RTX LiDAR：水平雷达使用绿色回波，向下雷达使用橙色回波。向下雷达安装在水平雷达下方约 `0.20 m`，采用朝向世界 `-Z` 的姿态，默认量程 `150 m`；最终导出时向下雷达的点会转换到主水平雷达坐标系，因此仍只有一个 `(0, 0, 0)` 原点。

采集状态中：

- `frames` / `scans`：水平雷达收到的有效扫描数；
- `downFrames` / `downScans`：向下雷达收到的有效扫描数；
- `accumulatedSensorPointCount`：两台雷达合并后的主雷达坐标系点数。

正式采集必须等到两个雷达的扫描数都达到目标值；如果超时，先查看 `downFrames` 和 `downLatestPointCount`，不要只看水平雷达的 `frames`。

因此，查看器中不应再用旧的世界坐标 `rtg_yard_capture.npy` 判断雷达中心效果；重启并重新采集同名文件，或指定新的 `output_stem`。若只想导出已经累积的雷达中心点云，可执行：

```python
print(save_rtg_yard_sensor_scan("rtg_yard_sensor_scan"))
```

雷达中心坐标只能改变显示原点，不能穿透箱体遮挡。若要得到箱体背面或完整侧面，还需要让 RTG/小车移动到多个视角后分别采集，并用 `output_frame="world"` 拼接。

## 后续注意事项

### 1. 检查传感器 Prim 和属性

先执行一条内置诊断命令：

```python
print(diagnose_rtg_yard_lidar())
```

然后如需逐项检查，再执行下面的长版脚本：

在 stage 加载完成后执行：

```python
import omni.usd
from pxr import UsdGeom

stage = omni.usd.get_context().get_stage()
lidar_path = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/ANIM_CTRL_RTG_TROLLEY_TRAVEL/RTG_YARD_LIDAR_MOUNT/RTG_YARD_LIDAR"
prim = stage.GetPrimAtPath(lidar_path)
print("valid:", prim.IsValid(), "type:", prim.GetTypeName())
print("schemas:", prim.GetAppliedSchemas())
print("world:", UsdGeom.Xformable(prim).ComputeLocalToWorldTransform().ExtractTranslation())

for name in [
    "omni:sensor:Core:nearRangeM",
    "omni:sensor:Core:farRangeM",
    "omni:sensor:Core:scanType",
    "omni:sensor:Core:scanRateBaseHz",
    "omni:sensor:Core:numberOfEmitters",
]:
    attr = prim.GetAttribute(name)
    print(name, "=", attr.Get() if attr else None)
```

### 2. 检查 direct sensor data

使用 `rtg_lidar` 中保存的 `LidarSensor` 对象，直接读取 `generic-model-output`，确认是 sensor 本身没有数据，还是自定义 writer 解析错误。

### 3. 确认 RTX LiDAR extension

检查 `isaacsim.sensors.rtx.nodes` 是否启用，并确认启动日志没有 `Invalid USD RenderProduct Prim`、`Missing cached render settings` 或 GPU buffer 相关错误。

### 4. 检查 LiDAR 方向

如果 sensor 属性有效但仍然 0 点，临时把 LiDAR 放到 3C 第一箱列附近并测试多个旋转角度，确认扫描扇区是否覆盖箱区。

### 5. 检查 Nucleus / LiDAR 配置

当前代码优先使用 `Example_Rotary`，不可用时使用本地 128 通道 rotary schema fallback。需要确认启动日志实际走的是哪一条路径，以及 fallback 配置是否被 RTX 节点接受。

## 相关代码

- [rtg_yard_lidar.py](../scripts/rtg_yard_lidar.py)
- [launch_isaac_sim_project.py](../scripts/launch_isaac_sim_project.py)
- [rtg_isaac_physics.py](../scripts/rtg_isaac_physics.py)
- [smart_port.usda](../scenes/smart_port.usda)
- [live_containers.usda](../scenes/live_containers.usda)
