# 港口表面细节

改动前本地快照：`5adcac6`。

`scenes/smart_port.usda` 在最前加载 `port_realism.usda`。这一层提供：

- 6 米平铺尺度的沥青和混凝土颜色、法线、粗糙度贴图。
- 堆场、道路和码头表面的材质；保留现有道路标线。
- 码头服务带的混凝土伸缩缝、排水格栅。
- 调整道路网络悬浮标线，使其贴合路面。
- 四座连接桥的边缘矮路缘和双横杆护栏。
- 用合并网格的多团树冠替换原方块树冠，降低绿化带饱和度。
- `/World/PortRealism/Cameras/QuayDetail` 和 `PortOverview` 两个可选查看相机。

新增几何按材质合并，只提供视觉细节，不附加碰撞或修改作业设备状态。护栏位于连接桥中心线两侧 4.3 米，保留现有两侧 3.7 米道路边线。未来若启用车辆碰撞规划，应单独加入对应碰撞代理。

使用带 NumPy/Pillow 的 Python 执行 `omniverse/scripts/build_port_realism.py --textures`，再用带 `pxr` 的 Python 执行同一脚本生成 USD 层。构建可重复执行。执行 `verify_port_realism.py` 检查材质绑定、贴图、UV 和 USD 组合。

使用 Isaac Sim Python 执行 `preview_port_realism.py`，会在 `outputs/port_realism` 生成同相机的前后对比图；不会保存预览相机或运行时状态。

回退这次外观修改只需从主场景 `subLayers` 移除 `@./port_realism.usda@`；原场景和材质仍在原层中。重开主场景可加载更新。

## 第二轮：岸线结构与设备表面

`port_structure_realism.usda` 叠加在第一轮图层之上，可以单独移除以回退第二轮：

- 按当前海面 Z=-4.9478 米，补齐主堆场及离岸码头的混凝土岸壁、压顶、分缝、水位污渍与局部排水痕迹。
- 四座连接桥各增加四组双桩支撑、横向盖梁和纵梁；桥桩底部延伸到 Z=-8 米。
- 替换离岸码头原有小护舷，增加金属背板、橡胶面板、竖向筋条和固定件；保留此前隐藏的系船柱设置。
- 岸桥与 RTG 漆面采用低强度污痕和粗糙度贴图；钢件与橡胶使用独立表面参数，保留设备配色和材质绑定。

这些结构是外观模型，不代表已完成结构设计，也不参与现有碰撞与路径规划。设备与库存控制没有改动。

生成命令：带 NumPy/Pillow 的 Python 执行 `build_port_structure_realism.py --textures`，再用 USD Python 执行该脚本（不加参数）。重新构建会先排除第二轮图层，从第一轮材质读取基准，避免材质反复变暗。

验证：USD Python 执行 `verify_port_structure_realism.py`；检查资产路径、材质、UV、支撑深度、护舷范围，以及图层开关前后原有变换和物理属性保持一致。

护舷最外侧 X=-241.848 米，当前最近靠泊船的包围盒边缘 X≈-241.9765 米，静态场景保留约 0.128 米间隙。此检查针对当前摆放，不替代运动过程中的碰撞检测。

渲染：Isaac Sim Python 执行 `preview_port_realism.py --structure`，岸线、靠泊侧、护舷近景、设备和全景共五组对比图保存到 `outputs/port_structure_realism/`。此处 before 为第一轮外观，after 为第一轮加第二轮；不覆盖第一轮预览图。

## 第三轮：海面、船体与路面使用痕迹

`port_finish_realism.usda` 单独叠加在前两轮之上，提供：

- 更细、振幅更低的港池法线波纹和较柔和的粗糙度反射；保留海面运动控制器使用的 UVTransform 及 translation 输入。
- 深色船体漆面、轻微纵向污痕和接近静态水位的吃水线/防污漆过渡。船体颜色贴图采用 sRGB，避免暗色低精度条带。
- 码头减速区和连接桥转弯处的少量胎印，以及六块小面积沥青修补痕迹；保持在原路面之上、现有标线之下。

船体通过 `ship_finish_asset.usda` 包装原始 `Custom_Feeder_Ship.usdc`，不修改原始二进制资产。保留五艘实例船和一艘独立作业船的设置；最终组合有两个共享原型变体。所有 11490 个船舶网格的点、拓扑和世界变换均与第二轮场景逐项比较一致。吃水线是随船体运动的漆面，不是独立水面或碰撞几何。

生成：带 NumPy/Pillow 的 Python 执行 `build_port_finish_realism.py --textures`，然后用 USD Python 执行同一脚本（无参数）。验证用 `verify_port_finish_realism.py`，同时可继续运行前两轮验证脚本。

渲染：Isaac Sim Python 执行 `preview_port_realism.py --finish`；水面、船体、道路、全景和夜间五组前后图保存在 `outputs/port_finish_realism/`。before 为前两轮场景，after 为加入第三轮；夜间只是预览验证，不改变场景默认环境。

回退第三轮只需移除主场景中的 `@./port_finish_realism.usda@`。新贴图与船体包装层可保留，第一、二轮外观不受影响。

## 动态港池与船舶联动

`ship_sea_motion.py` 在运行时以独立、可移除的会话图层增强海面：使用多尺度方向波纹贴图，平铺次数从 360 降至 190，法线横向增益为 1.7，并滚动法线和粗糙度纹理，形成持续变化的反光。保持海面大网格静态，不逐帧重建几何；这是视觉波纹和程序化船舶响应，并非流体物理模拟。

启动默认使用 Eco 模式，六艘船分批更新，每艘每秒最多 12 次，水面波纹最多每秒更新 6 次；可切换 Smooth 模式将船体与波纹更新提高到每秒最多 30 次。船体缓慢升沉、横摇、纵摇，独立挂载的双 20 英尺货物同步跟随所属船舶；装卸任务保持工作船及配套货物稳定。默认升沉振幅上限约 0.135 米。

项目 Isaac Sim 启动器会自动启用；在 Kit 中单独打开 USD 后，可在 Script Editor 执行 `enable_harbor_motion.py`。该入口会刷新当前控制器，并在 `outputs/sea_motion/` 写入实时位移检查 JSON 和当前视口预览。控制面板的 Wave strength 调整强度；关闭波浪或关闭该面板会移除运行时效果，恢复静态场景。原始场景文件不会因动画运行而被保存或改写。

使用 USD Python 运行 `verify_sea_motion.py`，验证平滑更新频率、货物同步、装卸保持、开关恢复以及根图层与海面网格不被改写。

性能优化：初始化时缓存动画属性的 Sdf spec，将每次更新的船舶、货物和水面参数合并到一个 ChangeBlock 中提交，避免逐属性触发 USD 通知。在 ChangeBlock 内只写 Sdf，不调用 USD 查询。验证脚本还会检查每次动画 tick 最多产生一次 ObjectsChanged 通知。

`benchmark_harbor_motion.py` 可在当前 Kit 视口比较已加载控制器与磁盘更新版本，并保存 `outputs/sea_motion/performance.json`。2026-10-14 的同视角 Eco 采样中，控制器平均开销从 2.007 ms 降至 1.284 ms；应用更新采样约 32.4→33.1 次/秒，整体提升有限。该应用更新采样与视口 HUD 的渲染 FPS 口径不同，不应直接互相比较。测试保持了原分辨率和渲染画质。
