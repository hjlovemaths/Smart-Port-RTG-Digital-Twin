# RTG Twin Service

这个服务用于替代 WPF 页面做运行时桥接：

```text
PLC ZMQ + 任务 ZMQ
  -> services/rtg_twin_service.py
  -> 127.0.0.1:48700 TwinState JSON
  -> Omniverse rtg_twin_state_receiver.py
  -> 正式 RTG 大车/小车/吊具/绳索运动
```

## 启动

推荐先在 PowerShell 启动服务：

```powershell
python services\rtg_twin_service.py --config services\rtg_twin_service.config.json
```

再打开 `omniverse/scenes/smart_port.usda`，在 Omniverse Kit Script Editor
执行：

```python
exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_twin_state_receiver.py", encoding="utf-8").read())
```

接收脚本会自动调用 `start_live_driver()` 并连接 `127.0.0.1:48700`，不要与
`rtg_simulation_driver.py` 同时运行。真实库存使用
`omniverse/data/yard_inventory.live.json`，不会读取随机仿真库存。

默认配置使用 RCMS 里当前看到的 RTG_220 端口：

- PLC：`10.142.20.1:5515`
- 任务/核心消息：`10.142.20.1:7065`
- 发布给 Omniverse：`127.0.0.1:48700`

## 离线检查

只生成一帧 TwinState：

```powershell
python services\rtg_twin_service.py --once
```

如果有测试 JSON：

```powershell
python services\rtg_twin_service.py --once --plc-json path\to\Yard_PLC_RTG_220.json --task-json path\to\Yard_TaskInfo_RTG_220.json
```

## 设计说明

任务消息是低频的，会缓存在服务内存里；PLC 消息是高频的，只刷新姿态。服务按固定频率发布：

```text
最新 PLC 姿态 + 当前任务缓存 = TwinState
```

收到任务完成 runinfo 后，服务会把当前任务标记为 `COMPLETED` 并继续发布，
让 Omniverse 有机会按 `taskId` 完成一次幂等库存结算。下一条任务消息会替换
这个终态任务；服务不再在完成消息到达时直接丢失任务身份。

任务中的六位 `bay_map` 会作为本次作业贝位的权威快照发送给 Omniverse。
跨贝翻箱如有 `target_bay_map` 也会转发为 `targetBayMap`；缺少必要快照时，
接收器会将库存同步状态标记为 `PENDING` 并阻止错误结算。

## 跳板机转发模式：本机主动拉取（推荐）

如果 PLC、任务、runinfo 只有跳板机所在网络能订阅到，而且跳板机不能主动访问你本机，就用这个方向：

```text
PLC / task / runinfo
  -> 跳板机：rtg_jump_relay_server.py 监听 48800
  -> 你本机：rtg_local_pull_collector.py 主动连接 10.10.10.25:48800
  -> 可选：127.0.0.1:48700 TwinState
  -> Omniverse
```

### 1. 跳板机先启动 relay server

把 `services` 目录拷到跳板机，并安装依赖：

```powershell
pip install -r services\requirements.txt
```

然后在跳板机运行：

```powershell
python services\rtg_jump_relay_server.py --listen-host 0.0.0.0 --listen-port 48800 --plc-host 10.142.20.1 --plc-port 5515 --core-host 10.142.20.1 --core-port 7065
```

### 2. 本机主动连接跳板机

只接收、打印任务和 runinfo，并保存三路消息：

```powershell
python services\rtg_local_pull_collector.py --jump-host 10.10.10.25 --jump-port 48800
```

如果要同时转成 Omniverse 可消费的 TwinState：

```powershell
python services\rtg_local_pull_collector.py --jump-host 10.10.10.25 --jump-port 48800 --publish-twin-state
```

如果只是给本机 WPF 调试界面也开一份同样的数据流，再加：

```powershell
python services\rtg_local_pull_collector.py --jump-host 10.10.10.25 --jump-port 48800 --publish-twin-state --publish-wpf-debug-state
```

此时：

- Omniverse：`127.0.0.1:48700`
- WPF 调试：`127.0.0.1:48701`

本机日志默认保存到：

```text
services\logs\pulled_messages
```

默认端口：

- PLC：`5515`
- task/runinfo：`7065`
- 跳板机 relay：`48800`

### 3. Omniverse 接入

如果本机 collector 启动时带了 `--publish-twin-state`，Omniverse 里 receiver 仍连接：

```text
127.0.0.1:48700
```

也就是说，Omniverse 不需要知道跳板机地址，只看本机 TwinState。

WPF 调试界面如果启动时带了 `--publish-wpf-debug-state`，连接：

```text
127.0.0.1:48701
```

这个端口发送的是同一份 TwinState JSONL，每行一个 JSON。

## 跳板机主动推送模式（备选）

只有当跳板机可以主动访问你本机端口时，才用这个旧方向：

```text
PLC / task / runinfo
  -> 跳板机：rtg_jump_forwarder.py
  -> 你本机：rtg_local_collector.py 监听 48800
  -> 可选：127.0.0.1:48700 TwinState
  -> Omniverse
```

### 1. 本机先启动接收端

```powershell
python services\rtg_local_collector.py --listen-host 0.0.0.0 --listen-port 48800 --publish-twin-state
```

如果也要 WPF 调试端口：

```powershell
python services\rtg_local_collector.py --listen-host 0.0.0.0 --listen-port 48800 --publish-twin-state --publish-wpf-debug-state
```

### 2. 跳板机启动转发端

把 `services` 目录拷到跳板机，并安装依赖：

```powershell
pip install -r services\requirements.txt
```

然后运行：

```powershell
python services\rtg_jump_forwarder.py --local-host 你的本机IP --local-port 48800 --plc-host 10.142.20.1 --plc-port 5515 --core-host 10.142.20.1 --core-port 7065
```

如果跳板机上订阅源不是本机地址，把 `--plc-host` / `--core-host` 改成跳板机能访问到的发布源地址。

默认端口：

- PLC：`5515`
- task/runinfo：`7065`
- 跳板机转发到本机：`48800`
