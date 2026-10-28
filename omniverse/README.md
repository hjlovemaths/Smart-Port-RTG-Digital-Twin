# Smart Port RTG Digital Twin - Omniverse Content Project

This directory contains the OpenUSD content project used by the Smart Port RTG Digital Twin Kit application.

## Entry point

Open `scenes/smart_port.usda` in Omniverse.

## Agent panel

The Smart Port Composer app and `scripts/launch_isaac_sim_project.py` load
`smartport.agent_panel` and show **Smart Port Agent** automatically on startup.
Use **Window > Smart Port Agent** to reopen the panel after closing it.

The `smartport.agent_panel` Kit extension adds an in-Omniverse question and
operation window backed by an OpenAI-compatible Chat Completions API. It can
query tasks, equipment and yard/bay inventory (including six-row box area maps),
switch viewport presets, move the primary RTG and STS cranes, and operate
simulation, IECS, environment and sensor workflows through 37 runtime tools.
Equipment movement requires operator confirmation and operates on the digital
scene without sending PLC commands. Live TwinState mode receives external data;
start the Twin Service separately before enabling its receiver. See
`extensions/smartport.agent_panel/docs/README.md` for loading and API setup.
The [Agent interface catalog](docs/AGENT_INTERFACES.md) lists all 37 connected
runtime tools, parameters and natural-language examples.

The panel labels remain English; questions and replies use Simplified Chinese
by default. Restart Kit after updating the extension so its startup font atlas
loads the bundled CJK glyph list and Windows SimHei font. API keys are held in
memory only; the URL and model can be saved separately.

## Layout

- `scenes/`: composed stages and environment layers.
- `assets/rtg/`: Blender-exported RTG and port assets.
- `materials/`: shared MDL/USD materials added during SimReady authoring.
- `scripts/`: repeatable Blender/USD preparation scripts.
- `config/`: project metadata and import settings.
- `docs/`: model, animation, and SimReady conversion analysis.

## Workflow

1. Export the Blender-authored visual asset and controller animation to `assets/rtg/RTG_Model.usdc`.
2. Open `scenes/smart_port.usda`; the RTG asset is included as a payload.
3. Validate hierarchy, materials, transforms, normals, lights, and performance.
4. Split environment and dynamic equipment into separate USD assets.
5. Add physics, joints, collision, sensors, and ROS2 bindings in stronger USD layers rather than editing the source asset.

### Automatic harbor sea motion

Use `scripts/launch_isaac_sim_project.py` to open the stage when you want the
previously demonstrated sea surface and moored-ship motion to start
automatically. The launcher enables it by default after the USD stage loads;
use `--disable-sea-motion` for a static scene or sensor capture. Opening the
USD file directly still loads the authored scene only; in that workflow run
`scripts/ship_sea_motion.py` from Script Editor with `show_sea_motion()`.

### Trolley-mounted yard LiDAR point cloud

`scripts/rtg_yard_lidar.py` adds a horizontal RTX LiDAR and a second RTX LiDAR
pointing downward near the primary RTG trolley. It also creates temporary blue
L1-L24 guide spheres from `scripts/rtg_lidar_position_markers.py`; these are
approximate drawing locations anchored to the imported green C camera markers.
Because Isaac Sim 6.x is
unstable when RTX prims are nested below the animated/physics RTG hierarchy,
the runtime sensors are root-level and their positions are synchronized to
the current trolley immediately before capture. The viewport displays
horizontal returns in green and downward returns in orange. The capture
writer exports both sensor-centered and world-coordinate `.ply`, `.pcd`, and
`.npy` files to `outputs/rtg_pointcloud`.

The recommended one-shot launcher is:

```powershell
& "D:/isaacsim/python.bat" `
  "D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/launch_isaac_sim_project.py" `
  --load-live-inventory `
  --enable-rtg-lidar `
  --freeze-rtgs `
  --rtg-bay 3C/002 `
  --lidar-far-range 220
```

The launcher initializes Isaac Sim, prepares the live USD layer, and then
opens the stage. If the
inventory JSON changes, close and relaunch Isaac Sim so the sublayer is loaded
cleanly; do not call `yard_reload(reload_stage=True)` from Script Editor while
RTX sensors are attached, because a synchronous Stage reload can invalidate
Hydra render products.

For a stationary RTG scan, use `--freeze-rtgs`. This freezes the primary and
background RTGs at their frame-1 pose while leaving the timeline available for
RTX sensor rendering; it does not enable PhysX or a motion controller.
The default LiDAR far range is 600 m because the current live inventory is
approximately 263–444 m from the initial primary-RTG pose; override it with
`--lidar-far-range <metres>` when the yard layout changes.

After the stage is open, use these snippets in the Script Editor:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_yard_lidar.py", encoding="utf-8").read())
start_rtg_yard_lidar()
import asyncio
asyncio.ensure_future(capture_rtg_yard_pointcloud())  # RTG LiDAR frame -> PLY/PCD/NPY
save_rtg_yard_map()            # accumulated voxel-thinned map -> PLY/PCD/NPY
get_rtg_yard_lidar_status()
stop_rtg_yard_lidar()
```

`capture_rtg_yard_pointcloud()` 默认以 RTG 上的 LiDAR 为原点输出
`output_frame="sensor"` 点云；需要码头世界坐标时传入
`output_frame="world"`。两种坐标会在采集过程中同时保留。

If only the blue installation markers are needed, run this in Script Editor:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_lidar_position_markers.py", encoding="utf-8").read())
show_rtg_lidar_position_markers()
```

They appear under `/World/RTG_LIDAR_POSITION_MARKERS` and follow the primary
RTG's current gantry/trolley/hoist pose. By default they use a bright blue
renderable material so they are visible in the Rendered viewport. Use
`show_rtg_lidar_position_markers(renderable=False)` during a capture-only
session if the spheres should be excluded from RTX returns; they remain
temporary session-layer geometry and are not saved into the source model.

The downward sensor uses a nadir orientation and a 150 m range by default,
which is enough to cover the containers below the RTG without doubling the
full-yard range allocation.

The default horizontal sensor mount is `(X=-3.7811, Y=5.00, Z=9.35)` in the
RTG trolley local frame; the downward sensor is 0.20 m lower and points toward
world `-Z`. If the particular RTG asset revision places the roof elsewhere,
pass a different `translation=(x, y, z)` to `start_rtg_yard_lidar`; the
downward sensor follows that position automatically. The implementation uses
LiDAR because a geometric yard point cloud needs dense range returns; a true
millimetre-wave Radar sensor is better suited to sparse detections and radial
velocity.

### 24-sensor L1-L24 fleet test

The drawing labels can also be created as 24 independent RTX LiDARs with the
opt-in fleet API. This is substantially heavier than the normal horizontal
plus downward pair, so start with one scan and keep the RTG frozen:

```powershell
& "D:/isaacsim/python.bat" `
  "D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/launch_isaac_sim_project.py" `
  --load-live-inventory `
  --enable-all-rtg-lidars `
  --freeze-rtgs `
  --rtg-bay 3C/002 `
  --lidar-config Example_Rotary `
  --lidar-far-range 220
```

After the stage is ready, press Play and run this in Script Editor:

```python
import asyncio

asyncio.ensure_future(
    capture_rtg_lidar_fleet_pointcloud(
        timeout_frames=600,
        min_scans=1,
        output_frame="world",
    )
)
```

The sensors are created under `/World/RTG_LIDAR_FLEET/L1` through `L24`.
Each sensor writes its own scan bundle, and the merged cloud is written as
`rtg_lidar_fleet_capture_world.ply/.pcd/.npy`. Check progress with
`get_rtg_lidar_fleet_status()` and stop the fleet with
`stop_rtg_lidar_fleet()`. If the normal 2-sensor service is already running,
the fleet start command stops it before creating L1-L24.

### Low-load representative fleet

For the focused under-trolley scan, use only `L2`, `L3`, `L4`, and `L5`. These
four sensors are resolved from the trolley-bottom installation markers and all
point downward, so the captured cloud covers the container bay below the
current RTG:

```powershell
& "D:/isaacsim/python.bat" `
  "D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/launch_isaac_sim_project.py" `
  --load-live-inventory `
  --enable-rtg-lidar-trolley-bottom `
  --freeze-rtgs `
  --rtg-bay 3C/002 `
  --lidar-config OS1 `
  --lidar-variant OS1_REV7_128ch10hz512res `
  --lidar-far-range 60
```

This mode keeps the live draw writer disabled for stability; after pressing
Play, use the same `capture_rtg_lidar_fleet_pointcloud(...)` Script Editor
capture command. The per-sensor files are named `L2` through `L5`, and the
merged file is written under `outputs/rtg_pointcloud`.

### Spreader upper-side L6-L13 fleet

To scan the bay from eight LiDARs around the spreader, use
`--enable-rtg-lidar-spreader`. This creates only `L6` through `L13`; they are
placed in the hoist/spreader local frame as four corners plus four side-center
units, so the installation follows the moving spreader instead of relying on
nearby camera markers. Each sensor points independently inward and 35 degrees
downward, covering container tops and upper side faces. The start status prints
`anchors`, `positions`, `installationSides`, and `orientationsWXYZ` so the
installation can be checked in the current stage:

```powershell
& "D:/isaacsim/python.bat" `
  "D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/launch_isaac_sim_project.py" `
  --load-live-inventory `
  --enable-rtg-lidar-spreader `
  --freeze-rtgs `
  --rtg-bay 3C/002 `
  --lidar-config OS1 `
  --lidar-variant OS1_REV7_128ch10hz512res `
  --lidar-far-range 150
```

After pressing Play, capture one scan per sensor and merge in world
coordinates:

```python
import asyncio

async def capture_spreader_lidar():
    try:
        result = await capture_rtg_lidar_fleet_pointcloud(
            timeout_frames=300,
            warmup_frames=20,
            min_scans=1,
            output_frame="world",
            output_stem="rtg_spreader_L6_L13",
            reset_accumulation=True,
            hold_pose=False,
            serial=True,
        )
        print("merged spreader cloud:", result)
    except Exception as error:
        print("spreader capture failed:", repr(error))

asyncio.ensure_future(capture_spreader_lidar())
```

The merged files are `outputs/rtg_pointcloud/rtg_spreader_L6_L13_world.ply`,
`.pcd`, and `.npy`. The per-sensor bundles remain in the same directory for
diagnosis. The live `draw-point-cloud` writers stay disabled to reduce GPU
load; the saved merged cloud is unaffected. The capture uses serial GMO
collection by default, so only one high-resolution OS1 output stream is active
at a time.

For a faster point-cloud test, use one LiDAR from each installation zone in the
supplied table: `L2`, `L6`, `L14`, `L18`, `L20`, `L22`, `L23`, and `L24` (8
sensors total). `L2` represents the same trolley-bottom installation row as
L1-L5; L1 is excluded because its GMO stream is invalid in this stage. The
representative mode uses the Ouster OS1 `OS1_REV7_128ch10hz512res` profile by
default. This is the 128-channel/10 Hz/512-resolution asset available in
`D:/isaacsim/Sensors/OS1` (the launcher passes it as `config=OS1` plus the
variant name so Isaac Sim does not mistake a Windows path for a config name).
It runs at the asset's 10 Hz scan rate, captures one scan after 10 warmup
frames, limits range to 150 m, applies 5 cm voxel thinning, and caps the merged
cloud at 500,000 points. L6 is the spreader-mounted representative; its local
`+X` ray axis is rotated to world `-Z`, so it scans downward under the spreader:

```powershell
& "D:/isaacsim/python.bat" `
  "D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/launch_isaac_sim_project.py" `
  --load-live-inventory `
  --enable-rtg-lidar-representatives `
  --freeze-rtgs `
  --rtg-bay 3C/002 `
  --lidar-far-range 150
```

After the stage is ready, press Play and run this in Script Editor. Use
`asyncio.ensure_future`; top-level `await` is invalid in the Script Editor:

```python
import asyncio

async def capture_representative_once():
    try:
        result = await capture_rtg_lidar_fleet_pointcloud(
            timeout_frames=180,
            warmup_frames=10,
            min_scans=1,
            output_frame="world",
            output_stem="rtg_lidar_representative_capture",
        )
        print(result)
    except Exception as error:
        print(repr(error))

asyncio.ensure_future(capture_representative_once())
```

The merged result is written to
`outputs/rtg_pointcloud/rtg_lidar_representative_capture_world.ply`, `.pcd`,
and `.npy`; each selected L sensor also writes its own bundle. Inspect progress
with `get_rtg_lidar_fleet_status()`. Representative mode intentionally does
not attach the live `draw-point-cloud` viewport writers; this avoids a GPU
crash while retaining the captured files. The 24-sensor mode remains available
with `--enable-all-rtg-lidars` when the higher GPU load is acceptable.

## RTG roles

The middle yard RTG is the production candidate and is exported under
`RTG_PRIMARY_DYNAMIC`. Its existing gantry, trolley, and hoist controller chain
is retained for SimReady conversion. The left and right RTGs are grouped under
`RTG_STATIC_LEFT` and `RTG_STATIC_RIGHT`; they remain visible but are detached
from the animated controllers and are not intended to receive physics or ROS2
control.

Run `scripts/configure_rtg_roles.py` in Blender to create or repair this role
hierarchy before exporting the USD asset.

`scenes/rtg_simready.usda` is a stronger, non-destructive SimReady layer for the
primary RTG. It preserves rigid-body, mass, and prismatic-joint metadata for a
later collision-proxy pass, adds the dynamic rope visual, and hides the legacy
silver rope meshes. Blender remains the source of truth for the three
controller animations. Prototype physics is disabled during visual validation
so PhysX cannot overwrite those authored transforms.

The 230-frame validation sequence raises and lowers the hoist during frames
1-70, moves the trolley during frames 90-150, and moves the complete primary
RTG 4.2 m during frames 160-230. The eight tires do not receive independent
rotation samples; they inherit the gantry transform together with the wheel
bogies, frame, cable loops, conduits, floodlights, trolley, hoist, and spreader.

The demo animation still keeps the small authored gantry movement for visual
validation, but the engineering limits now match the real 1C-4C yard:

| Joint | Hard limits | Soft limits | Maximum velocity |
| --- | ---: | ---: | ---: |
| Gantry engineering travel | 0.00 to 1,961.00 m | 1.00 to 1,960.00 m | 1.50 m/s |
| Trolley engineering travel | 0.00 to 18.06 m | 0.00 to 18.06 m | 1.00 m/s |
| Hoist engineering height | 0.00 to 18.00 m | 0.50 to 17.70 m | 0.90 m/s |

The hard limits describe the physical stroke. The soft limits describe the
normal command envelope used by WPF/ROS2 so regular commands keep clearance
from end stops and keep the spreader at least 0.5 m above ground.

### Engineering-coordinate mapping

PLC, ROS2, and WPF use real equipment coordinates while the current visual
model retains its validated travel. The live controller applies calibrated
trolley row stops so PLC trolley positions align the spreader centreline with
the current six-row container layout:

| Engineering value | USD controller value |
| --- | ---: |
| Gantry 0 m, 1C/001 seaside bay-start origin | Y = 0.00 |
| Gantry 1,961 m, 4C/041 bay end | Y = 1,961.00 |
| Trolley 0.48-1.00 m, truck centre | X = 0.615562 |
| Trolley 3.71 m, row 6 centre | X = -1.793975 |
| Trolley 6.66 m, row 5 centre | X = -2.815810 |
| Trolley 9.50 m, row 4 centre | X = -3.837645 |
| Trolley 12.35 m, row 3 centre | X = -4.859480 |
| Trolley 15.19 m, row 2 centre | X = -5.881315 |
| Trolley 18.06 m, row 1 centre | X = -6.903150 |
| Hoist 0 m, engineering zero | Z = -2.465000 |
| Hoist 2.6 m, spreader on tier-1 container top | Z = -1.525647 |
| Hoist +2.6 m, one physical container tier | Z += 0.939353 |
| Hoist 18 m, highest position | Z = 4.038213 |

The stop table is implemented only in `scripts/rtg_live_control.py` and maps
PLC readings to the positions already validated by the `task.txt` animation.
It does not reposition the RTG model, truck, container rows, or scripted demo.

Therefore `set_gantry()` accepts 0-1,961 m from the 1C/001 seaside origin,
`set_trolley()` accepts 0-18.06 actual metres, and `set_hoist()` accepts 0-18
actual metres. Reverse conversion helpers are provided for status values sent
back to WPF/ROS2. The rope visual continues to use the mapped USD hoist
position, so it remains attached throughout the full engineering range.

`scripts/yard_coordinate_mapping.py` contains the shared business-coordinate
rules for the real-scale yard. `set_gantry_bay("1C", 25)` and
`set_gantry_bay_id("1C/025")` move the gantry to a bay target; by default the
target is the bay center. Pass `anchor="start"` or `anchor="end"` when the
command should use the bay boundary instead. Bay spacing is 6.0 m pad length
plus 0.20 m clear gap, and the four C blocks are separated by 5.0 m gaps.

Bay-addressed stops include the visual centerline calibration used by the
painted yard numbers. In particular, `currentField=3C, currentBay=63` parks
the RTG short-beam/spreader center on the middle of the visible `63` mark and
the 20-ft box center. Continuous `bigcarElecDistance` commands remain on the
PLC metre calibration and are not shifted by this label correction.

The same layer contains `RTG_DYNAMIC_HOIST_ROPES`: 16 linear rope curves
measured from the Blender source. Their upper endpoints remain fixed to the
trolley while their lower endpoints use the same Z targets as the hoist joint,
so the ropes extend and retract with the spreader. They are visual geometry
only and are deliberately excluded from collision.

For live control in Kit, create `RTGController(stage)` from
`scripts/rtg_live_control.py`. `set_gantry`, `set_trolley`, and `set_hoist`
write deterministic controller transforms plus the retained drive targets to
the USD session layer. `set_hoist` updates all lower rope endpoints in the same
call, which is the entry point intended for the later ROS2/WPF data bridge.
Use `set_positions()` when a command is allowed to reach the hard limits, and
`set_positions_safe()` for normal operation inside the soft-limit envelope.

### 手柄远程手动模式

`scripts/rtg_gamepad_manual_control.py` 增加了本机优先的远程手动模式。它直接
优先读取 Omniverse Kit 已识别的手柄（可覆盖 DirectInput 设备），并在 Kit 输入不可用
时回退到 Windows XInput；不需要额外安装 Python 包。打开 `scenes/smart_port.usda`
后，在 Kit Script Editor 运行：

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_gamepad_manual_control.py", encoding="utf-8").read())
start_gamepad_manual_mode()
```

如果接的是第二至第四个 XInput 手柄，调用 `start_gamepad_manual_mode(1)`、`(2)` 或
`(3)`。模式启动会从当前 RTG 姿态接管，且实时 PLC/TwinState 仍会接收但不会覆盖本机
手柄动作；执行 `stop_gamepad_manual_mode()` 后，控制权立即回到实时数据流。

| 手柄输入 | 动作 |
| --- | --- |
| 按住右肩键（RB） | 死手开关；未按住时所有运动停止 |
| 左摇杆上下 | 大车前后移动 |
| 左摇杆左右 | 小车横移 |
| 右摇杆上下 | 吊具升降（上推为起升） |
| RB + A | 在吊具下方对位后锁箱 / 再按一次解锁放箱 |
| RB + X / B | 切换 20 ft / 40 ft 吊具 |
| 方向键上 | RTG 全局驾驶视角（跟随大车） |
| 方向键下 | 吊具下方对位视角（跟随吊具，向下观察锁头） |
| Back | 急停 |
| Start | 解除急停；仍须按住 RB 才能运动 |

键盘也可用于同一远程手动模式，便于不接手柄时先验证控制。移动时需一直按住
`Space` 作为死手开关：

| 键盘输入 | 动作 |
| --- | --- |
| `Space + W / S` | 大车前进 / 后退 |
| `Space + D / A` | 小车右移 / 左移 |
| `Space + ↑ / ↓` | 吊具起升 / 下降 |
| `G` | 锁箱 / 解锁放箱 |
| `1` / `2` | 切换 20 ft / 40 ft 吊具 |
| `F` | 在 RTG 全局视角与吊具下方视角之间切换 |
| `Home` / `End` | RTG 全局视角 / 吊具下方视角 |
| `Esc` / `Enter` | 急停 / 解除急停 |

抓箱不使用预设贝位、箱号或视口选中项。将吊具下降到目标箱上，按下锁箱后，脚本会根据
主 RTG 四个红色锁脚当前落入哪个箱体顶面来判定目标（至少两个锁脚有效接触）；日志和
`get_gamepad_manual_status()` 会显示实际锁住的 Prim 路径。锁住后，箱体会随大车、小车、
吊具的后续运动一起移动；在新位置再次按 `G` 或 `RB + A` 解锁，即把这一个箱体放在当前位置。
因此可自由驶往任意贝位、选择任意可接触箱体并搬运到另一贝位。

进入吊具下方视角时，手动模式会自动打开“吊具四角模型相机”面板，按参考视频以 2×2
四宫格显示四路 USD 模型相机画面：左上 C1、右上 C2、左下 C4、右下 C3。画面来自
数字孪生模型，不读取 RTSP；四个相机挂在动态吊具下方，会随吊具升降、大小车移动和
20/40 ft 伸缩同步更新。切回全局视角或退出手动模式时面板自动关闭。

手柄断开时，模式会保持接管但停止一切运动，避免 PLC 帧在断开瞬间驱动设备。通过
`get_gamepad_manual_status()` 可以在 Script Editor 查询连接、急停和锁箱状态。
启动时脚本还会暂时关闭 Kit 自带的 `Gamepad Camera Control`，使手柄只控制 RTG；退出
手动模式时会恢复此前的相机导航设置。

当 `rtg_twin_state_receiver.py` 的 `RTG Twin HUD` 已运行时，手动模式会实时更新 HUD
中的 `Gantry`、`Trolley`、`Hoist`、`Field`、`Bay`、`Bay ID` 和 `Mode`。其中 `Bay ID`
按当前大车工程米数映射为最近的堆场贝位，例如 `3C/063`；`Mode` 显示
`REMOTE_MANUAL`，明确当前不是 PLC 驱动。

仅在验证锁脚对位时，可使用下列测试辅助命令直接移动到当前仿真库存的一个示例位置；
它不是正常远程操作的一部分，也不会给后续 `G` 锁箱写入固定目标：

```python
position_gamepad_manual_for_pick()
```

然后按住 RB 并短按 A 验证锁箱。正常使用时无需运行该辅助命令：直接将 RTG 驶到任意
可触达箱体上方即可。

### Day and night environment presets

`smartport.rtg_bridge` 扩展创建的 `RTG Control` 移动控制面板提供 `Day` 和 `Night`
按钮。
夜晚模式会在 USD session layer 中降低天空与太阳亮度、显示 288 个路灯发光灯头，并
为 96 根三头路灯的每个灯头启用一个向下、无阴影的真实照明光源，三个方向相差
120°，共 288 个夜间路灯光源。RTG 原有横梁灯已由
`assets/resource/led/LED_Floodlight.usdz` 替换：模型自带 U 形支架和螺丝，每台 RTG 的
两根顶部大车长横梁各安装 4 盏，三台共 24 盏。两侧灯头分别朝 RTG 内侧斜下方照射；
夜晚时每盏灯启用一个同方向柔光灯，在地面形成相互衔接的照明光斑。24 个 RTG 光源均
关闭阴影且只在夜晚启用，物理灯具模型在白天和夜晚均保持可见。

环境逻辑集中在 `scripts/smart_port_environment.py` 的预设表中；后续雨天和雾天模式可
沿用同一入口扩展，不需要改动 RTG 状态接收与任务逻辑。

### PLC-free task simulation

When no PLC publisher is available, use `scripts/rtg_simulation_driver.py` in
the Kit Script Editor. It emits the same pose/task snapshot shape consumed by
`rtg_twin_state_receiver.py`, so the normal bay-map renderer, truck, spreader,
container attachment, and the shared LOAD/UNLOAD/SWITCH state machine are
exercised together:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_simulation_driver.py", encoding="utf-8").read())
start_simulation()
publish_simulated_task(
    work_type="UNLOAD",
    start_pos="3C6323",
    bay_map="441000",
    container_size=20,
)
```

Use `work_type="LOAD"` with `end_pos` for truck-to-yard, or
`work_type="SWITCH"` with both `start_pos` and `end_pos` for a rehandle. The
simulator can be stopped with `stop_simulation()` and the active task removed
with `clear_simulated_task()`. Do not run the simulator and the TCP live
receiver at the same time; both would write the same session-layer transforms.

Example task positions and debugging commands:

```python
# LOAD: truck -> row 2, tier 1
publish_simulated_task(
    work_type="LOAD",
    end_pos="3C6321",
    bay_map="401000",
    container_size=20,
)

# SWITCH: row 2, tier 3 -> row 5, tier 1
publish_simulated_task(
    work_type="SWITCH",
    start_pos="3C6323",
    end_pos="3C6351",
    bay_map="441000",
    container_size=20,
)

get_simulation_status()
clear_simulated_task()
stop_simulation()
```

`3C6324` is parsed as block `3C`, bay `063`, row `2`, tier `4`.

### Live PLC and task subscription

Run the standalone bridge from the repository root when RCMS/WPF is not
already publishing TwinState JSON on port 48700:

```powershell
python services\rtg_twin_service.py --config services\rtg_twin_service.config.json
```

The bridge subscribes to the configured PLC ZMQ endpoint (default port 5515)
and task/runinfo endpoint (default port 7065), merges the latest messages, and
publishes newline-delimited TwinState JSON on `127.0.0.1:48700`.

Start the Kit subscriber and scene driver in Script Editor:

```python
try:
    stop_simulation()
except NameError:
    pass

exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_twin_state_receiver.py", encoding="utf-8").read())
```

The script starts `start_live_driver("127.0.0.1", 48700)` automatically.
Use `get_latest_state()` to inspect the latest combined snapshot,
`apply_latest_state_once()` to replay it, and `stop_live_driver()` to stop the
TCP subscription and Kit update callback. Offline bridge checks:

```powershell
python services\rtg_twin_service.py --once
python services\rtg_twin_service.py --once --plc-json path\to\Yard_PLC_RTG_220.json --task-json path\to\Yard_TaskInfo_RTG_220.json
```

See `services/README.md` for jump-host relay/pull modes and the optional WPF
debug stream on port 48701.

The SimReady layer hides the 16 top-level
`RTG_BOUND_HOIST_ROPE_*__USD_MESH` meshes exported from Blender. Those meshes
are static duplicates of the dynamic rope system and would otherwise appear as
a second, silver-grey set of ropes in RTX rendering. Static left/right RTG rope
meshes are not affected.

The dynamic curves use all 16 endpoint pairs measured directly from Blender.
Their lower ends terminate at the four attachment groups on the upper edge of
the yellow hoist beam, using a small visual offset from the Blender guide-curve
tips. The rope lower endpoints
and the complete hoist/spreader hierarchy use the same hoist command, while the
upper endpoints remain fixed in trolley-local space.

The source Blender file remains the visual-authoring source of truth. Omniverse layers hold SimReady and runtime overrides.

The validation export intentionally excludes the Blender showcase hemisphere and
round display base. Omniverse lighting and environment layers should replace
that backdrop; the source `.blend` objects are not modified.

Only the `Omniverse_User_View` camera is included. Blender inspection cameras
and preview lights are omitted so their Omniverse gizmos cannot obstruct the
stage viewport.

The export uses Blender's built-in USD exporter. It exports only objects visible
in the active view layer and includes the controller time samples. During export
it creates temporary evaluated mesh copies of visible Blender curves while
preserving their controller parents, so thin rails, ropes, and cables keep both
their Blender thickness and their gantry/trolley motion in Omniverse. The source
`.blend` curves remain editable.

Static yard and ground containers are omitted from `RTG_Model.usdc`. The hoisted
`YLOAD` container, truck load, and ship deck cargo remain; runtime container
instances can be authored later from live terminal data.

## Runtime container generation

Inventory is mode-isolated: `data/yard_inventory.sim.json` contains the
deterministic random simulation yard, while `data/yard_inventory.live.json` is
the non-random live cache. On stage load the selected mode rebuilds occupied
bays below `/World/LiveContainers/YardInventory`. Task stacks, trucks, and
hoisted proxies remain session-layer actors; successful LOAD, UNLOAD, and
SWITCH completion updates only that mode's JSON inventory atomically and
rebuilds the affected bays. Repeated completion messages are ignored through
the persisted task ledger.

Live startup treats its local file as potentially stale. A task-provided
six-digit `bayMap` authoritatively reconciles the affected bay. Missing data,
cross-bay SWITCH without `targetBayMap`, corrupt JSON, or failed persistence
leaves the task in `PENDING` inventory-sync state instead of clearing visuals
or crashing the Kit driver.

Regenerate the deterministic development snapshot with:

```powershell
python omniverse\scripts\generate_yard_inventory.py --seed 20260729
```

`scripts/build_live_containers.py` generates lightweight container stacks into
`scenes/live_containers.usda`. The current demo command is:

```bash
blender --background --python omniverse/scripts/build_live_containers.py -- --bay 1C/041 --pattern 413413
```

The pattern is parsed one digit per row from right to left. For example,
`413413` means row 1 on the right has 4 tiers, row 2 has 1 tier, row 3 has 3
tiers, and so on toward the left. The generated layer writes one schematic stack
column per row, adds `live:bayId`, `live:row`, and `live:tiers` metadata, draws
tier separator lines, and draws a red outline around the bay-map footprint.
`live_containers.usda` is retained as an offline demo/validation layer but is
not sublayered by the production `smart_port.usda` stage, avoiding overlap with
the persistent runtime inventory.

Each physical container tier is treated as 2.6 m, so a six-tier stack is
15.6 m high in engineering coordinates. The compressed visual stack keeps its
existing 0.904353 m box height plus 0.035 m tier gap; live hoist movement maps
each physical 2.6 m increment to that 0.939353 m visual tier pitch.

## Real-scale 1C-4C yard layout

Run `scripts/build_yard_layout.py` in Blender to regenerate the central yard
layout. The current parameters are:

| Block | Bay range | Block length |
| --- | ---: | ---: |
| 1C | 001-091 | 564 m |
| 2C | 001-091 | 564 m |
| 3C | 001-091 | 564 m |
| 4C | 001-041 | 254 m |

Each bay pad is 6 m long and 12 m wide. Adjacent pads have a 0.20 m clear gap,
and adjacent C blocks have a 5 m clear gap. The complete layout is 1,961 m
long. `YARD_LAYOUT_1C4C_BAY_ANCHORS` contains 314 logical anchors with
`yard_block`, `bay_number`, and `bay_id` properties for later live container
instancing. The visual pads and markings are combined per block to keep the
Blender and USD object counts low.

The same build pass extends the existing continuous left, right, and outer
side-yard surfaces, stack pads, lane boundaries, AGV routes, and the internal
truck lane to the same `Y=-0.5` through `Y=1960.5` longitudinal range. Their
authored widths and X positions are preserved. Short decorative objects and
existing container meshes are not stretched.

Three previously uncovered longitudinal strips are filled by generated yard
surfaces and edge markings over the same 1,961 m range:

- left-central infill: X = -32.0 to -8.0 m;
- right-central infill: X = 8.0 to 37.5 m;
- right narrow infill: X = 60.0 to 67.0 m.

## RTG camera marker popup

The Kit receiver can open an RTSP preview when a camera marker is selected in
the viewport. Camera definitions are read from the local, git-ignored file
`omniverse/config/cameras/Cam_RTG_220.json`, with the RCMS camera configuration
used as a fallback.
The authored RTG markers (`C1`–`C33`) are preserved; markers without a
matching entry open an `UNCONFIGURED` status instead of creating geometry
outside the RTG. To start it standalone from Script Editor:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_camera_popup.py", encoding="utf-8").read())
start_rtg_camera_popup()
```

Select a green marker to open the `RTG Camera` window. The reader uses the Axis
RTSP endpoint `rtsp://user:password@ip:port/axis-media/media.amp` built from
the configuration (including the `AttachedLine` camera query). For HaiKang/Hikvision entries the reader uses
`/Streaming/Channels/101`-style URLs and defaults to RTSP port 554; add
`RtspPort` or `RtspChannel` to override those defaults. The Omniverse Kit Python environment needs an OpenCV build
with FFMPEG/RTSP support; if it is unavailable the popup reports the missing
decoder and still shows the resolved stream URL. Stop it with
`stop_rtg_camera_popup()`.

远程手动模式的吊具视角会自动打开四路模型相机面板。需要单独调试时，可在 Kit
Script Editor 中运行：

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_model_camera_grid.py", encoding="utf-8").read())
import omni.usd
start_rtg_model_camera_grid(omni.usd.get_context().get_stage())
stop_rtg_model_camera_grid()
```

## RTG virtual scene cameras

The same authored green marker prims can also be used as virtual camera mount
points inside the 3D world. Run this in the Kit Script Editor after opening
`scenes/smart_port.usda`:

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_virtual_cameras.py", encoding="utf-8").read())
create_rtg_virtual_cameras()
open_rtg_virtual_camera(1)
```

The script scans the current stage for markers named `C1` through `C33`, removes
older generated marker-child cameras, and creates `/World/RTGVirtualCameras/C01`
through `/World/RTGVirtualCameras/C33` aimed at the RTG work area. The two-digit
names keep Omniverse camera menus sorted in numeric order.

`create_rtg_virtual_cameras()` creates session-only cameras for immediate
debugging, aligns each Cxx label with its green marker, and starts the
click-to-open RTSP popup service. Use `open_rtg_virtual_camera(1)` through
`open_rtg_virtual_camera(33)` to bind the active viewport. Persist the camera
definitions with:

```python
save_rtg_virtual_camera_layer()
```

`scenes/rtg_virtual_cameras.usda` is sublayered by
`scenes/smart_port.usda`, so after saving the layer once, reopening or
reloading the stage loads all cameras without regenerating them in the session
layer. Use `cleanup_rtg_virtual_cameras()` to remove session-generated cameras.
