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
