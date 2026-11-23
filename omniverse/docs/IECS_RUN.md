# 启动 IECS

在网页管理员会话中打开「IECS 账号」，设置调度员账号。该账号从
`/login` 登录后进入 `/iecs?spectator=1`，可看港区画面并操作 IECS 工作台。
点击「启动仿真」后选择流程并提交订单。「任务与载箱」中每条订单的「聚焦」可定位到指定订单。
也可在 Agent 输入「聚焦第 4 条任务」或「聚焦 IECS-00004」。外车还在候场时镜头先对准闸外候场点，入场后跟随该车。
继续前会检查场景，
「更早事件」按数据库顺序翻页。工作台运行在 Kit 的场景会话中，刷新页面可继续查看同一轮仿真。
港区画面下方居中的 Agent 输入框可用中文查询进度、箱位和设备状态，或切换到当前作业及指定集卡视角。
点击输入框上方的 Settings，填写公网 HTTPS 模型接口、模型名称和 API Key。
模型配置与对话随 IECS 当前登录会话保存。切换服务商时，请填写新服务商的 Key。
派单及流程控制使用工作台已有入口。

## 真实 IECS 调度

登录 IECS 工作台，选「真实 IECS 调度」并点「开启接入」。操作员继续使用
同一网页的三维画面和视角控制。真实模式由外部 IECS 下发设备任务和车辆
报到，数字港区执行动作并回传阶段，面板显示设备编号、箱号、实际归属和
收发消息。未完工的模拟任务需要先完成或复位。接入期间原模拟派单被锁定。

开启真实模式后，可在「数字场景速度」选择 1×、5×、10× 或 20×。倍率只作用于
数字设备与集卡的运动和仿真计时，IECS 心跳、报文时间戳、许可及回执仍按实际时钟。
展开「IECS 收发报文」可按方向查询本轮最近 100 条业务报文，查看请求与响应正文、
实际收发时间和 HTTP 状态。勾选「包含心跳」可同时查看最近 30 条心跳。报文随
本轮接入保留在内存中，关闭接入后清空。

首版在当前 6.6 船图上用 STS_01、主 RTG 和一辆指定内集卡作业。每轮接一条
20 尺卸船任务，源位须为 STS_01 可达的顶层结构箱，目标须为场景可达的
空闲第 1 排第 1 层。任务用 `oisVlocation` 绑定实际船图编号，以
`soaTcntrPos` 指定场箱位，支持 `3C0111`、`3C/001/R1/T1` 等格式。
当前船图是结构示意箱，三维箱号由 IECS 指令绑定。集卡在 Kit 路网中行驶，
IECS 在靠岸和靠场时分别发送粗停和精停确认。持续运行业务时需另接车队接口。
20 尺单箱固定放在集卡靠车头的前仓。岸桥与 RTG 的集卡停位按前仓校准，
集卡驶入堆场后，RTG 在目标贝位取箱、横移及落箱。
进出岸桥时按车道方向选择引桥，并根据当前场景中的岸桥支脚和车身包络检查路线。
候选通道受阻时改走其他可用引桥，全部受阻时在当前安全位置等待。

### 本机跑一单

默认数字设备入口为 `http://127.0.0.1:48731/process`，IECS 回调服务为
`http://127.0.0.1:48732`。开启接入后，在项目目录运行本地联调端：

```powershell
python services/iecs_test_peer.py
```

该脚本用面板给出的一个可用箱位走完整 HTTP 协议。运行过程可点「聚焦作业」。
任务结束后关闭接入，再次开启即可重跑。关闭会恢复数字场景，外部联调的事件
保存在 `outputs/iecs/external.sqlite3`。在收到拒绝许可或通信故障时，画面
停在当前状态，操作员可确认「结束本轮并恢复场景」，上游指令应独立核对。

### 配置实际联调端

在启动 Kit 的 PowerShell 会话中配置，以下为变量名及含义。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SMARTPORT_IECS_BIND` | `127.0.0.1` | Twin 监听及回执通告 IPv4 |
| `SMARTPORT_IECS_PORT` | `48731` | Twin 设备入口端口 |
| `SMARTPORT_IECS_CALLBACK` | `http://127.0.0.1:48732` | IECS 回调服务 HTTP(S) 源地址 |
| `SMARTPORT_IECS_PEER` | 回调地址的 IPv4 | 允许投递到 Twin 设备入口的来源 IPv4 |
| `SMARTPORT_IECS_QC` | `STS_01` | 实际 IECS 使用的桥吊编号 |
| `SMARTPORT_IECS_RTG` | `RTG` | 实际 IECS 使用的 RTG 编号 |
| `SMARTPORT_IECS_VEHICLE` | `ITT-001` | 实际 IECS 使用的车辆编号 |
| `SMARTPORT_IECS_TRUCK_NUMBER` | `1` | 车辆编号对应的场景集卡，1–20 |
| `SMARTPORT_IECS_TOKEN` | 空 | 双方约定的可选 `X-IECS-Token` 请求头 |

多数请求走 `POST /process`。设备消息回调到预配置的 IECS 服务，
桥吊放箱申请用独立的同步 `POST /landBoxApply`。本轮协议顺序如下。

1. IECS 下发桥吊 `114`，Twin 回 `115`。IECS 用 `120` 报到，数字集卡驶向岸边。
2. 岸桥交接点就绪后，IECS 用 `123` 报粗停，Twin 回 `126`、`117`。IECS
   再用同一引导序号报精停，桥吊取箱并向 IECS 申请 `173` 放箱许可。
3. 放箱及抬升完成后，Twin 用 `116` 分别上报可驶离和已执行。收到 `211` RTG
   任务及 `219` 车辆报到后，集卡载箱驶往指定贝位。
4. RTG 用 `220/221/214` 完成对位，`234/235` 确认 RTG 换贝后执行落箱。
   Twin 用 `213` 上报实际落位，单独发送 `225` 放行并等待 `226`，再让空车归位。

桥吊 `129` 和 RTG `215` 周期上报，RTG 经纬度沿用当前港区演示坐标。实际接入
应先确认双方编号、箱位编码、对位报文、回调响应及坐标标定。此功能使用 Kit 内的
数字设备，运行只有场景会话状态，重启后需重新联调。实机 PLC 和 TOS 关单属于独立接口。

## 模拟调度

「混合作业（5 单并行）」会一次提交五种标准流程。也可以连续提交单个流程。
同一时刻最多派出 5 辆车，共用 STS_01 和 RTG。路线、作业车道或设备冲突时，
车辆就地或在闸外候场，条件满足后自动继续。订单完成事件记录派车到完工的
仿真耗时及其中的等待时间，暂停期间不计入仿真耗时。

默认 `smart_port.usda` 的 6.6 船图使用可达的 20 尺结构箱进行演示，
岸桥及 RTG 吊具匹配到 20 尺。网页账号访问通过现有登录服务鉴权。

## Kit 原生面板

在已打开 `smart_port.usda` 的 Kit 中打开 Window → Script Editor，执行：

```python
import runpy
launch = runpy.run_path(r'D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/iecs_launch.py')['launch']
iecs = launch()
```

IECS 面板采用英文显示，提供五种标准流程和混合作业、暂停、继续、复位、1/5/10/20 倍率、
聚焦任务。点击流程提交一单，系统按可用车辆数量派车。
Mixed Workload 同时提交卸船、装船、进场落箱、提箱出场、场内转栈五单。
空闲内部车辆按距离选车。
外车使用 EXT 独立资源，进场任务从载箱车开始，出场任务载箱通过闸口。

统一入口按绝对路径加载新增模块，避免 Kit 导入缓存导致找不到文件。
重复执行 `launch()` 只显示现有面板，不重置任务。需要加载新代码并清空演示时，
明确执行 `iecs = launch(restart=True)`。这不是断点恢复：应用重启后不恢复旧任务。
Check Scene 检查箱子身份、位置、尺寸、朝向、载箱归属以及贝位元素；
Resume 在检查失败时拒绝继续，不擅自修复或清空现场。

按钮对照：Ship to Yard=卸船入场，Yard to Ship=堆场装船，
Gate In / Drop Box=外车落箱，Gate Out / Pick Box=外车提箱，
Yard Transfer=转栈，Mixed Workload=混合作业；Pause/Resume/Reset Demo
分别是暂停/继续/复位，Focus Task 聚焦当前任务。

本机接口：GET `http://127.0.0.1:48730/health` 和 `/state`；POST `/command`。
提交 JSON 示例：`{"action":"submit","flow":"unload","request_id":"demo-001"}`。
其他流程为 `load`、`gate_in`、`gate_out`、`transfer`。
控制 action 为 `mixed`、`pause`、`resume`、`speed`（value=1..20）、`reset`。
HTTP 202 表示已入队；结果和拒绝原因从 `/state` 的 events 获取。
同一 request_id 在本次会话内幂等。

事件和最近快照写入 `outputs/iecs/iecs.sqlite3`。
重启当前版本建立新的演示会话，尚不恢复执行中的任务；历史事件保留。
可通过 `iecs.close()` 关闭，恢复本次场景会话前的车辆状态。

当前已实现的是运动学演示版：01–20 真正移出/返回原停车位，编号随车。
Ship to Yard 使用 STS_01 的机械轴与吊具。6.6 场景使用顶层结构箱，
旧场景可继续从 `Model/ShipContainer179` 取箱。流程包括抬升净空、对准、下降、锁定、
带箱提升、横移到集卡、放箱、解锁和吊具抬升；收到动作完成后才放行车辆。
它使用场景箱位和现有吊具。每轮同一船箱只分配给一个订单，复位后可重演。
卸船入场同时接入主 RTG 的大车、小车、吊具升降与绳索：抬升净空、
对准集卡、下降锁箱、提升、横移至空闲箱位、落箱解锁并抬升。
目标位按场景实际可见库存检查，遇占用自动寻找空闲地面箱位。
岸桥装箱后沿当前车道方向到下一路口离场；折线路口使用连续圆滑转弯。
其他流程仍使用独立模拟库存，没有 PLC 反馈。
系统按路线预留通行空间。互不冲突的路线可同时放行，重叠路线等候。
路线为明确的有向折线，尚未进行重型车辆转弯扫掠或精确场景障碍验证；
不能把本版运行结果当作真实设备安全验证。

验收命令：

```powershell
python -m pytest tests/test_iecs_core.py -q -p no:cacheprovider
& 'D:/Software/kit-app-template/.packman/chk/python/3.12.13+nv3-windows-x86_64/python.exe' tmp/run_sts_usd_tool.py omniverse/scripts/verify_iecs_scene.py
```
# Real Yard to Ship demo

After reloading the IECS Python module, use **Reset Demo**, then **Yard to Ship**.
The adapter selects an accessible row-1 top-level 40ft container in the primary
RTG corridor. The real RTG puts it on the trailer; lane-directed transport takes
it to STS_01; the real STS places it on the existing ShipContainer179 stack.
No substitute container is spawned. All movement stays in a reversible session
layer, without changing the production inventory JSON. This is a single-order
kinematic demonstration, not a production vessel stow plan or full collision
planner. Mixed Workload reuses the vacated ship slot after unloading.

Gate In creates a declared incoming cargo instance on the external trailer at
the gate, then the real RTG unloads it into an unoccupied yard slot. Gate Out
and Yard Transfer select existing accessible top-level row-1 cargo, using the
real RTG to load the truck. Departed external cargo is hidden only after exit.
All of these are reversible visual demos, without production inventory writes.

Headless verification: run `verify_iecs_load.py` with the project's USD runtime.

