# Smart Port RTG Digital Twin

智慧港口 RTG（轮胎式集装箱门式起重机）数字孪生项目。项目以 Blender 维护视觉源模型，以 OpenUSD/Omniverse 组织港区场景和实时渲染，并逐步接入 Isaac Sim、ROS2、WPF 客户端和 AI Agent。

## 项目定位

本项目不是单纯的三维模型查看器，而是一套面向港口自动化设备的数字孪生基础平台，目标是把 RTG、集装箱、车辆、堆场环境和实时设备数据组织成同一个可扩展系统。

- **Blender**：视觉资产源文件，负责建模、材质和基础动画。
- **OpenUSD / Omniverse**：数字世界与高真实感渲染中心，负责资产组合、场景分层、光照、天气和实时画面。
- **Isaac Sim / ROS2**：后续物理仿真、传感器模拟、设备控制和消息通信层。
- **WPF**：面向操作员的桌面控制中心和业务界面，而不是高精度模型的第二份维护端。
- **AI Agent**：后续用于智能调度、异常分析、故障预测和辅助决策。

## WPF 项目的定位

WPF 客户端负责数字孪生系统的“操作台”：显示实时状态、作业任务、告警、趋势、设备参数和控制入口，并与后端或 ROS2 网关交换业务数据。

高精度三维画面建议由 Omniverse 渲染后，以实时视频流方式接入 WPF。这样 WPF 不需要再次导入整套 USD/OBJ 高精度模型，也不用承担 RTX 渲染和场景同步；它只需要显示视频流，并在画面周围叠加设备状态、按钮、报警和任务信息。控制命令从 WPF 发往控制服务/ROS2，设备状态再回传到 WPF 和 Omniverse，二者共享同一份实时数据。

```text
PLC / 传感器 / 作业系统
          │
          ▼
  数据服务 / ROS2 网关
      ┌───┴───────────────┐
      ▼                   ▼
Omniverse / Isaac Sim     WPF 客户端
场景、仿真、RTX 渲染       状态、告警、任务、控制
      │                   │
      └── 实时视频流 ──────►│
```

现有 OBJ 方案可以保留为离线、低配或断流时的轻量备用视图，但不建议让 WPF 同时维护一套与 Omniverse 重复的高精度模型和动画逻辑。

## 六步实施路线

以下顺序来自 `doc/智慧港口数字孪生平台方案总结.pdf`，并结合当前工程状态进行了落地说明。

1. **安装 Omniverse 开发环境**：准备 Omniverse/Kit、OpenUSD 和 RTX 运行环境。当前已完成。
2. **创建第一个 USD 场景**：建立可组合的场景入口、环境层和资产层。当前入口为 `omniverse/scenes/smart_port.usda`，已完成基础搭建。
3. **导入 RTG 模型**：从 Blender 导出 USD，确定中间 RTG 为正式动态设备，左右两台作为静态背景；大车、小车、吊具、绳索和大车附件的基础动画校验已经完成，碰撞代理和正式物理关节仍待完善。
4. **实现白天、夜晚、雨雾环境**：建立天空、太阳、海面、材质和天气切换。白天环境、天空和海面已有基础版本，夜晚、雨雾和性能分级仍需补充。
5. **接入 ROS2 控制**：把大车、小车、吊具、集装箱和传感器状态映射到 ROS2 Topic/Service/Action，并建立 WPF 到控制网关的命令与状态闭环。
6. **添加 AI Agent 智能调度**：在稳定的数据、控制和仿真基础上加入任务调度、故障预测、异常诊断和自动优化。

## 当前 RTG 资产

- 中间 RTG：`RTG_PRIMARY_DYNAMIC`，作为正式动态 RTG。
- 左右 RTG：`RTG_STATIC_LEFT`、`RTG_STATIC_RIGHT`，作为静态场景模型。
- 动作控制器：大车 `ANIM_CTRL_RTG_GANTRY_TRAVEL`、小车 `ANIM_CTRL_RTG_TROLLEY_TRAVEL`、吊具 `ANIM_CTRL_RTG_HOIST_VERTICAL`。
- 地面静态集装箱不进入导出资产，后续由实时数据实例化；吊运箱、卡车载箱和船上集装箱保留。

## 快速开始

1. 在 Omniverse 中打开 `omniverse/scenes/smart_port.usda`。
2. 场景通过 payload 加载 `omniverse/assets/rtg/RTG_Model.usdc`。
3. 天空、太阳光和海面材质位于 `omniverse/scenes/environment.usda`。
4. 修改 Blender 模型后，先运行 `omniverse/scripts/configure_rtg_roles.py` 修复 RTG 角色和层级，再运行 `omniverse/scripts/export_usd.py` 重新导出。
5. `omniverse/scenes/rtg_simready.usda` 负责非破坏性的 SimReady 元数据和动态绳索覆盖。

## 运行与调试指令

以下 Python 指令需要在 Omniverse Kit 的 Script Editor 中执行，并且应先打开
`omniverse/scenes/smart_port.usda`。模拟任务和实时 PLC 接收器会写入同一套
RTG 会话层变换，二者不要同时运行。

### 脚本入口速查

| 文件 | 在哪里运行 | 用途 | 是否直接运行 |
|---|---|---|---|
| `omniverse/scripts/rtg_simulation_driver.py` | Omniverse Kit Script Editor | 启动虚拟任务、随机仿真库存和 RTG 动画 | 是，用 `exec(open(...).read())` |
| `omniverse/scripts/rtg_twin_state_receiver.py` | Omniverse Kit Script Editor | 接收真实 TwinState、驱动场景并维护真实库存 | 是，用 `exec(open(...).read())` |
| `services/rtg_twin_service.py` | 项目根目录 PowerShell | 订阅 PLC/RCMS，向 Omniverse 发布 TwinState | 是，用系统 Python |
| `omniverse/scripts/generate_yard_inventory.py` | 项目根目录 PowerShell | 生成仿真随机库存或空的真实库存缓存 | 是，用系统 Python |
| `omniverse/scripts/yard_inventory.py` | 由上述脚本自动导入 | 库存模型、原子保存、任务结算 | 否 |
| `omniverse/scripts/rtg_task_state_machine.py` | 由仿真器和接收器自动导入 | LOAD/UNLOAD/SWITCH 状态机 | 否 |
| `omniverse/scripts/rtg_live_control.py` | 由接收器自动导入 | RTG 大车、小车、吊具和伸缩吊具控制 | 否 |

推荐按以下二选一流程运行：

```text
仿真：打开 smart_port.usda
  -> 在 Script Editor 执行 rtg_simulation_driver.py
  -> 调用 publish_simulated_task(...)

真实：PowerShell 启动 services/rtg_twin_service.py
  -> 打开 smart_port.usda
  -> 在 Script Editor 执行 rtg_twin_state_receiver.py
```

### 1. 发布模拟作业任务

没有 PLC 数据时，可使用模拟驱动完整调试箱区渲染、集卡进出、吊具抓放和
LOAD、UNLOAD、SWITCH 三种作业状态机：

```python
try:
    stop_live_driver()
except NameError:
    pass

exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_simulation_driver.py", encoding="utf-8").read())
start_simulation()
```

`exec(...)` 加载脚本后不会连接 PLC；`start_simulation()` 创建仿真更新循环，
并自动读取 `omniverse/data/yard_inventory.sim.json`。重复执行脚本会先停止
旧仿真，避免两个更新循环同时控制 RTG。调用 `start_simulation()` 后会立即
显示随机箱区，不需要先发布任务；控制台应输出 `stacks=62`、
`containers=574`。

出箱：从 `3C/063` 的第2排第4层抓箱并放到集卡：

```python
publish_simulated_task(
    work_type="UNLOAD",
    start_pos="3C6324",
    bay_map="441000",
    container_size=20,
    container_no="SIM-UNLOAD-001",
)
```

进箱：从集卡抓箱并放到 `3C/063` 的第2排第1层：

```python
publish_simulated_task(
    work_type="LOAD",
    end_pos="3C6321",
    bay_map="401000",
    container_size=20,
    container_no="SIM-LOAD-001",
)
```

翻箱：从第2排第3层移动到第5排第1层：

```python
publish_simulated_task(
    work_type="SWITCH",
    start_pos="3C6323",
    end_pos="3C6351",
    bay_map="441000",
    container_size=20,
    container_no="SIM-SWITCH-001",
)
```

位置格式为“堆场 + 贝号 + 排 + 层”，例如 `3C6324` 表示
`3C/063，第2排，第4层`。常用调试命令：

```python
get_simulation_status()   # 查看当前状态机阶段和任务
clear_simulated_task()    # 清除当前模拟任务
stop_simulation()         # 停止模拟驱动
```

### 2. 订阅 PLC 和任务消息

实时链路为：

```text
PLC ZMQ :5515 + task/runinfo ZMQ :7065
  -> services/rtg_twin_service.py
  -> TCP 127.0.0.1:48700（每行一个 TwinState JSON）
  -> omniverse/scripts/rtg_twin_state_receiver.py
  -> RTG、箱区、集卡和状态机
```

先在项目根目录的 PowerShell 中启动桥接服务：

```powershell
python services\rtg_twin_service.py --config services\rtg_twin_service.config.json
```

然后在 Kit Script Editor 中启动实时接收与模型驱动：

```python
try:
    stop_simulation()
except NameError:
    pass

exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_twin_state_receiver.py", encoding="utf-8").read())
```

`exec(...)` 会自动调用 `start_live_driver()`，连接 `127.0.0.1:48700`，
并读取 `omniverse/data/yard_inventory.live.json`。无需再手工调用一次
`start_live_driver()`；只有停止后重启或修改地址时才需要调用它。

调试命令：

```python
get_latest_state()             # 查看最近一帧 PLC + 任务快照
apply_latest_state_once()      # 手工重放最近一帧
get_yard_inventory_status()    # 查看模式、同步状态、统计和已结算任务
reload_yard_inventory()        # 重载本地缓存；真实模式仍保持待同步
stop_live_driver()             # 停止 TCP 接收和场景更新
start_live_driver("127.0.0.1", 48700)  # 停止后重新启动
```

如果业务系统能导出一份与库存 JSON 同结构的权威全场快照，在 Script Editor
中调用：

```python
synchronize_live_yard_inventory(
    r"D:\path\authoritative_inventory.json"
)
```

只检查服务的数据转换而不持续运行：

```powershell
python services\rtg_twin_service.py --once
python services\rtg_twin_service.py --once --plc-json path\to\Yard_PLC_RTG_220.json --task-json path\to\Yard_TaskInfo_RTG_220.json
```

跳板机主动拉取、消息落盘和 WPF 调试端口说明见
[`services/README.md`](services/README.md)。

### 3. 全场持久化箱区库存

仿真和真实库存使用完全隔离的文件：

- `omniverse/data/yard_inventory.sim.json`：固定随机种子的虚拟库存，只由
  `rtg_simulation_driver.py` 使用。
- `omniverse/data/yard_inventory.live.json`：真实链路的本地缓存，初始为空，
  绝不加载随机箱子。

两份快照都覆盖 1C-4C 的全部 314 个逻辑贝位，记录箱子的尺寸、高度、类型和
来源任务。Omniverse 将当前模式的库存恢复到
`/World/LiveContainers/YardInventory`；任务执行期间仅使用临时工作箱区和
车辆对象。

真实模式每次启动都将本地文件视为可能过期的缓存：

- 任务带有合法 `bayMap` 时，先用权威箱区图校准本次作业贝位，再允许结算。
- 缺少 `bayMap` 时标记为 `PENDING`，保留现场并阻止错误库存写入。
- 跨贝 `SWITCH` 还需要 `targetBayMap`，或先同步一份权威全场快照。
- 快照损坏、源箱不匹配或写盘失败时降级并限频告警，不会让 Kit 驱动崩溃；
  写盘失败会同时回滚内存事务。

成功任务按 `taskId` 幂等结算：

- `LOAD`：向目标排层增加箱子。
- `UNLOAD`：从源排层移除箱子。
- `SWITCH`：将源箱移动到目标排层，可跨贝位。

结算采用原子文件替换。失败、取消或无法定位源箱时不会修改库存，也不会清除
任务现场。任务成功后只清理集卡、吊起箱和临时工作箱区，持久箱区继续保留。

需要重新生成可复现的随机初始快照时，在项目根目录运行：

```powershell
python omniverse\scripts\generate_yard_inventory.py --mode random --seed 20260729
```

省略 `--output` 时固定写入
`omniverse/data/yard_inventory.sim.json`，不会覆盖真实库存。

生成空的真实库存缓存：

```powershell
python omniverse\scripts\generate_yard_inventory.py --mode empty --output omniverse\data\yard_inventory.live.json
```

这条命令会覆盖真实库存缓存，只应在初始化或明确需要重置测试数据时执行。

可分别用 `SMART_PORT_SIM_INVENTORY_PATH` 和
`SMART_PORT_LIVE_INVENTORY_PATH` 指定库存文件。两种模式不接受同一个
通用默认变量，避免误把随机仿真库存接入真实链路。

### 4. 创建和打开 RTG 虚拟相机

加载虚拟相机工具：

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_virtual_cameras.py", encoding="utf-8").read())
```

只在当前会话创建 C01-C33，并立即切换到 C01：

```python
create_rtg_virtual_cameras()
open_rtg_virtual_camera(1)
```

切换其他相机：

```python
open_rtg_virtual_camera(12)
open_rtg_virtual_camera(33)
```

将相机永久保存到 `omniverse/scenes/rtg_virtual_cameras.usda`：

```python
save_rtg_virtual_camera_layer()
```

该层已经由 `smart_port.usda` 组合，保存后重新打开或重新加载场景即可自动得到
C01-C33。需要重建当前会话相机时可再次调用 `create_rtg_virtual_cameras()`；
清理当前会话生成的相机使用 `cleanup_rtg_virtual_cameras()`。

## 目录

- `blender/`：Blender 源模型。
- `omniverse/`：可直接打开的 Omniverse/OpenUSD 内容工程。
- `doc/`：智慧港口数字孪生平台方案文档。
结合[方案 PDF](</D:/dnt/project/Smart Port RTG Digital Twin/doc/智慧港口数字孪生平台方案总结.pdf>)，你目前完成的是“视觉资产与 USD 场景基础”，接下来重点应转向 **SimReady、运动控制、ROS2 和实时数据**。

| PDF 阶段 | 当前状态 | 还需要做 |
|---|---|---|
| 1. RTG 模型转 USD | 已完成 | 整理生产级模型层级 |
| 2. SimReady RTG | 未完成 | 碰撞体、质量、关节、驱动、限位 |
| 3. 港区环境 | 部分完成 | 已有白天天空和海面；缺夜晚、雨、雾 |
| 4. Isaac Sim + ROS2 | 未开始 | 控制、状态回传、传感器仿真 |
| 5. AI Agent | 未开始 | 调度、故障诊断、预测维护 |
| 展示端 | 尚未正式接入 | WPF/Unity/Web 与数字孪生数据联动 |

## 最优先：把 RTG 做成 SimReady 设备

建议先做一台标准 RTG：

- 清理模型里重复的 39 组控制器，仅保留一套正式 RTG。
- 将模型拆成：
  - `Gantry`：大车及门架
  - `Trolley`：小车
  - `Hoist`：吊具升降系统
  - `Spreader`：吊具
  - `Payload`：被吊集装箱
- 建立三个主要移动关节：
  - 大车：Y 轴 Prismatic Joint
  - 小车：X 轴 Prismatic Joint
  - 吊具：Z 轴 Prismatic Joint
- 为关节设置行程、最大速度、加速度、阻尼和急停限制。
- 添加简化碰撞体、质量、重心和惯量。
- 添加吊具锁箱/解锁状态，集装箱不能再依靠“显示/隐藏动画”模拟装卸。
- 车轮、卷筒和滑轮旋转可以后做；它们主要影响视觉，不是第一阶段控制闭环的必要条件。

现有运动分析已经写在 [RTG_ANIMATION_ANALYSIS.md](</D:/dnt/project/Smart Port RTG Digital Twin/omniverse/docs/RTG_ANIMATION_ANALYSIS.md>)。

## 第二步：完成一次完整装卸循环

在接 ROS2 之前，先在 Isaac Sim 内部完成手动控制：

1. 大车移动到目标贝位。
2. 小车移动到集装箱上方。
3. 吊具下降。
4. 对位并锁箱。
5. 起升集装箱。
6. 大车/小车移动。
7. 下降、解锁并放箱。
8. 验证碰撞、限位和急停。

这是后续 ROS2、实时数据和 AI Agent 的共同基础。这个循环没有打通，直接做 AI 调度意义不大。

## 第三步：接入 ROS2 和实时状态

建议先定义一套稳定接口：

- 控制命令：大车速度、小车位置、吊具高度、锁箱/解锁、急停。
- 状态数据：三个机构的位置和速度、吊具状态、载荷、限位、故障码。
- ROS2 标准数据：
  - `/joint_states`
  - `/tf`
  - `/rtg/command`
  - `/rtg/status`
  - `/rtg/alarm`
  - `/spreader/lock`
- 为以后真实 PLC 数据预留 ROS2 与 OPC UA/MQTT 的适配层。
- 加入时间戳和设备 ID，保证真实港口状态能稳定映射到数字世界。

## 第四步：实时生成集装箱

你已经把地面的静态箱子移除了，这是正确方向。后续需要：

- 建立一个轻量化集装箱 USD 原型。
- 使用 USD Instance/Point Instancer 批量生成。
- 数据字段包含箱号、尺寸、颜色、堆场贝位、层高、状态和目标位置。
- 根据实时堆场数据增删或移动实例。
- 被吊箱从实例系统切换到独立刚体，落箱后再归还实例系统。

这能显著降低 RTX 5060 8GB 的显存压力。

## 第五步：环境与性能

目前已有白天天空、太阳光和较真实的海面：

- [environment.usda](</D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scenes/environment.usda>)
- [天空纹理](</D:/dnt/project/Smart Port RTG Digital Twin/omniverse/materials/textures/day_harbor_sky_360.png>)
- [海面法线纹理](</D:/dnt/project/Smart Port RTG Digital Twin/omniverse/materials/textures/harbor_water_normal.png>)

仍需补充：

- 夜晚照明和港机工作灯。
- 雨天、雾天、湿地反射。
- 白天/夜晚/雨雾预设切换。
- Payload 按区域加载。
- 重复 RTG、车辆、集装箱使用实例。
- 为远处港区建立 LOD 或代理模型。
- 建立性能预算：显存、帧率、启动时间和场景对象数量。

建议以 RTX 5060 8GB 为基准，先保证实时模式稳定达到约 30 FPS，并给系统和后续传感器留出显存空间。

## 第六步：传感器和 AI

传感器建议按最小闭环逐步增加：

- 吊具俯视相机：箱角和锁孔对位。
- 载荷/称重传感器。
- 大车、小车和起升限位。
- 防碰撞距离传感器。
- 后续再增加激光雷达、OCR 和箱号识别。

AI Agent 应放在控制系统稳定之后：

- 第一阶段：规则调度，作为基准。
- 第二阶段：RTG 作业路径与任务排序优化。
- 第三阶段：视觉对位和异常检测。
- 第四阶段：电机、制动器、钢丝绳等故障预测。
- 第五阶段：调度 Agent 与维护 Agent 协同。

## WPF 项目的定位

PDF 写的是 Unity/Web，但你的 WPF 项目也可以继续使用。建议：

- Omniverse/Isaac Sim 负责三维场景、物理仿真和传感器。
- ROS2 或服务层负责实时数据。
- WPF 负责设备状态、报警、任务列表、趋势图和操作界面。
- 三维画面优先使用 Omniverse 视频流或远程渲染，不建议再把整个高精度港区转成 OBJ 塞进 WPF。

最合理的下一项工作是：**提取一台标准 RTG，建立大车、小车、吊具三个可控制关节，并在 Isaac Sim 中完成一次手动吊箱循环。**完成这一步后，再接 ROS2。
更详细的 Omniverse 工作流见 [`omniverse/README.md`](omniverse/README.md)。



PLC / TOS / 任务系统
        ↓
RTG Twin Service
        ↓
Omniverse Extension
        ↓
RTG / 集卡 / 箱区实时渲染

AI 智能体
        ↘
         任务规划 / 风险预警 / 翻箱建议 / 可视化解释
