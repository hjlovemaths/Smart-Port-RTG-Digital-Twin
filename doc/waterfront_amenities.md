# 临水侧配套区域

在主场景陆侧临水带 X=125.5..150、Y=10..426 范围内增加静态配套模型，与远控中心配套图层独立。

新增：司机休息站与遮阳廊、两处候车休息亭、服务设备间和设备柜、两组巡检车位（共11位、4辆车、4个示意充电桩）、绿化岛、长椅、分类垃圾箱、信息牌、分段护栏、沿岸窄步道、9组救生圈及消防柜。充电、消防、车辆均为环境示意模型。

保留：X<=125.5 主路；Y=65..118、160..213 集卡等候位；Y=290..346且X<=148 黄色安全预留区；Y=118..134、248..264、378..395 横向连接和闸口空间。与既有远控中心配套区域 Y>=430 不重叠。

资源：52个按材质/区域合并的网格，13,816个三角面，零新灯光/纹理/碰撞/逐帧脚本。

- 模型：omniverse/scenes/waterfront_amenities.usda，主场景已引用。
- 生成：omniverse/scripts/build_waterfront_amenities.py（使用Kit的USD Python；复用build_port_service_areas.py几何函数）。
- 净空及资源检查：omniverse/scripts/verify_waterfront_amenities.py。
- Kit中实景对比：omniverse/scripts/preview_waterfront_amenities.py。恢复原相机和选择，不保存其他未保存修改。
- 预览图与性能结果：outputs/waterfront_amenities/。帧率区分原生渲染和包含现有帧生成倍率的估算显示值。

2026-10-23 实测（1343×585，3个固定视角，各预热8秒、采样5秒）：新增前原生均值46.08/48.42/48.68 FPS，新增后45.87/48.03/48.02 FPS；含帧生成的估算显示均值183.49/186.12/186.08 FPS。预览结束已恢复原相机，root_unchanged=true。
