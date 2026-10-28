# Agent 可调用接口

项目的交互式运行接口已整理为 **37 个 Agent 工具**。在 **Smart Port Agent**
输入自然语言并点击 **Send** 即可，模型会选择对应接口，不需要输入 Python。
面板按钮保持英文，助手默认用简体中文回答。中文问答使用启动时加载的 SimHei
字体与中文字符集；更新后需要完整重启应用，重新加载字体和工具目录。

## 可以直接复制的口令

| 目的 | 中文口令 |
| --- | --- |
| 看 RTG 斜视角 | `从斜视角看 RTG。` |
| 小车平台下方正看吊具和箱顶 | `从小车平台下方正着往下看吊具和箱顶，使用广角并跟随小车。` |
| 下视镜头再调宽 | `保持平台下方正下视，把视野角调到 130 度。` |
| 跟随吊具 | `从右侧跟随 RTG 吊具。` |
| 俯视指定贝位 | `俯视 4C41 贝位。` |
| 看岸桥 | `从斜视角看 STS_04。` |
| 移动后切换视角 | `将 RTG 在 5 秒内移动到 4C03 贝位中心，然后切换到 RTG 斜视角。` |
| 移动岸桥小车 | `将 STS_04 的小车移动到 5 米。` |
| 查看当前任务 | `查看当前 RTG、岸桥和 IECS 任务及阶段。` |
| 查箱区图 | `查看 4C41 贝位的箱区图、各排层数及库存来源。` |
| 启动数据驱动 | `启动本机 TwinState receiver，接收 PLC 数据驱动 RTG。` |
| 查看所有能力 | `列出所有可以调用的接口。` |
| 停止本地设备运动 | `停止数字孪生中的所有设备运动。` |

运动和任务启动会显示 **Confirm / Cancel**。运动接口返回 `running` 或 `moving`
表示已经开始；通过 `get_operation_status` 或 `get_equipment_status` 确认是否到达。
视角切换和状态查询立即执行。

## 完整清单

| # | 接口 | 用途与主要参数 |
| --- | --- | --- |
| 1 | `get_agent_capabilities` | 当前完整工具目录、参数 schema 和运行条件。无需参数。 |
| 2 | `get_scene_status` | 当前场景、环境、时间和主 RTG 位置。 |
| 3 | `get_equipment_status` | RTG／岸桥实际位置、目标、限位、停止状态。`equipment=all/rtg/sts`，可选 `crane_id`。 |
| 4 | `get_current_tasks` | RTG 最新任务、仿真任务及队列、岸桥任务阶段、IECS 订单／车辆／暂停状态；保留数据时间。 |
| 5 | `list_viewpoints` | 可用视角、现有 USD 相机路径、当前相机和跟随状态。 |
| 6 | `set_viewport_view` | `target=port/rtg/trolley/spreader/bay/sts/task`；`view=angled/front/back/left/right/top/under_trolley`。支持 `bay_id`、`crane_id`、`distance_m`、`azimuth_deg`、`elevation_deg`、`field_of_view_deg`、`follow`。`target=trolley` 默认使用平台下方正下视广角镜头并跟随小车；贝位和岸桥视角必须明确编号。 |
| 7 | `switch_viewport_camera` | 选择现有 `camera_path` 或 `camera_number=1..33`，二选一；停止 Agent 相机跟随。 |
| 8 | `get_bay_inventory` | `bay_id`：贝位六位箱区图、各排层数、箱号、尺寸、ASCII 占用图和 40 英尺覆盖关系。 |
| 9 | `get_yard_inventory_summary` | 整个堆场或 `block=1C/2C/3C/4C` 的箱数、TEU、已占用逻辑贝位及箱区图。 |
| 10 | `reload_yard_inventory` | 显式重载当前接收器已选择的库存并刷新场景；需要已有接收器或仿真驱动。 |
| 11 | `move_rtg_to_bay` | `bay_id`、`anchor=start/center/end`、可选 `duration_s=0.2..120`。连续移动，保留贝位标定。 |
| 12 | `set_rtg_axis_positions` | 主 RTG 的 `gantry_m`、`trolley_m`、`hoist_m`，至少一轴；可选 `duration_s`，默认 3 秒。未指定轴不变。 |
| 13 | `set_rtg_spreader_size` | `size_ft=20/40`，控制伸缩吊具尺寸并返回实际值。 |
| 14 | `set_sts_axis_positions` | `crane_id=STS_01..STS_07`；`gantry_m`、`trolley_m`、`hoist_m`、`boom_deg`。或单独使用 `park_for_luffing=true`。沿用现有速度、分区限位和抬梁联锁。 |
| 15 | `stop_equipment` | `equipment=rtg/sts/all`，岸桥可指定 `crane_id`。停止本地控制目标；涉及共享任务时暂停相应任务。 |
| 16 | `resume_sts_crane` | 指定 `crane_id` 解除本地停止锁存；已经取消的目标不会自动重启，需要重新给目标。 |
| 17 | `control_timeline` | `action=status/play/pause/stop`，读取或控制 Kit 时间轴。 |
| 18 | `control_sts_task` | `action=status/start_unload/start_double20/pause/resume/reset/focus`。使用已有单箱卸船和双 20 英尺往返演示。 |
| 19 | `submit_rtg_task` | 仿真任务 `work_type=LOAD/UNLOAD/SWITCH`；按类型指定 `start_pos`／`end_pos`，可带 `bay_map`、箱尺寸／高度／箱号、`task_id`、排队选项。 |
| 20 | `control_rtg_simulation` | `status/start/stop/clear_task/preview_scenarios/start_scenarios/continue_scenarios/stop_scenarios/scenario_status`。支持任务数、随机种子、箱区、贝位范围和仿真库存重置选项。 |
| 21 | `control_iecs` | `status/start/submit/mixed/pause/resume/reset/speed/check_scene/inventory_preview/focus/stop`；流程 `unload/load/gate_in/gate_out/transfer`；倍率 1／5／10／20。 |
| 22 | `control_live_twin` | `action=status/start/stop`，控制已有本机 TwinState 接收器，接收真实数据并更新数字场景。 |
| 23 | `set_environment` | `mode=day/night/rain/fog`，切换环境预设。 |
| 24 | `set_time_of_day` | `hour=0..24`，设置港区时间和光照。 |
| 25 | `set_weather` | `wind_speed_mps=0..20`、`rain_amount=light/medium/heavy/torrential`、`fog_amount=light/medium/heavy`。 |
| 26 | `control_sea_motion` | `status/start/stop/pause/resume/strength/quality/hold/release`；海浪强度 0..3、平滑质量、指定现有船舶的 `ship_path`。 |
| 27 | `control_camera_display` | `display=rtsp_popup/spreader_rtsp_grid/model_grid/virtual_cameras`；`action=status/start/stop/refresh`。视频使用项目现有 RTSP 配置。 |
| 28 | `control_info_panel` | `panel=device/container/alarm`，`action=status/start/stop`，控制信息面板服务。 |
| 29 | `show_alarm` | `action=status/show/clear`；可指定本地模拟报警内容、`severity=info/warning/error`、持续秒数。 |
| 30 | `control_gamepad` | `status/start/stop/position_for_pick`；手柄索引 0..3；抓箱定位必须指定 `block/bay/row/tier`。 |
| 31 | `rtg_slot_operation` | `target/alignment/calibration_status/move/lower/lift/attach/detach/pick_demo/calibrate/reset_calibration/stop/stop_follow/scene_rows/scene_target/move_scene_row`；支持业务排位及场景从右数的可视排位。 |
| 32 | `control_rtg_physics` | `status/diagnose/validate/activate_visual/activate_physics/deactivate/set_targets/start_sync/stop_sync/freeze`。目标沿用 RTG 软限位。 |
| 33 | `control_lidar` | `status/start/stop/diagnose/markers_on/markers_off/scan_volumes/debug_draw/wireframes/probe_on/probe_off/save_map/save_scan`；传感器模式 `yard/representative/trolley_bottom/spreader/all`；支持配置、变体和扫描距离。 |
| 34 | `capture_lidar` | 异步采集 `mode=yard/fleet/batches/positions`；支持输出名称、坐标系、预热帧数和扫描数。多位置采集可指定 `bay_id`，不指定时保留当前大车位置。返回操作 ID。 |
| 35 | `get_operation_status` | 查询所有后台操作，或指定 `operation_id` 获取进度、完成状态、输出文件和错误。 |
| 36 | `cancel_operation` | 指定 `operation_id`，取消 Agent RTG 运动或后台采集操作。 |
| 37 | `control_livestream` | `action=start/restore_ui/enable_interaction/status`，启动本地 WebRTC 视窗或恢复交互。保留 Kit 界面；项目当前 `2k` 兼容别名实际使用 1080p 预设。 |

所有参数的精确类型、范围及必填条件见
[`agent_interfaces.json`](../extensions/smartport.agent_panel/docs/agent_interfaces.json)。
也可以直接询问 Agent：`set_sts_axis_positions 接口接受哪些参数？`

## 使用顺序与运行条件

- **岸桥抬梁**：先 `park_for_luffing=true`，等待到达停机位，再给 `boom_deg`。
  大臂必须完全放平后才能把小车重新开到海侧或降低吊具。不能通过同时给“停车＋抬梁”绕过联锁。
- **控制权**：手动 RTG／岸桥运动会检查正在运行的 IECS、岸桥任务、RTG 仿真、
  TwinState 驱动和手柄模式。冲突时先查看任务，再按你的要求完成、停止或复位相应流程。
  查询接口不会为了查询而启动或重置这些子系统。
- **库存**：优先读取当前驱动的内存库存，否则读取当前场景指定的 JSON 快照。
  结果保留来源、时间及完整性；未记录的贝位不会在不完整快照中被自动解释为空。
  TwinState 任务携带的有效箱区图会更新对应贝位并重绘；同一任务的后续修正也会同步。
  库存刷新、抓箱对位和接收性能修复见 [RTG 实时驱动说明](RTG_LIVE_DIAGNOSTICS.md)。
- **相机**：视角使用独立会话相机。`follow=true` 跟随被观察物体；切换相机或关闭
  Agent 运行控制时停止跟随。相机控制不会触发设备运动。
  `target=trolley, view=under_trolley` 把镜头固定在小车平台下方，正下看吊具与箱顶，
  默认随大车／小车移动，起升不改变相机安装高度。默认水平视野 110 度，
  `field_of_view_deg=30..140` 可调，数值越大看得越广；该预设不接受斜视角或距离参数。
  `follow=false` 可以固定当前机位。`list_viewpoints` 会返回当前预设和视野参数。
- **雷达与物理**：RTX 雷达采集需要 Isaac Sim 对应扩展；物理控制需要可用的
  Isaac／PhysX 运行环境。普通 Composer 中能使用的功能会按当前环境执行，缺少依赖时返回原因。
  雷达采集以后台操作运行，可通过操作 ID 查询或取消。
  `batches` 和 `positions` 采集使用世界坐标；`yard` 模式通过 `config` 选择配置，
  独立 `variant` 参数用于雷达阵列模式。`control_lidar(action="stop", mode="all")`
  会同时停止双向雷达和雷达阵列。
- **作用范围**：设备运动、抓放箱和任务演示作用于数字场景；不会向 PLC／RCMS
  下发真实设备控制指令。接口目录覆盖运行时能力；资产建模、离线构建／导出脚本和底层数学辅助函数
  继续作为开发工具使用，不暴露任意脚本执行入口。

## 代码对应关系

- 工具 schema 与参数校验：`smartport/agent_panel/agent_api.py`、`runtime_catalog.py`。
- 相机、运动、任务查询与后台操作：`scene_runtime.py`。
- IECS、任务、海面、视频、信息面板、抓放箱、物理、雷达适配：`project_interfaces.py`。
- 贝位与库存：`yard_queries.py`。
- 面板分发与确认：`extension.py`。
- 岸桥界面和 Agent 共享控制器：`omniverse/scripts/sts_control.py` 中的 `shared_sts_controller`。
