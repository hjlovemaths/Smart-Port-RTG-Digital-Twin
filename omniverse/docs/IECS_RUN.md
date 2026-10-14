# 启动 IECS

在已打开 `smart_port.usda` 的 Kit 中打开 Window → Script Editor，执行：

```python
import runpy
launch = runpy.run_path(r'D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/iecs_launch.py')['launch']
iecs = launch()
```

IECS 面板采用英文显示，提供五种标准流程和混合作业、暂停、继续、复位、1/5/10/20 倍率、
聚焦任务。点击流程提交一单；实际设备演示限制为一次一单，忙时拒绝重复提交。
Mixed Workload 自动依次运行卸船、装船、进场落箱、提箱出场、场内转栈五单，
面板显示后续流程。空闲内部车辆按距离选车。
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
Ship to Yard 已接入 STS_01 的原机械轴与吊具，从货船现有
`Model/ShipContainer179` 取箱。流程包括抬升净空、对准、下降、锁定、
带箱提升、横移到集卡、放箱、解锁和吊具抬升；收到动作完成后才放行车辆。
它不生成替代橙色箱或替代吊具。一次会话该船箱只能派发一次，再运行需要复位。
卸船入场同时接入主 RTG 的大车、小车、吊具升降与绳索：抬升净空、
对准集卡、下降锁箱、提升、横移至空闲箱位、落箱解锁并抬升。
目标位按场景实际可见库存检查，遇占用自动寻找空闲地面箱位。
岸桥装箱后沿当前车道方向到下一路口离场；折线路口使用连续圆滑转弯。
其他流程仍使用独立模拟库存，没有 PLC 反馈。
全路网一次只放行一辆行驶车辆，多个装卸步骤可以并发。
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

