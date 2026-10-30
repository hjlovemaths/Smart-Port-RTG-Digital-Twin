# RTG 的 ZMQ PLC 协议接口

本接口以最新《RTG-PLC服务读写指令通信协议.docx》（修订记录到 V1.37）为控制命令基准，使用
`match/cmd/value/CRC/time` 指令和
`match=PLC/msg_type/content` 状态格式，控制当前 USD 数字 RTG 并发布场景参数。
当前接入范围为 RTG 三轴运动、伸缩吊具、吊具旋转/左右微移、控制状态，以及可选的箱体碰撞、锁箱和 PhysX 落箱；不会向真实 PLC 下发命令。
PLC 参数以 RCMS 接收字段为准，同时保留原 Word 协议中的参数名以兼容现有客户端。
完整字段差异、类型和未模拟项见 [RCMS 字段对照](RTG_PLC_RCMS_FIELDS.md)。
最新协议全部 31 个控制命令的实现范围和缺口见 [控制命令核对表](RTG_PLC_COMMAND_AUDIT.md)。

## 端口与连接方向

| 端口 | 本项目服务端 | 第三方客户端 | 内容 |
|---|---|---|---|
| 5515 | ZMQ **SUB bind** | ZMQ **PUB connect** | 控制指令 |
| 5516 | ZMQ **PUB bind** | ZMQ **SUB connect**，订阅空主题 | 周期参数、指令结果及异常 |
| 127.0.0.1:48720 | 普通 TCP 服务 | Kit 场景适配器 | 内部场景链路，第三方无需接入 |

原文档的 5515 发布状态、5516 接收指令，与本次需求相反。默认按本次需求实现，
可配置切回原方向。5516 是持续发布流，使用 SUB 接收，不使用 REQ/REP。

```text
第三方 PUB ──5515──► ZMQ 服务 ──本机 TCP──► Kit 主线程 ──► RTGController
第三方 SUB ◄─5516─── ZMQ 服务 ◄─实际位置──── Kit 主线程 ◄── USD 场景
```

命令支持单帧 UTF-8 JSON 或 `[topic, JSON]` 两帧消息，状态始终为单帧 UTF-8 JSON。
单条消息最大 64 KiB。外部端口默认绑定 `0.0.0.0`，内部场景链路仅绑定本机。
端口占用会明确报错，不会关闭现有进程或自动换端口。

## 启动

### 通过 Agent 启动

更新代码后完整重启 Kit，在 Smart Port Agent 面板中输入：

> 启动 RTG 的 ZMQ PLC 服务，并连接当前 Kit 场景适配器。

Agent 调用 `control_plc_zmq(action="start")`，在后台启动固定的服务脚本并连接适配器。
返回操作 ID 时表示正在启动，需查询 `get_operation_status` 确认完成。
服务重复启动会复用 Agent 已启动的进程；不会自动发送控制使能或运动指令。
若终端或之前的 Kit 会话已启动兼容服务，`start` 会先连接并复用，不再创建占用相同端口的第二个服务。
状态分别报告网络服务、适配器连接、`scene_ready` 和 `armed`。必须打开含 RTG 的
`smart_port.usda` 才有位置数据；Agent 不会替换当前场景。

| 口令 | 接口 |
|---|---|
| 查看 ZMQ 服务、适配器和控制合状态。 | `control_plc_zmq(action="status")` |
| 连接终端里已经启动的 ZMQ PLC 服务。 | `control_plc_zmq(action="connect")` |
| 断开 ZMQ 的 Kit 适配器，保留服务。 | `control_plc_zmq(action="disconnect")` |
| 停止 ZMQ PLC 服务并断开 Kit 适配器。 | `control_plc_zmq(action="stop")` |
| 开启 ZMQ 抓放箱物理模式。 | `control_plc_zmq(action="physics_on")` |
| 关闭 ZMQ 抓放箱物理模式。 | `control_plc_zmq(action="physics_off")` |

需要同时开启时可以说：“启动 ZMQ 服务并开启 ZMQ 箱体碰撞、抓放箱物理模式。”
Agent 先完成 `start` 并确认 `scene_ready`，再调用 `physics_on`，后者会显示确认栏，
通过与 ZMQ 相同的校验申请本地控制合、开启 `sim_physics=1`，不发送任何轴速度。
请保持时间轴暂停。成功以 `adapter.physics_enabled=true` 为准；
此功能使用 PhysX 箱体代理，不调用旧的 `control_rtg_physics.activate_physics` 整机控制器。

`stop` 会停止三轴、释放控制合，并仅关闭由当前 Kit Agent 启动的服务进程。
终端手动启动的服务需在原终端停止，Agent 不会关闭外部进程。
Agent 管理的服务在 Kit 正常退出时清理；面板关闭后服务仍可运行。
日志在 `outputs/plc_zmq/agent_service.log`。

Agent 会从本机 Python 路径中查找已安装 `pyzmq` 的解释器，后台启动时隐藏终端窗口。
也可在 `services/rtg_plc_zmq_service.config.json` 增加固定配置，例如
`"service_python": "D:/Software/anaconda3/python.exe"`，或设置环境变量
`SMARTPORT_PLC_PYTHON`。模型接口不接收解释器路径或自定义命令。
如果 5515/5516 已被手动启动的服务占用，使用上面的“连接已有服务”口令。

### 通过终端启动

在项目根目录 PowerShell 中运行：

```powershell
python -m pip install -r services/requirements.txt
python services/rtg_plc_zmq_service.py
```

在 Kit 打开 `omniverse/scenes/smart_port.usda`，在 Script Editor 整块执行：

```python
import sys
sys.path.insert(0, r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts")
from rtg_plc_scene_driver import start_rtg_plc_driver
start_rtg_plc_driver()
```

示例路径改成实际项目目录。Kit 适配器仅用标准库，无需给 Kit 安装 `pyzmq`。
网络线程只传 JSON，USD 查询和修改在 Kit 主线程执行，不需要按 Play。
启动默认控制关闭，先提供参数；外部程序发送 `ctr_sw_set=1` 后才允许运动。

若 TwinState receiver、虚拟 RTG 仿真、手柄、Agent 运动或其他作业任务占用 RTG，
申请控制会被拒绝。先明确停止占用者，再申请控制。不要把本服务反馈重新接到
TwinState receiver 来同时驱动同一 RTG。更新已有应用后须重启 Kit，加载新的控制权检查；
适配器检测到旧版本 Agent 控制模块时会拒绝启动并提示重启。

查看和停止适配器：

```python
from rtg_plc_scene_driver import get_rtg_plc_status, stop_rtg_plc_driver
print(get_rtg_plc_status())
# 需要停止时执行：stop_rtg_plc_driver()
```

已运行的 Kit 在更新协议代码后，需要刷新缓存的适配器（保留当前场景和位置）：

```python
import importlib
import rtg_plc_protocol as protocol
import rtg_plc_scene_driver as driver
driver.stop_rtg_plc_driver()
importlib.invalidate_caches()
importlib.reload(protocol)
importlib.reload(driver)
driver.start_rtg_plc_driver()
```

刷新后控制合默认为关闭，需要重新发送 `ctr_sw_set=1`。这一步不会开启远端 ZMQ
监听进程，也不需要重复启动已占用 5515/5516 的服务。

`ctr_sw_set=0` 停止三轴，保留参数订阅；普通模式会释放控制权。物理模式需先落箱、解锁并发送 `sim_physics=0` 才允许其他驱动接管。Agent 的“停止 RTG 运动”
也会撤销此适配器的控制合状态。

## 第三方客户端示例

另开 PowerShell 订阅参数：

```powershell
python services/rtg_plc_zmq_client.py --watch
```

小车按正方向 0.5 m/s 运动 2 秒，结束后停车并释放控制权：

```powershell
python services/rtg_plc_zmq_client.py --demo --trolley 0.5 --seconds 2
```

大车按正方向 0.3 m/s 运动 2 秒：

```powershell
python services/rtg_plc_zmq_client.py --demo --gantry 0.3 --seconds 2
```

另一台电脑使用 `--host <运行项目的电脑IP>`。发送单条指令并等待结果，例如停止：

```powershell
python services/rtg_plc_zmq_client.py --send '{"match":"CTR","cmd":"ctr_sw_set","value":0,"CRC":"0"}'
```

PUB/SUB 连接需要时间传递订阅，不能创建 PUB 后立刻发一条指令就退出。
示例客户端等待连接后发送，按 `cmd/command_time` 关联结果；演示每 100 ms 刷新速度。
实际运动结果应从后续参数流判断，指令应用结果不表示已经到达目标。
演示会输出运动前后位置和实际位移，不会只根据 `accepted=true` 宣称设备移动。
`--watch` 只查看参数。大车 0.3 m/s 持续 2 秒的期望位移约 0.6 米，远景中不容易观察；
可保持 Kit 窗口显示并使用 `--demo --gantry 0.5 --seconds 6` 观察约 3 米的位移。

## 运动指令

控制合：

```json
{"match":"CTR","cmd":"ctr_sw_set","value":1,"CRC":"0","time":"1793170000000"}
```

小车和起升速度，单位 m/s：

```json
{"match":"CTR","cmd":"vel","value":[0.5,0.2],"CRC":"0","time":"1793170000100"}
```

RCMS 大车速度（正值向左、负值向右）：

```json
{"match":"CTR","cmd":"bigcarvel","value":[2.0,2.0,2.0],"CRC":"0","time":"1793170000200"}
```

三项依次为手柄速度、大车 1 给定速度、大车 2 给定速度，单位 m/s。
单轴数字模型按第二项执行，要求第二、三项相等；不支持双侧差速纠偏时会明确拒绝。
第一项作为手柄给定反馈，不将三项相加。正值向左，对应场景工程距离减小。

先发送 `ctr_sw_set=1`，然后每 100 ms 左右刷新速度。RCMS 的“启用写功能”只建立
发送通道，并不会发送控制合。只点击一次速度按钮时，默认最多持续约 0.6 秒即超时停车。
`heartbeat=1` 仅确认连接，不代替速度刷新。停止大车：

```json
{"match":"CTR","cmd":"bigcarstop","value":null,"CRC":"0","time":"1793170000300"}
```

原 Word 协议的大车控制仍可使用：

```json
{
  "match":"CTR","cmd":"set_ctrl_cmd",
  "value":{"switch":1,"speed":[0,0],"bigCarSpeed":0.3,"toolAction":0,"midLock":0},
  "CRC":"0","time":"1793170000200"
}
```

- `vel.value[0]`：小车速度，正值增加 `little_car_pos`，负值减小。
- `vel.value[1]`：起升速度，默认正值上升，负值下降。原文没有定义速度正负，
  这里明确采用此约定；`hoist_positive_up=false` 可反转输入起升速度。
- `switch=0` 执行 `speed=[小车,起升]` 并停止大车；`switch=1` 执行
  `bigCarSpeed` 并停止小车和起升。大车正速度增加工程坐标，负速度减小。
- 按 RCMS 语义，`vel` 只更新小车和起升，`bigcarvel` 只更新大车。
  各轴分别检查速度刷新时间，小车命令不会使过期的大车命令继续执行。
  `vel=[0,0]` 停止小车和起升，`bigcarstop` 停止大车；`ctr_sw_set=0` 停止三轴并释放控制权。
- 坐标沿用现有 RTG 工程米值和标定。小车集卡停位的平坦标定区间通过连续目标积分跨越。
- 默认速度上限为大车 3、小车 3、起升 1 m/s。非有限数和超限速度拒绝。
  位置受现有软限位约束。每个积分子步最多 0.1 秒，低帧率时分步累计有效时间，
  不截掉剩余帧时间；帧间隔超过看门狗时间时停车，不追补长期挂起的运动。

## 已接入命令范围

| cmd | value | 数字场景行为 |
|---|---|---|
| `ctr_sw_set` | 0／1 | 控制断／合，检查控制权 |
| `vel` | `[小车,起升]` | 独立更新小车和起升；正值向前／向上 |
| `bigcarvel` | `[手柄速度,大车1速度,大车2速度]` | 独立更新大车；正值向左，双侧给定须相等 |
| `bigcarstop` | null | 停止大车，不改变小车和起升 |
| `heartbeat` | 1 | 心跳确认，不申请控制，也不续期速度 |
| `sim_physics` | 0／1 | 本项目扩展：关闭／开启箱体 PhysX、接触及锁箱联锁；默认关闭 |
| `tool_action` | 11/10/21/20 | 吊具左旋/停止/右旋/停止，绕四个锁脚的中心旋转 |
| `tool_move` | 11/10/21/20 | 吊具左移/停止/右移/停止，不改变小车工程位置 |
| `tool_reset` | 11/21 | 分别平滑回到旋转零位/平移零位，可被对应停止指令中断 |
| `move` | `[小车,起升,旋转码,微动码,大车1,大车2]` | 三轴与吊具旋转/微移组合控制；旋转/微动均支持 10/11/20/21，双侧大车须相等 |
| `estop` | null | 虚拟紧停三轴并释放控制，锁定直到显式复位；同场景链路重连不解除锁定 |
| `manual_mc` | 0／1 | 1 选择已有远程手动模式并停车，0 不切换；不自动控制合 |
| `comm_test` | 0..2147483647 整数 | 原值回传通信测试字段，不改变运动 |
| `autocorrection` | 0／1 | 仅虚拟纠偏使能状态；命令名允许文档中的尾空格 |
| `bigcar_correction` | 0／1 | 仅虚拟纠偏控制状态，不模拟车体偏航 |
| `set_bypass` | 10/11/20/21/30/31 | 记录通用/着箱/吊具旁路状态，不绕过场景软限位 |
| `set_ctrl_cmd` | 上述对象 | 切换大车／小车起升模式；`toolAction/midLock` 只能为 0 |
| `tool_size` | 1／2 | 实际伸缩吊具切换 20／40 英尺，需三轴停止 |
| `tool_sw_set` | 0／1 | 虚拟油泵开合状态 |
| `wheel_clamp` | 0／1 | 虚拟夹轮器松开／夹紧，夹紧会停止大车 |
| `lock` | 0／1 | 普通模式记录虚拟锁状态；物理模式检查接触后建立／解除真实 PhysX 固定约束 |
| `set_big_car_left` | 0／1／2 | 负方向无限制／禁止／限速 |
| `set_big_car_right` | 0／1／2 | 正方向无限制／禁止／限速 |
| `set_big_car_move_mode` | 1／2／3 | 大车双向禁止／微动／正常速度 |
| `plc_test_delay` | 0／1 | 回传 `test_delay_state`，可用时间戳关联结果 |
| `reset_one_key` | null | 停止并释放控制权，清除虚拟紧停；不清空库存或重置场景 |

45 英尺、倾转、中锁、导板、大梁俯仰、摄像头手柄操作、硬件标定、记忆位置及
其他未列出的命令均明确拒绝。校验失败或未知命令会清零当前速度，保留场景位置。
本接口没有把整份协议中的硬件功能全部模拟出来。

`move` 任一分量无效时整条拒绝。例如
`[0.5,0,10,10,0.3,0.3]` 同时向前动小车和向左动大车；`[0.5,0,11,11,0.3,0.3]`
还会同时左旋和向左微移。虚拟状态命令返回 `effect=virtual_state`，
不把虚拟泵、锁、纠偏或旁路状态说成已完成真实机构动作。

### 吊具旋转与左右微移

先发送 `ctr_sw_set=1`，RCMS 现有按钮即可直接使用，无需修改按钮命令。
旋转为俯视下绕世界竖直轴左旋（逆时针）/右旋，左右微移沿吊具横向，当前主 RTG
左移对应世界 X 减小。与大车、小车和起升轴独立，保持工程位置标定；不模拟俯仰和侧倾。

默认旋转限位 **±5°**，速度 **1°/s**；平移限位 **±0.3 米**，速度 **0.05 m/s**。
`motion.spreader_yaw_limit_deg/spreader_shift_limit_m/spreader_yaw_speed_deg_s/spreader_shift_speed_mps`
可配置范围及速度。模型的非等比缩放已经补偿，旋转不会拉伸箱体或绕港区原点公转。
绳索上端保持在小车，下端跟随吊具。已经通过场景抓箱接口绑定的箱子也跟随微调；
未启用 `sim_physics` 时，`lock=1` 仍仅改变虚拟锁状态。物理模式下，锁住的箱子随吊具旋转和微移。

`tool_action`、`tool_move` 为按钮式启动/停止：一次启动持续到停止、对应限位或
`spreader_jog_timeout_s`（默认 10 秒）。两个停止码 10/20 均停止对应机构，
只停止旋转不会停止平移，反之亦然。再次启动可刷新对应机构的超时。
`tool_reset` 一次触发平滑回零，使用相同超时；不会瞬移到零点。
`move` 为流式组合指令，所有运动轴仍须约 100 ms 刷新，默认 0.6 秒超时。
控制断、紧停、断线、控制权冲突或 Kit 帧挂起都会停止微调和回零，保留当前位置。

5516 参数流新增 `content.tool_l_rotate/tool_r_rotate`（旋转命令反馈）和
`content.to_mid_state`（旋转已在零位）。实际角度、偏移、速度、回零状态和限位在
`digital_twin.spreader_trim` 中；不将单位未明确的 RCMS `rotate_pos` 伪装成角度。
旋转/微移的成功确认是 `effect=digital_scene`，表示实际驱动数字模型。

更新后需重启 Kit 和 ZMQ 服务，重新控制合；已在终端启动的服务需在原终端重启。

只移动小车使用 `vel=[2,0]`。`[2,2]` 的第二项是起升速度，会超过默认 1 m/s
上限而整条拒绝。Kit 当前起升到达 18 m 软上限时，合法的正向上升速度也不再提高吊具。

文档中的 CRC 仅为占位，没有算法说明；当前仅接受数字 `0` 或字符串 `"0"`，
省略时按占位处理，不宣称已校验 CRC。`match` 支持文档中的 `CTR/UI/CALI/custom`，
指令是否可用仍由 `cmd` 的允许清单决定。

## 通过 ZMQ 开启箱体碰撞和抓放箱

`sim_physics` 是本项目扩展命令，默认 **关闭**，不会改变旧客户端的虚拟锁行为。
更新后完整重启 Kit，再启动服务及适配器。Kit 必须具备 `omni.physx`，并保持时间轴
**暂停，不按 Play**；适配器单独步进箱体物理场景，避免原模型动画覆盖 PLC 坐标。

先在 RCMS 控制合并停止所有轴、旋转和微移，再发送：

```json
{"match":"CTR","cmd":"sim_physics","value":1,"CRC":"0"}
```

也可以直接在项目根目录执行（此命令不会替你控制合）：

```powershell
python services/rtg_plc_zmq_client.py --physics on
```

正常 ACK 为 `accepted=true`；从 5516 的参数消息确认
`digital_twin.physics.enabled=true` 和 `backend="PhysX"`。
缺少 PhysX、时间轴正在播放、轴未停止或场景不匹配时会明确拒绝。

抓箱顺序如下，所有消息沿用 `match/cmd/value/CRC/time` 格式，`time` 使用发送时的毫秒时间戳。

| 顺序 | cmd | value | 判断与效果 |
|---|---|---|---|
| 1 | `ctr_sw_set` | `1` | 申请控制合 |
| 2 | `sim_physics` | `1` | 开启物理模式，所有轴须已停止 |
| 3 | `tool_size` | `1` 或 `2` | 选择匹配的 20 或 40 英尺吊具 |
| 4 | `tool_sw_set` | `1` | 开吊具泵，闭锁联锁要求 |
| 5 | `vel` | `[0,-0.2]` | 缓慢下降；约每 100 ms 刷新。接触箱顶时拦停下降 |
| 6 | `vel` | `[0,0]` | 停止小车、起升；若大车或微调在动，也需分别停止 |
| 7 | `lock` | `1` | 四锁脚均着箱、锁点对齐、箱型匹配才建立固定约束 |
| 8 | `vel` | `[0,0.2]` | 起升，箱体随吊具运动；大小车及微调同样带箱 |

着箱查看 `content.drop_state=1`。详细四脚接触、锁点最大误差和箱型匹配在
`digital_twin.physics.contact` 中；仅着箱不保证对位合格，应以闭锁 ACK 和后续
`content.lock_state=1` 为准。锁脚垂直容差默认 0.015、水平对位容差 0.07，
均是当前模型的 **世界坐标米**，不是起升工程坐标米。通过实际渲染几何测量，
避免用工程高度猜箱顶；未对齐时使用小车、大车或吊具旋转/左右微移调整。

放箱时先移到目标上方，低速下降。带箱碰到下层箱面、地面或其他箱体时会拦停对应轴，
`digital_twin.stop_reason="collision_stop"`。上升或离开接触面仍被允许。
四个箱底角都得到支撑后，`digital_twin.physics.landed=true`，此时停止全部运动，
发送 `lock=0` 解锁。悬空解锁会拒绝，不会把箱子从半空放掉；停稳解锁后箱体由
PhysX 重力及碰撞自行落稳，吊具可以空载上升。放在集卡上时使用当前箱底生成的车板支撑面。

5516 中新增的物理反馈如下：

| 字段 | 含义 |
|---|---|
| `content.drop_state` | 空载四脚着箱，或带箱四底角均受支撑时为 1 |
| `content.lock_state / unlock_state` | 物理约束的实际开闭状态，退出虚拟字段清单 |
| `content.collision_state` | 本模式新增字段：发生过碰撞拦停时为 1；关闭物理模式清除 |
| `digital_twin.physics.carried_container` | 正在吊运的 USD 箱体路径 |
| `digital_twin.physics.contact` | 接触箱体、四脚接触、锁点误差及箱型匹配 |
| `digital_twin.physics.landed` | 带箱时的落箱支撑判断 |
| `digital_twin.physics.collision` | 最近一次拦停的轴和障碍物 |
| `digital_twin.physics.last_release` | 最近一次解锁箱体及其支撑对象 |

物理模式不会因控制断、看门狗超时或 ZMQ 断线自动解锁，仍会保持负载和处理已释放箱子的沉降。
因此 RTG 控制权会保留至物理模式关闭。带箱时不能切换箱型或关闭物理模式；先落箱解锁，
保持控制合并停止各轴，再发送 `sim_physics=0`（或 `--physics off`），最后 `ctr_sw_set=0`。
关闭适配器或切换场景会清理物理场景、冻结最后可见姿态。

碰撞体从当前 `/World/LiveContainers` 的可见箱体生成，最新任务重建箱区图后会刷新。
若新任务重建的是正在锁住的箱体，适配器会报错停车，避免吊起替换后的另一只箱子；
应停止适配器并核对任务后重新连接。落箱后的会话姿态会保留，直到该箱体被最新任务重建；
本功能暂不把自由摆放位置反写为库存 JSON 的排、层或作业完成状态。

实现范围：箱体使用盒形刚体代理、重力、堆叠碰撞和固定锁箱约束；大车、小车、起升继续沿用
已标定的运动控制，并在运动前做连续碰撞检测。仅为附近箱体创建碰撞代理。
这不是整机全动力学：绳索仍为视觉模型，不含绳索弹性/摆动、结构变形、锁销内部运动、
四角载荷或硬件安全认证。`weight/load` 不因设置了模拟质量而冒充称重反馈。
空载且没有动态箱体时跳过 PhysX 步进，箱顶接触和防穿透仍由运动前的连续检测处理；
锁箱或释放箱体后继续进行重力和约束求解。

## 参数和结果

5516 默认以 20 Hz 发布参数。主要字段示例（实际消息还有版本描述和辅助状态）：

```json
{
  "match":"PLC","time":"1793170000300","msg_type":1,
  "version":{"product":"smartport_rtg_plc_simulator","version":"0.1.0"},
  "content":{
    "ctr_on_state":1,
    "bigcar_elec_distance":261.7,"bigcar_engine_distance":261.7,
    "bigcar1_vel_fb":0.0,"bigcar2_vel_fb":0.0,
    "smallcar_pos":5.2,"smallcar_vel":0.5,
    "lifting_pos":10.0,"lifting_vel":0.2,
    "rope_pos":8.0,"rope_vel":-0.2,
    "tool_20f":0,"tool_40f":1,"tool_45f":0,
    "lock_state":0,"unlock_state":1,"total_height":1800,"CRC":0
  },
  "digital_twin":{
    "scene_connected":true,"simulated":true,"hoist_height_m":10.0,
    "rope_pos_mode":"rope_length","hoist_positive_up":true,"stop_reason":""
  }
}
```

位置来自场景控制器，速度取实际相邻采样位置差。默认 `rope_pos` 表示绳长，
等于 `hoist_reference_m - 吊具工程高度`；`rope_vel` 向放绳方向为正，上升时为负。
`digital_twin.hoist_height_m` 始终为工程高度。`total_height` 使用厘米，18 米即 1800。

RCMS 使用 `bigcar_elec_distance/bigcar_engine_distance`（大车米数）、
`smallcar_pos/smallcar_vel`（小车米数／米每秒）、
`lifting_pos/lifting_vel`（起升高度／高度变化率，向上为正）。双侧大车位置来自
同一条模拟工程坐标，未模拟双侧 GNSS 偏差。`lifting_pos` 始终是高度，
不受旧协议 `rope_pos_mode` 配置影响。原 `big_car_pos/little_car_pos/rope_pos` 仍可读取。
RCMS 的 `bigcar1_vel_fb/bigcar2_vel_fb/bigcar_handle_vel` 正值向左；旧字段
`big_car_vel/big_car_handle_value` 正值为工程坐标增加（向右），两套反馈符号相反。
RCMS 原始整型反馈 `smallcar_vel_fb/lifting_vel_fb` 的 PLC 缩放未定义，不将米每秒
小数强转填入；独立制动器、GNSS、称重、防撞等未建模信号也不编造。
当前尚未输出 `current_field_num/current_bay_num`；RCMS 三维按场号和贝号定位，
三轴数字参数正常接收不代表其三维大车定位已完成对接。

消息还提供方向信号、速度反馈、限位、夹轮／制动及大车左右限制状态。
`digital_twin.virtual_fields` 标明虚拟控制状态。没有实际称重、着箱传感器的字段
（如 `weight`，普通模式的 `drop_state`）不编造数值，未支持内容见 `unsupported_fields`。

| msg_type | 含义 | 字段 |
|---|---|---|
| 1 | 场景参数 | `content`、样本时间 `PLC_long_time`、`digital_twin` |
| 2 | 指令已应用 | `content.accepted=true`，用 `cmd/command_time/request_id` 关联 |
| 3 | 指令拒绝、场景不可用或异常 | `exp_type/exp_desc`，拒绝结果含 `accepted=false` |

场景未打开、Kit 未连接或参数已过期时只发布异常，不发布假实时坐标。
`msg_type=4` 的真实硬件故障列表未在数字场景中模拟。

## 超时和配置

配置文件：`services/rtg_plc_zmq_service.config.json`。

- 默认速度命令 0.6 秒不刷新即停车，只有速度命令续期，查询和重复控制合不续期。
  超时停车保留控制合；让其他驱动接管前需发送 `ctr_sw_set=0`。
- 低帧率下按完整帧间隔积分，每步最多 `motion.max_dt`（默认 0.1 秒），
  不会将整帧运动时间截成 0.1 秒。帧间隔超过看门狗时间时停车，避免恢复后突然跳动。
  `digital_twin.tick_dt_s/integration_dt_s` 可用于检查帧间隔与本帧运动积分时间。
- 断线、切换场景或关闭适配器会停车并撤销控制合，重连后必须重新申请控制。
- Kit 超过 `scene_stale_s` 没有更新参数时拒绝新命令；内部过期指令也不会恢复后重放。
- 原有 TwinState 服务的远程 PLC 配置不变，不能把它改成本服务形成反馈回路。
- 可调整 `publish_hz`、`scene_stale_s`、`motion.watchdog_s`、速度上限、参考高度及
  `rope_pos_mode=rope_length/height`。修改后重启服务，Kit 会自动重连并释放旧控制权。

切回原文档端口方向：

```powershell
python services/rtg_plc_zmq_service.py --command-port 5516 --state-port 5515
python services/rtg_plc_zmq_client.py --command-port 5516 --state-port 5515 --watch
```

## 验证

- `tests/test_rtg_plc_protocol.py`：语义、限位、校验、超时、方向限制和标定区间。
- `tests/test_rtg_plc_zmq_service.py`：真实 ZMQ／TCP、错误反馈、失联、过期指令和重连。
- `tests/test_rtg_plc_usd.py`：完整 USD 模型下的三轴运动、绳长、20／40 英尺吊具与控制权。
  此文件需要 Kit USD Python，普通 Python 会跳过。
- `tests/test_rtg_plc_contact.py`：高速穿透、接触面滑动和离开接触面的连续检测。
- `tests/test_rtg_plc_interaction_usd.py`：真实 RTG 几何的着箱、对位、锁箱、负载防撞、解锁联锁和任务更新。
- `tests/plc_physx_smoke.py`：在 Kit 暂停时执行 `run(stage)`，以独立不可见代理验证真实 PhysX
  重力落地、固定约束起吊及解锁落箱堆叠，不移动 RTG 或保存场景。
