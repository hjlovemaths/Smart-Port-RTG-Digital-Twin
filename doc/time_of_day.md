# Manual time of day

RTG Control → Environment now provides a **Time of day** slider from 00:00 to
24:00, replacing the Day and Night buttons. Clear, Rain and Fog choose weather;
the selected hour remains when weather or its intensity changes. Dragging the
slider applies the latest value at most ten times per second. There is no
automatic clock advance and no dependency on the simulation timeline.

The sun follows an artistic harbor day: sunrise near 06:00, solar noon at 12:00,
sunset near 18:00. This is not a latitude/date-based astronomical simulation.
Sun elevation, intensity, warm low-angle color and ambient fill interpolate
continuously. Direct sunlight fades through atmospheric extinction near the
horizon and is zero below it. Clear-weather lamps ease in through dusk, reaching
70% of authored power at night; each change uses the saved base intensity so
repeated drags do not compound the dimming. Midnight and 24:00 have
identical lighting. Rain and fog retain their existing local working lights and
adjust illumination and fog color with the hour.

Implementation is in `omniverse/scripts/smart_port_time_of_day.py`. The hour is
stored as `smartPort:timeOfDayHours` in the session layer, without saving the
scene. Existing weather APIs reapply it after preset/grade changes. The installed
Kit extension calls `build_controls(self)` when constructing the panel and
`tick(self)` from its update callback.

Startup may provide a non-null but empty USD stage. The panel uses 12:00 while
the environment is unavailable and synchronizes to the loaded stage without
writing that default into it. Slider changes made during loading are deferred
until the environment, sun, sky and streetlight roots exist. Once ready, the
selected hour (12:00 if unset) is applied to the lights as well as the label.
The same readiness check handles closing or replacing the stage. A viewport
created later triggers a lighting rebind, followed by one additional rebind
after at least 0.75 seconds and a positive viewport FPS reading. Idle frames do
not continuously reapply the sky.

Clear weather uses `omniverse/materials/HarborSky.mdl`, a lightweight analytic
approximation with a blue zenith, pale horizon haze, low-sun warmth, twilight
and night radiance. It is an artistic model, not a spectral atmosphere solver.
The renderer-owned Hosek-Wilkie function returned black after live source
updates during testing. The replacement uses only MDL math and color functions.
Each source revision gets an immutable content-hashed module under
`outputs/daylight_realism/mdl_cache`, avoiding replacement of a compiled module.
The dome retains its material binding and the shader handles Z-up coordinates.
Only the MDL supplies the dome radiance; the previous cloud PNG is used by the
headless fallback path. Rain and fog release the analytic binding and retain
their weather textures. Rendering uses per-light visibility and a 512-pixel,
one-sample dome bake, with no animated volumetric clouds.

Live testing corrected the initial 21:05 fix: attenuating both the procedural
material and dome power crushed the sky to black. Procedural sky brightness
now comes from its radiance curve at a constant dome multiplier; the texture
fallback alone uses the 0.3% late-night strength. Night has a faint blue-gray
gradient and twilight retains horizon glow. Port lamp power remains independent.
The current running app was updated through its Script Editor during validation.

`harbor_daylight.py` adds a single distant-water quad below the existing harbor
surface, extending 40 km in each horizontal direction. Calm distant shading
uses water's 1.333 IOR and lower roughness; near-water normal amplitude is reduced.
The intent is to remove the finite sea-mesh edge and reflect the sky near the
horizon. Near-water normal amplitude is 0.35 to reduce repetitive distant waves.
The earlier forced light-visibility and mesh-light diagnostic settings are no
longer applied.

The extension lives outside this repository at
`D:/Software/kit-app-template/source/extensions/smartport.rtg_bridge/smartport/rtg_bridge/extension.py`.
The before/after source and original SHA256 are in `outputs/time_of_day/`.
`tmp/prepare_time_panel.py` is a one-time migration script for the original
Day/Night layout; it deliberately stops if the layout differs.

Run `verify_time_of_day.py` with `runpy.run_path(..., run_name='__main__')`
inside Kit to reload the project weather code, capture midnight, sunrise, noon
and sunset, check weather switching and root-layer stability, then restore the
previous weather and hour. It records native FPS separately from the displayed
estimate including existing frame generation, with a 140 FPS sampled target.
This verification changes the live weather temporarily and does not save the
stage. It must be run when live scene operation is authorized.

2026-10-22 code-only validation after the 21:05 report: the weather/time
regression suite passed 78 tests (one pre-existing HUD test deselected).
Five native-USD integration tests passed separately under Kit's Python,
checking binding persistence, weather handoff, lamp restoration, stable geometry
counts, night shader selection without duplicate attenuation, immutable module
revisions and unchanged root-layer contents. Live captures and measured native
and frame-generated FPS are recorded in `outputs/time_of_day/validation.json`.

## 2026-10-23 云层与全天色彩更新

`HarborSky.mdl` 新增四层平滑噪声的高空云层。方向投影使云随距离缩小，接近地平线时融入薄霭；白天使用白色边缘和冷灰云底，低太阳角度渐变为暖色，日落后渐变为暗淡云影。夜间天空基底亮度进一步降低。

云层随0–24小时时间条缓慢改变分布；使用周期相位保证0点和24点一致。天空昼夜混合区由太阳高度 -3..6度扩展为 -6..12度，使晨昏变化更柔和。太阳运动仍是场景用的简化24小时轨迹，不是指定经纬度、日期的天文模拟。

没有新增体积云、网格、阴影光源或逐帧计算。云层只在时间或天气切换导致天空材质变化时，随1024分辨率环境贴图一起烘焙；不会投射逐块云影。雨/雾继续使用原有预设。

`verify_cloud_sky.py` 固定视角采集5:30、7:00、12:00、17:30、18:30、21:00、0:00、24:00；实景预览和性能报告位于 `outputs/cloud_sky/`。验证后恢复原天气、时间和视角。
