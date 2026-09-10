# RTG Control 四档雨天

RTG Control → Rain → Rain amount，保留 Light / Moderate / Heavy / Torrential 四档。
雨天开启路灯和动态作业 RTG 的下照灯，另外两台 RTG 保持关闭；夜晚模式不变。
四档保留较亮的中性环境光，暴雨不再降到近似夜景的曝光；密度和能见度继续区分档位。

| 档位 | 累计雨滴 | 新增雨层雨线长度 | 下落速度 |
|---|---:|---:|---:|
| 小雨 | 100 | 0.18–0.38m | 5.5m/s |
| 中雨 | 350 | 0.24–0.52m | 6.5m/s |
| 大雨 | 850 | 0.32–0.70m | 8m/s |
| 暴雨 | 2000 | 0.42–0.92m | 9.5m/s |

雨线为细尾交叉面片，最多 8000 个三角形、12 个动画批次。更新上限 60Hz，
只更新启用的雨层，合并 USD 变更通知。没有增加粒子碰撞、积水模拟或地面水花。
使用现有距离雾，禁用可能在 RTX Real-Time 2.0 下不透明的雨天全屏遮罩。
这是轻量雨效，不包含屋顶/集装箱逐物体遮雨碰撞。

## 热加载

在 Kit Script Editor 执行（不保存、清空或重开场景）：

```python
import runpy
rain_preview = runpy.run_path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/preview_rain_effects.py")
rain_preview["reload_rain_effects"]()
```

重载器绕过 Kit 的旧模块缓存，只重载无未保存修改的天气资产，并应用当前档位。

## 帧率验证

```python
import asyncio
rain_test = asyncio.ensure_future(rain_preview["benchmark_rain_effects"]())
```

固定视角和视口尺寸，约 70 秒依次测试晴天与四档雨天，结束恢复原天气。
结果在 `tmp/rain_benchmark.json`；可在 Script Editor 用 `rain_test.result()` 查看结果或异常。
不要同时启动多个测试。

当前设备使用 4 倍帧生成，HUD 的显示 FPS 不等于原生 FPS。报告分别记录
原生渲染 FPS、含帧生成的显示 FPS、应用帧耗时与采样最低值，不更改帧生成设置。
150+ 是否满足必须以当前视角、灯光、分辨率和后台负载实测为准，不能由几何预算保证。
