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

更详细的 Omniverse 工作流见 [`omniverse/README.md`](omniverse/README.md)。
