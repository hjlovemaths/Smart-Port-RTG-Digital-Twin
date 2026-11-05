# Agent 可调用接口

项目的交互式运行接口已整理为 **46 个 Agent 工具**。在 **Smart Port Agent**
输入自然语言并点击 **Send** 即可，模型会选择对应接口，不需要输入 Python。
面板按钮保持英文，助手默认用简体中文回答。中文问答使用启动时加载的 SimHei
字体与中文字符集；更新后需要完整重启应用，重新加载字体和工具目录。

## 可以直接复制的口令

| 目的 | 中文口令 |
| --- | --- |
| 看 RTG 斜视角 | `从斜视角看 RTG。` |
| 小车平台下方正看吊具和箱顶 | `从小车平台下方正着往下看吊具和箱顶，使用广角并跟随小车。` |
| 同时看作业与整体 | `打开 RTG 作业三视角，同时显示正下视、侧面抓放箱和整台 RTG 的斜视全景。` |
| 关闭多视角 | `关闭 RTG 作业视角面板。` |
| 下视镜头再调宽 | `保持平台下方正下视，把视野角调到 130 度。` |
| 跟随吊具 | `从右侧跟随 RTG 吊具。` |
| 俯视指定贝位 | `俯视 4C41 贝位。` |
| 看岸桥 | `从斜视角看 STS_04。` |
| 移动后切换视角 | `将 RTG 在 5 秒内移动到 4C03 贝位中心，然后切换到 RTG 斜视角。` |
| 移动岸桥小车 | `将 STS_04 的小车移动到 5 米。` |
| 查看当前任务 | `查看当前 RTG、岸桥和 IECS 任务及阶段。` |
| 查询设备工作模式 | `当前 RTG 是维修模式、远程手动还是自动模式？请读取 TwinState 的 workMode。` |
| 查箱区图 | `查看 4C41 贝位的箱区图、各排层数及库存来源。` |
| 启动数据驱动 | `启动本机 TwinState receiver，接收 PLC 数据驱动 RTG。` |
| 启动 ZMQ PLC 服务 | `启动 RTG 的 ZMQ PLC 服务，并连接当前 Kit 场景适配器。` |
| 连接已有 ZMQ 服务 | `连接终端里已经启动的 ZMQ PLC 服务。` |
| 查看 ZMQ 状态 | `查看 ZMQ 服务、适配器和控制合状态。` |
| 停止 ZMQ 服务 | `停止 ZMQ PLC 服务并断开 Kit 适配器。` |
| 查看所有能力 | `列出所有可以调用的接口。` |
| 停止本地设备运动 | `停止数字孪生中的所有设备运动。` |

运动和任务启动会显示 **Confirm / Cancel**。运动接口返回 `running` 或 `moving`
表示已经开始；通过 `get_operation_status` 或 `get_equipment_status` 确认是否到达。

工作模式取自接收的 `workMode`：0 未知、1 自动、2 辅助、3 远程手动、4 本地、5 维修。
`get_equipment_status` 和 `get_current_tasks` 的 RTG 结果含 `work_mode`，没有任务也能查询。
仅 `is_current=true` 可作为当前实时设备模式；接收器停止、超过 5 秒未更新或模拟数据会单独标记。
该字段与本地手柄、Agent 或 PLC 场景控制权分别记录，启动 receiver 不代表自动模式。
视角切换和状态查询立即执行。
确认操作使用输入框上方的单一确认栏。点击 Confirm 或 Cancel 后立即收起；
等待超过 120 秒、关闭面板或请求结束时自动清理，过期按钮不能再确认操作。

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
| 27 | `control_camera_display` | `display=work_views/rtsp_popup/spreader_rtsp_grid/model_grid/virtual_cameras`；`action=status/start/stop/refresh`。work_views 为同面板正下视+侧面跟随+整台 RTG 斜视全景；视频使用项目现有 RTSP 配置。 |
| 28 | `control_info_panel` | `panel=device/container/alarm`，`action=status/start/stop`，控制信息面板服务。 |
| 29 | `show_alarm` | `action=status/show/clear`；可指定本地模拟报警内容、`severity=info/warning/error`、持续秒数。 |
| 30 | `control_gamepad` | `status/start/stop/position_for_pick`；手柄索引 0..3；抓箱定位必须指定 `block/bay/row/tier`。 |
| 31 | `rtg_slot_operation` | `target/alignment/calibration_status/move/lower/lift/attach/detach/pick_demo/calibrate/reset_calibration/stop/stop_follow/scene_rows/scene_target/move_scene_row`；支持业务排位及场景从右数的可视排位。 |
| 32 | `control_rtg_physics` | `status/diagnose/validate/activate_visual/activate_physics/deactivate/set_targets/start_sync/stop_sync/freeze`。目标沿用 RTG 软限位。 |
| 33 | `control_lidar` | `status/start/stop/diagnose/markers_on/markers_off/scan_volumes/debug_draw/wireframes/probe_on/probe_off/save_map/save_scan`；传感器模式 `yard/representative/trolley_bottom/spreader/all`；支持配置、变体和扫描距离。 |
| 34 | `capture_lidar` | 异步采集 `mode=yard/fleet/batches/positions`；支持输出名称、坐标系、预热帧数和扫描数。多位置采集可指定 `bay_id`，不指定时保留当前大车位置。返回操作 ID。 |
| 35 | `get_operation_status` | 查询所有后台操作，或指定 `operation_id` 获取进度、完成状态、输出文件和错误。 |
| 36 | `cancel_operation` | 指定 `operation_id`，取消 Agent RTG 运动或后台采集操作。 |
| 37 | `control_livestream` | `action=start/restore_ui/enable_interaction/status`，启动纯场景 WebRTC 或恢复交互。启动时隐藏 Kit 编辑器菜单和窗口，保留浏览器 Agent 与场景交互；`restore_ui` 显式恢复编辑器。`1080p` 为 1920×1080，`2k` 为 2560×1440。 |
| 38 | `control_plc_zmq` | `action=status/start/stop/connect/disconnect/physics_on/physics_off`。启动时优先复用已有服务；首次连接默认控制关闭。`physics_on` 申请本地控制合并开启 ZMQ 箱体碰撞、锁箱和重力模式，不发送速度；保持时间轴暂停。`physics_off` 要求先落箱解锁。异步操作需查询完成状态。停止只关闭 Agent 自己启动的服务。详见 [ZMQ 接口说明](RTG_PLC_ZMQ.md)。 |

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

## 船图结构查询

`get_vessel_structure` 读取 `vessel_structure.sample.json`（用户提供的船图结构 TXT 副本）。支持 bay、H/D 分区、location 筛选；include_slots/offset/limit 分页返回有效箱位。结构数据不表示实箱清单，未返回贝位不应判断为空。

后续 6.6 接口可通过 `query_vessel_structure(provider=...)` 接入，provider 返回原始响应信封，沿用同一校验和查询逻辑。当前未配置在线接口。

## 船—岸桥—集卡—堆场RTG联动演示

`preview_ship_to_yard_transfer` 按贝号、舷侧、从舷侧外侧数的排号自动选甲板最上层结构示意箱；按堆场贝/排解析现有支撑箱（含40尺覆盖相邻奇数贝）并检查6层上限、邻箱碰撞和岸桥覆盖。预览不修改USD，返回10分钟有效的plan_id。

`start_ship_to_yard_transfer` 使用plan_id复核源/目标指纹，审批后启动完整运动链：停车场01集卡出车、岸桥下降/闭锁/起升/移箱/落车/开锁、按道路和引桥运输、主RTG闭锁起升并在目标箱顶落箱。

`control_ship_to_yard_transfer` 查询状态、暂停/继续和复位；继续和复位需审批。完成后保留演示效果，复位恢复原箱位、设备和停车车辆。执行过程资源互锁，急停会暂停任务。场景改动使用临时会话属性及可移除子层，不修改库存文件、不发送PLC命令。

## 堆场经集卡和岸桥装船

`preview_yard_to_ship_transfer` 指定 `source_bay`、`source_row`、可选 `source_tier`（默认最上层可见箱），以及六位船上 `target_location`。例如堆场 `3C/005` 第2排顶部箱 → 船上 `011090`。可选 `crane_id` 指定岸桥；不指定时按实际行程选择。预览只读、有效期10分钟；校验源箱未被压住、目标6.6甲板箱位存在且顶部可达、支撑高度、邻箱、尺型和岸桥可达范围。

`start_yard_to_ship_transfer` 用 `plan_id` 再次核验场景并审批启动：停车场01集卡到堆场 → RTG下降、闭锁、起升、移箱、落车、开锁 → 集卡沿道路/引桥运输 → STS下降、闭锁、起升、移向船位、落箱、开锁。保留堆场箱的全部门框、瓦楞、锁杆等部件及原始尺寸，运输中不缩放。20尺目标用奇数贝；40尺用偶数贝，必须同时有相邻两个奇数贝的有效、等高、受支撑的顶部箱位。当前只支持已返回的甲板结构，不推测未知贝位，也不穿过舱盖装入船舱。

船上彩色箱为结构示意占位，并非已核实的实箱；预览和审批摘要明确说明目标顶部占位会在演示层中隐藏替换，不能据此声称真实箱位空闲。`control_yard_to_ship_transfer` 提供状态、暂停、继续和复位；完成后保留效果并占用设备，复位恢复堆场原箱、船上占位、车辆和吊机。与船到堆场任务互斥，资源及急停沿用共同互锁；不修改库存文件或发送PLC命令。

可直接说：`把3C/005第2排最上面的箱子，用RTG装集卡，再由岸桥装到船上011090箱位。` 或 `把3C/002第6排最上面的40尺箱装到船上020090箱位。`

两种完整搬运任务均先读取实际源箱的 `size_ft` 和当前RTG吊具尺型。预览返回 `spreader_adjustment`；启动后若不匹配，先进入“RTG吊具切换至20/40尺”阶段，以现有伸缩速度连续调整。前后端实际变换和反馈尺型均到位后才进入运输、下降、闭锁；取箱时再次互锁。状态包含 `rtg_spreader_size_ft`、`required_size_ft`、`spreader_ready`，不能把目标尺型当作已到位。手动控制写入session根层的同名属性优先级高于任务子层，任务仅按字段临时接管所需的opOrder及伸缩反馈，复位恢复原字段及其他用户会话修改；不会保存到场景文件。单独的 `rtg_slot_operation` 下降/闭锁/演示入口也校验源箱实际尺型，失配时拒绝动作并要求先匹配吊具。堆场奇数贝可能被40尺箱跨占，不能仅按贝号猜测源箱尺型。

示例：source_bay=1,source_side=port,row_from_side=1,target_bay=3C/001,target_row=6，对应结构箱位011090，支撑箱3C/002第6排40尺箱，目标新层为2。当前船箱已按原生堆场比例生成，默认不临时缩放。结构箱位不是真实配载清单，属于可复位仿真。道路路径是固定路线，非完整多车交通避碰或真实设备调度。


## 双向搬运自动电影镜头

双向搬运启动默认开启电影镜头；`start_ship_to_yard_transfer`、`start_yard_to_ship_transfer` 的 `cinematic=false` 可关闭。镜头按真实任务事件依次展示任务全景、集卡出场、船箱定位、岸桥取箱、船到集卡、集卡接箱、运输途中（引桥宽镜头）、RTG接箱、堆场落箱和完成全景。普通切镜约1.2秒平滑过渡，RTG作业区的转入/转出约2.4秒平滑环绕；RTG下降、闭锁、起升、横移、落箱、开锁和抬离共用固定机位，双20两次卸车也共用该机位，只更新阶段文字，不反复切镜、自动变焦或因瞬时遮挡换边。其他远距离转场带抬高轨迹；保持Z向上和海面以上，根据附近设备包围盒选择较少遮挡的机位。镜头不推进设备、不改变任务时序；任务暂停时镜头冻结。

`control_transfer_camera` 提供 `status/start/pause/resume/stop`，只操作视角，不暂停或恢复设备。手动鼠标导航或主动切换相机会暂停导演，点击“继续自动镜头”或发出口令才恢复。完成后保留3秒结果镜头，再恢复上一台相机；任务复位、换场景和扩展关闭会清理匿名相机层。镜头异常时退出电影模式，搬运继续。

网页左上角显示阶段、起点/终点及流程进度；管理员可暂停/继续/退出，观众只读。`GET /api/transfer-camera/state` 仅供已登录用户读取精简镜头状态；控制仍在管理员专用 `POST /api/smartport-agent/cinema`。语音与Agent问答面板的现有可见性不变。反向装船按实际阶段展示：任务全景 → 集卡前往堆场 → 源箱定位 → RTG闭锁取箱 → 移箱落车 → 集卡运输及引桥宽镜头 → 岸桥接箱 → 指定六位箱位装船 → 完成全景。镜头跟随实际搬运的可见箱体，手动接管、暂停、恢复和复位规则与正向相同。

可说：“用电影视角观察这次船到堆场搬运”“暂停自动镜头”“继续自动镜头”“退出电影视角”。


### 集卡装载与返场

20尺箱以实际拖板前缘为基准，留8厘米间隙靠近车头放置；40尺箱保持居中。两端吊具接触点不变，按装载偏移补偿车辆在STS、RTG下的停靠位置，载箱随车辆平移和转弯连续运动。

双向任务均在最终落箱、吊具抬离后，空车按车道返回停车场，再沿出库路径倒车回原车位和原朝向，随后才报告任务完成。返场期间保持落箱完成全景，不增加返场分镜；任务完成后保留3秒并恢复原视角。道路使用实际标线对应的方向和车道中心，STS下方为沿负Y方向的单向五车道，向北返程使用岸桥陆侧双向道路的北向车道；堆场作业通道始终沿负Y方向；4C末端没有跨水引桥，必须经已有引桥绕行。


### 双20卸船与RTG逐箱取放

`preview_ship_to_yard_transfer` 支持 `double20=true`。首只船箱由 `source_bay/source_side/row_from_side` 选择，第二只来自下一个奇数船贝的同排、同高顶部20尺结构箱位；STS一次双箱吊运，集卡承载整对箱子。RTG连续切换到20尺并确认就绪后，逐箱闭锁、起升、移至目标排、落箱、开锁；第二只落箱并抬离后集卡才返场。

两个落点必须分别由用户给出：`target_bay/target_row` 和 `second_target_bay/second_target_row`，不要求相邻，可为同贝不同排，当前要求在同一个堆场块内。按各排已渲染支撑箱顶计算落箱高度；不修改库存、保存船箱状态或发送PLC指令。审批、暂停、继续、急停与复位沿用现有船到堆场接口。电影镜头依次跟随两次RTG抓取和落箱，并显示第1/2只、第2/2只及对应落点。

### 一车两箱位、累积装车（堆场到岸桥或船）

`preview_yard_to_ship_transfer(plan_type="cumulative_double20")` 新增同一集卡的前、后两个20尺箱位。第一只使用 `source_bay/source_row`，第二只使用 `second_source_bay/second_source_row`。`source_tier/second_source_tier` 可选，默认按吊取顺序选当时最上层：同一贝、排可以连续吊取顶部两箱，也可指定不同贝、不同排。只支持同一堆场块内两只实际20尺箱，拒绝40尺、混装、重复箱、重复船位、埋箱及不可达落点。预览只读，检查拖板尺寸及同一台岸桥的覆盖范围。

- `delivery_mode="quay"`：仅运到岸桥。用户指定 `crane_id`，省略两个船上目标；集卡载着两箱停到指定岸桥交接区，保留载箱等待，岸桥不动作、车辆不空车返场。
- `delivery_mode="ship"`（默认）：继续装船。另需用户分别指定 `target_location`、`second_target_location` 两个六位船上目标，岸桥逐箱装船后集卡空车返场。

程序按堆场作业车道的负Y前进方向，优先访问上游源贝位，再访问下游源贝位，保持每只源箱与用户指定的船上目标对应；`loading_order` 返回实际装箱顺序（0/1对应用户第一/第二只）。集卡先用前箱位对准第一只源箱所在贝，RTG在该贝抓箱、起升，仅小车横移落到车上；吊具夹箱期间大车禁止移动。开锁并抬离后，RTG空吊具转移、集卡带第一箱向前到第二源贝位，用后箱位对准第二只源箱，再抓箱落车。同贝两箱也按先前位、后后位安排集卡向前挪位，不倒车对箱位。

预览返回两个 `yard_loading_stops_m`、`truck_route_between_loads` 和前后箱位的 `yard_vehicle_stop_m/yard_contact_m`，落车点与各自源箱Y坐标一致。堆场内不允许倒车或逆行；RTG未开锁时车辆禁止行驶，载箱大车命令被拒绝。两箱全部落稳开锁、吊具抬离后才驶往岸桥；每箱独立随车平移转弯。选择继续装船时，到岸桥后逐箱装到各自船位，全部卸完后空车返场，保留倒车进入原停车位的流程。

沿用 `start_yard_to_ship_transfer` 的 `plan_id`、预览复核、审批、电影镜头及 `control_yard_to_ship_transfer` 的暂停/继续/复位。状态返回 `plan_type`、`current_box`、`loaded_count`、`landed_count`、`departure_required_count=2` 和每箱的 `slot_id/on_truck/landed`；车体演示属性也分别记录两个箱位及载箱数。复位恢复两只堆场箱、两个船上占位、车辆及设备。仅修改临时演示层，不保存库存或发送PLC命令。未指定 `plan_type` 时保持原单箱计划。

示例口令：`把3C/005第2排和3C/015第1排最上面的20尺箱，用RTG连续装到同一辆集卡，装满两箱后一起运到1号岸桥等候交接。` 或 `把3C/005第2排最上面的两个20尺箱，用RTG连续装到同一辆集卡，装满两箱后一起去岸桥，分别装到船上011090和031090。`
