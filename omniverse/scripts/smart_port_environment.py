"""Runtime environment presets for the Smart Port stage.

The API is intentionally preset-based so rain and fog can be added without
changing the RTG control-panel callbacks.  Runtime edits go to the USD session
layer and therefore do not dirty ``smart_port.usda``.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


ENVIRONMENT_ROOT_PATH = "/World/Environment"
DAY_SKY_PATH = f"{ENVIRONMENT_ROOT_PATH}/DayHarborSky"
DAY_SUN_PATH = f"{ENVIRONMENT_ROOT_PATH}/DaySun"
NIGHT_STREET_LIGHTS_PATH = (
    "/World/SMART_PORT_QUAY_YARD_TRAFFIC_LAYOUT/"
    "SMART_PORT_QUAY_YARD_TRAFFIC_INTERSECTION_NIGHT_LIGHTS"
)
WEATHER_ROOT_PATH = "/World/SMART_PORT_WEATHER"
RAIN_EFFECT_PATH = f"{WEATHER_ROOT_PATH}/Rain"
AIR_RAIN_EFFECT_PATH = f"{RAIN_EFFECT_PATH}/AirRain"
ATMOSPHERE_OVERLAY_PATH = f"{WEATHER_ROOT_PATH}/AtmosphereOverlay"
ATMOSPHERE_OVERLAY_SHADER_PATH = (
    f"{WEATHER_ROOT_PATH}/Looks/AtmosphereOverlayMaterial/PreviewSurface"
)
RAIN_LAYER_PATHS = tuple(
    f"{AIR_RAIN_EFFECT_PATH}/RainLayer{index:02d}" for index in range(1, 5)
)
RAIN_LAYER_PHASE_PATHS = tuple(
    tuple(f"{layer_path}/Phase{phase:02d}" for phase in range(1, 4))
    for layer_path in RAIN_LAYER_PATHS
)
RAIN_SPLASH_LAYER_PATHS: tuple[str, ...] = ()
RAIN_MIST_LAYER_PATHS: tuple[str, ...] = ()
RAIN_ANIMATION_INTERVAL_SECONDS = 1.0 / 60.0
RAIN_CAMERA_FOLLOW_GRID_M = 10.0
RAIN_EMITTER_MIN_Z_M = 20.0
# The road tops are at Z=0.106..0.126 m. Clip individual streaks at the road,
# then respawn only after their trailing end reaches it.
RAIN_SURFACE_CLIP_Z_M = 0.13
RAIN_STREAK_MAX_LENGTHS_M = (0.38, 0.52, 0.70, 0.92)
RAIN_ANIMATION_PATH = RAIN_LAYER_PATHS[0]
ENVIRONMENT_MODE_ATTRIBUTE = "smartPort:environmentMode"
WIND_SPEED_ATTRIBUTE = "smartPort:windSpeedMps"
RAIN_AMOUNT_ATTRIBUTE = "smartPort:rainAmount"
FOG_AMOUNT_ATTRIBUTE = "smartPort:fogAmount"
DEFAULT_WIND_SPEED_MPS = 4.5
DEFAULT_RAIN_AMOUNT = "medium"
DEFAULT_FOG_AMOUNT = "medium"
WEATHER_BUILD_ID = "2026-08-14-rtg-weather-contrast-v9"
# Keep the legacy bridge ABI marker: older installed RTG Control extensions
# compare it on every update and otherwise reload this module every frame.
RAIN_EFFECT_REVISION = "2026-10-19-rain-surface-impacts-v15"
FOG_EFFECT_REVISION = "2026-10-26-depth-fog-v3"
RAIN_AMOUNT_LABELS = {
    "light": "Light Rain",
    "medium": "Moderate Rain",
    "heavy": "Heavy Rain",
    "torrential": "Torrential Rain",
}
# Layer count alone is easy to miss in a wide camera view.  These profiles also
# change streak opacity/emission, making the four dropdown choices visibly
# different while keeping the same batched geometry and performance budget.
RAIN_AMOUNT_PROFILES = {
    "light": {
        "visible_layers": 1, "opacity": 0.24, "emission": 6.0,
        "speed_mps": 5.5, "rate_mm_24h": "<10", "wetness": 0.06,
        "drop_count": 100,
        "streak_length_m": (0.18, 0.38), "streak_width_m": (0.006, 0.010),
    },
    "medium": {
        "visible_layers": 2, "opacity": 0.34, "emission": 8.0,
        "speed_mps": 6.5, "rate_mm_24h": "10-25", "wetness": 0.16,
        "drop_count": 350,
        "streak_length_m": (0.24, 0.52), "streak_width_m": (0.007, 0.012),
    },
    "heavy": {
        "visible_layers": 3, "opacity": 0.44, "emission": 10.0,
        "speed_mps": 8.0, "rate_mm_24h": "25-50", "wetness": 0.28,
        "drop_count": 850,
        "streak_length_m": (0.32, 0.70), "streak_width_m": (0.009, 0.015),
    },
    "torrential": {
        "visible_layers": 4, "opacity": 0.54, "emission": 12.0,
        "speed_mps": 9.5, "rate_mm_24h": "50-100; short burst >16 mm/h", "wetness": 0.42,
        "drop_count": 2000,
        "streak_length_m": (0.42, 0.92), "streak_width_m": (0.010, 0.018),
    },
}
RAIN_COVERAGE_SCALES = {
    "light": 1.0,
    "medium": 1.0,
    "heavy": 1.0,
    "torrential": 1.0,
}
# Keep diffuse daylight even in torrential rain. Density and visibility, not
# near-night exposure, distinguish the four grades. No additional lights.
RAIN_ATMOSPHERE_PROFILES = {
    # sky scale, sun scale, background, visibility m, distance density,
    # height density
    "light": (0.98, 0.86, (0.42, 0.48, 0.52), 420.0, 0.08, 0.015),
    "medium": (0.90, 0.72, (0.36, 0.42, 0.46), 240.0, 0.30, 0.060),
    "heavy": (0.82, 0.58, (0.30, 0.36, 0.40), 110.0, 0.85, 0.16),
    "torrential": (0.74, 0.44, (0.25, 0.31, 0.35), 45.0, 1.80, 0.30),
}
_RAIN_LAST_ANIMATION_UPDATE: dict[int, float] = globals().get('_RAIN_LAST_ANIMATION_UPDATE', {})
_RAIN_FALL_DISTANCES: dict[int, float] = globals().get('_RAIN_FALL_DISTANCES', {})
_RAIN_CAMERA_ANCHORS: dict[int, tuple[float, float]] = globals().get('_RAIN_CAMERA_ANCHORS', {})
_ATMOSPHERE_OVERLAY_LAST_UPDATE: dict[int, float] = {}
FOG_AMOUNT_LABELS = {
    "light": "Light Fog",
    "medium": "Moderate Fog",
    "heavy": "Heavy Fog",
}
FOG_AMOUNT_PROFILES = {
    # distance scale, density scale, height scale.  RTX Simple Fog becomes a
    # flat white veil well before its density reaches the previous values;
    # use distance bands as the primary grade cue and keep extinction low
    # enough that the working RTG remains readable.
    "light": (3.60, 0.38, 0.025),
    "medium": (2.30, 0.90, 0.060),
    "heavy": (1.35, 1.75, 0.115),
}
FOG_AMOUNT_BACKGROUND_COLORS = {
    "light": (0.64, 0.68, 0.70),
    "medium": (0.68, 0.71, 0.72),
    "heavy": (0.72, 0.74, 0.75),
}
# RTX Simple Fog is not consistently evaluated on every live viewport
# RenderProduct. This single camera-facing quad is a deliberately cheap
# visual fallback. Its opacity is kept low so it communicates visibility
# without turning the yard into a solid translucent wall.
ATMOSPHERE_OVERLAY_OPACITIES = {
    "rain": {
        "light": 0.025,
        "medium": 0.080,
        "heavy": 0.120,
        "torrential": 0.180,
    },
    "fog": {
        "light": 0.080,
        "medium": 0.250,
        "heavy": 0.480,
    },
}
ATMOSPHERE_OVERLAY_DISTANCE_M = 10.0
# The weather zone follows the modeled quay/yard footprint rather than the
# whole USD stage.  It covers the RTG work area shown in the control view and
# avoids applying a full-scene atmosphere to the surrounding water/background.
WEATHER_ZONE_BOUNDS = (-105.0, 155.0, -15.0, 505.0)
ATMOSPHERE_OVERLAY_SCALE = 90.0
DAY_BACKGROUND_TEXTURE = (
    Path(__file__).resolve().parents[1]
    / "materials"
    / "textures"
    / "day_harbor_sky_360.png"
)
RAIN_SKY_TEXTURE = DAY_BACKGROUND_TEXTURE.with_name("rain_overcast_sky_360.png")
# Preserve pre-rain session opinions, including across a Script Editor reload.
_RAIN_LOOK_RESTORE = globals().get("_RAIN_LOOK_RESTORE", {})
_RAIN_PARTICLE_BATCHES = globals().get("_RAIN_PARTICLE_BATCHES", {})
_RAIN_SURFACE_EFFECTS = globals().get("_RAIN_SURFACE_EFFECTS", {})
_RAIN_LOCAL_LIGHTS = globals().get("_RAIN_LOCAL_LIGHTS", {})
WEATHER_TEXTURE_BACKGROUND_MODES = frozenset(("rain", "fog"))
DAY_BACKGROUND_FALLBACK_COLOR = (0.34, 0.55, 0.84)
FOG_BACKGROUND_TEXTURE_LUMINANCE = 0.48
RTG_LED_NIGHT_ROOT_PATHS = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/SMART_PORT_RTG_LED_NIGHT_LIGHTS",
    "/World/PortAndRTG/RTG_STATIC_RIGHT/SMART_PORT_RTG_LED_NIGHT_LIGHTS",
    "/World/PortAndRTG/RTG_STATIC_LEFT/SMART_PORT_RTG_LED_NIGHT_LIGHTS",
)
RTG_LED_MODEL_ROOT_PATHS = (
    "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC/ANIM_CTRL_RTG_GANTRY_TRAVEL/SMART_PORT_RTG_LED_MODELS",
    "/World/PortAndRTG/RTG_STATIC_RIGHT/SMART_PORT_RTG_LED_MODELS",
    "/World/PortAndRTG/RTG_STATIC_LEFT/SMART_PORT_RTG_LED_MODELS",
)
HUD_EMISSIVE_MATERIAL_PATHS = (
    "/World/Looks/RTG_RunInfo_Floating_Billboard",
    "/World/Looks/RTG_Task_Info_Panel",
    "/World/Looks/RTG_Device_Location_Panel_Live",
    "/World/Looks/RTG_Device_Location_Panel_Values_Live",
    "/World/Looks/RTG_Container_Info_Panel_Live",
    "/World/Looks/RTG_Device_Location_Panel",
    "/World/Looks/RTG_Container_Info_Panel",
)


@dataclass(frozen=True)
class EnvironmentPreset:
    key: str
    label: str
    sky_intensity: float
    sky_exposure: float
    sky_color: tuple[float, float, float]
    sun_intensity: float
    sun_color: tuple[float, float, float]
    street_lights_visible: bool
    background_color: tuple[float, float, float] | None = None
    rain_visible: bool = False
    fog_enabled: bool = False
    fog_color: tuple[float, float, float] = (0.75, 0.75, 0.75)
    fog_start_dist: float = 0.0
    fog_end_dist: float = 5000.0
    fog_distance_density: float = 1.0
    fog_height_density: float = 0.0


ENVIRONMENT_PRESETS = {
    "day": EnvironmentPreset(
        key="day",
        label="白天",
        sky_intensity=650.0,
        sky_exposure=0.0,
        sky_color=(1.0, 1.0, 1.0),
        sun_intensity=2200.0,
        sun_color=(1.0, 0.95, 0.86),
        street_lights_visible=False,
    ),
    "night": EnvironmentPreset(
        key="night",
        label="夜晚",
        sky_intensity=160.0,
        sky_exposure=-0.4,
        sky_color=(0.12, 0.22, 0.55),
        sun_intensity=450.0,
        sun_color=(0.34, 0.48, 0.82),
        street_lights_visible=True,
        background_color=(0.018, 0.045, 0.12),
    ),
    "rain": EnvironmentPreset(
        key="rain",
        label="雨天",
        sky_intensity=600.0,
        sky_exposure=-0.08,
        sky_color=(0.94, 0.97, 1.0),
        sun_intensity=180.0,
        sun_color=(0.80, 0.84, 0.87),
        # Rain keeps road lighting and the working RTG on, not stationary RTGs.
        street_lights_visible=True,
        background_color=(0.20, 0.27, 0.32),
        rain_visible=True,
        fog_enabled=True,
        fog_color=(0.46, 0.53, 0.58),
        fog_start_dist=38.0,
        fog_end_dist=320.0,
        fog_distance_density=0.38,
        fog_height_density=0.08,
    ),
    "fog": EnvironmentPreset(
        key="fog",
        label="雾天",
        sky_intensity=520.0,
        sky_exposure=-0.05,
        sky_color=(0.94, 0.97, 1.0),
        sun_intensity=140.0,
        sun_color=(0.78, 0.80, 0.79),
        street_lights_visible=True,
        background_color=(0.52, 0.57, 0.58),
        fog_enabled=True,
        fog_color=(0.62, 0.67, 0.68),
        fog_start_dist=8.0,
        fog_end_dist=125.0,
        fog_distance_density=0.92,
        fog_height_density=0.18,
    ),
}

_MODE_ALIASES = {
    "day": "day",
    "daytime": "day",
    "白天": "day",
    "night": "night",
    "nighttime": "night",
    "夜晚": "night",
    "夜间": "night",
    "rain": "rain",
    "rainy": "rain",
    "雨天": "rain",
    "下雨": "rain",
    "fog": "fog",
    "foggy": "fog",
    "雾天": "fog",
    "大雾": "fog",
}

_RAIN_AMOUNT_ALIASES = {
    "light": "light",
    "light rain": "light",
    "small": "light",
    "小雨": "light",
    "medium": "medium",
    "moderate rain": "medium",
    "moderate": "medium",
    "中雨": "medium",
    "heavy": "heavy",
    "heavy rain": "heavy",
    "大雨": "heavy",
    "torrential": "torrential",
    "torrential rain": "torrential",
    "暴雨": "torrential",
}
_FOG_AMOUNT_ALIASES = {
    "light": "light",
    "light fog": "light",
    "轻雾": "light",
    "medium": "medium",
    "moderate fog": "medium",
    "moderate": "medium",
    "中雾": "medium",
    "heavy": "heavy",
    "heavy fog": "heavy",
    "浓雾": "heavy",
}


def normalize_environment_mode(mode: Any) -> str:
    key = str(mode or "day").strip().lower()
    normalized = _MODE_ALIASES.get(key, key)
    if normalized not in ENVIRONMENT_PRESETS:
        supported = ", ".join(sorted(ENVIRONMENT_PRESETS))
        raise ValueError(f"Unsupported environment mode {mode!r}; expected {supported}")
    return normalized


def environment_mode_label(mode: Any) -> str:
    return ENVIRONMENT_PRESETS[normalize_environment_mode(mode)].label


def normalize_rain_amount(amount: Any) -> str:
    key = str(amount or DEFAULT_RAIN_AMOUNT).strip().lower()
    normalized = _RAIN_AMOUNT_ALIASES.get(key, key)
    if normalized not in RAIN_AMOUNT_LABELS:
        supported = ", ".join(RAIN_AMOUNT_LABELS)
        raise ValueError(f"Unsupported rain amount {amount!r}; expected {supported}")
    return normalized


def normalize_fog_amount(amount: Any) -> str:
    key = str(amount or DEFAULT_FOG_AMOUNT).strip().lower()
    normalized = _FOG_AMOUNT_ALIASES.get(key, key)
    if normalized not in FOG_AMOUNT_LABELS:
        supported = ", ".join(FOG_AMOUNT_LABELS)
        raise ValueError(f"Unsupported fog amount {amount!r}; expected {supported}")
    return normalized


def _sdf_type(type_name: str) -> Any:
    try:
        from pxr import Sdf  # type: ignore

        return getattr(Sdf.ValueTypeNames, type_name)
    except Exception:
        return None


def _valid_prim(stage: Any, path: str) -> Any:
    prim = stage.GetPrimAtPath(path)
    if prim is None:
        raise RuntimeError(f"Missing environment prim: {path}")
    try:
        if not prim.IsValid():
            raise RuntimeError(f"Missing environment prim: {path}")
    except AttributeError:
        pass
    return prim


def _set_attribute(prim: Any, name: str, value: Any, type_name: str) -> None:
    attribute = prim.GetAttribute(name)
    try:
        valid = bool(attribute and attribute.IsValid())
    except AttributeError:
        valid = attribute is not None
    if not valid:
        sdf_type = _sdf_type(type_name)
        attribute = prim.CreateAttribute(name, sdf_type)
    attribute.Set(value)


def _set_attribute_if_changed(
    prim: Any,
    name: str,
    value: Any,
    type_name: str,
) -> bool:
    """Avoid session-layer churn when a low-frequency value is unchanged."""

    try:
        attribute = prim.GetAttribute(name)
        if attribute and attribute.IsValid() and attribute.Get() == value:
            return False
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    _set_attribute(prim, name, value, type_name)
    return True


def _range_text(value: tuple[float, float]) -> str:
    return f"{value[0]:.3f}-{value[1]:.3f}"


def _clear_attribute(prim: Any, name: str) -> None:
    """Remove a session-layer override so authored/default RTX behavior wins."""

    try:
        attribute = prim.GetAttribute(name)
        if attribute and attribute.IsValid():
            clear = getattr(attribute, "Clear", None)
            if clear is not None:
                clear()
    except (AttributeError, RuntimeError, TypeError):
        pass


def _session_edit_context(stage: Any) -> Any:
    try:
        from pxr import Usd  # type: ignore

        session_layer = stage.GetSessionLayer()
        if session_layer is not None:
            return Usd.EditContext(stage, session_layer)
    except Exception:
        pass
    return nullcontext()


def _asset_path(value: Path) -> Any:
    try:
        from pxr import Sdf  # type: ignore

        return Sdf.AssetPath(value.as_posix())
    except Exception:
        return value.as_posix()


def _rain_look_override(stage: Any, path: str, name: str, value: Any, type_name: str) -> None:
    """Remember the exact session default before temporarily changing the look."""
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return
    saved = _RAIN_LOOK_RESTORE.setdefault(stage, {})
    key = (path, name)
    if key not in saved:
        try:
            spec = stage.GetSessionLayer().GetAttributeAtPath(f"{path}.{name}")
            saved[key] = (True, spec.default) if spec and spec.HasInfo("default") else (False, None)
        except AttributeError:  # lightweight stage doubles
            attr = prim.GetAttribute(name)
            saved[key] = (bool(attr), attr.Get() if attr else None)
    _set_attribute(prim, name, value, type_name)


def _restore_rain_look(stage: Any) -> None:
    for (path, name), (authored, value) in _RAIN_LOOK_RESTORE.pop(stage, {}).items():
        prim = stage.GetPrimAtPath(path)
        if prim:
            attr = prim.GetAttribute(name)
            if attr:
                attr.Set(value) if authored else attr.Clear()


def _configure_rain_surface_and_lights(stage: Any, amount: str) -> None:
    """Reuse existing texture samples/lights; author only on weather changes."""
    wetness = float(RAIN_AMOUNT_PROFILES[amount]["wetness"])
    base = "/World/PortRealism/Looks/asphalt"
    # Preserve the existing aggregate/repair texture variation under the film.
    darken = .82 - wetness * .35
    rough_scale = .80 - wetness * .90
    for suffix, scale in (("color", darken), ("roughness", rough_scale)):
        _rain_look_override(stage, f"{base}/{suffix}", "inputs:scale", (scale, scale, scale, 1.0), "Float4")
    # Roughness carries the water film without an additional clearcoat lobe.
    _rain_look_override(stage, f"{base}/Surface", "inputs:clearcoat", 0.0, "Float")
    _rain_look_override(stage, f"{WEATHER_ROOT_PATH}/Rain/WetRoadSheen", "visibility", "invisible", "Token")
    _configure_weather_local_lights(stage)


def _configure_weather_local_lights(stage: Any) -> None:
    # Lights stay on for port operations, at daytime output. The previous
    # night exposure produced blown-out pools and luminous rain streaks.
    roots = (NIGHT_STREET_LIGHTS_PATH, RTG_LED_NIGHT_ROOT_PATHS[0])
    try:
        from pxr import Usd, UsdGeom
        transforms = UsdGeom.XformCache()
        anchor = _active_camera_ground_xy(stage)
        local_lights = []
        for root_path in roots:
            root = stage.GetPrimAtPath(root_path)
            if not root:
                continue
            for prim in Usd.PrimRange(root):
                if prim.GetTypeName().endswith("Light"):
                    _rain_look_override(stage, str(prim.GetPath()), "inputs:exposure", -0.8, "Float")
                    if root_path == NIGHT_STREET_LIGHTS_PATH:
                        # One centre emitter per three-head pole is sufficient
                        # under the diffuse sky; night restores all 336 emitters.
                        visibility = "inherited" if "_Head_02_" in prim.GetName() else "invisible"
                        if visibility == "inherited":
                            position = transforms.GetLocalToWorldTransform(prim).ExtractTranslation()
                            near = math.hypot(position[0]-anchor[0], position[1]-anchor[1]) <= 70.
                            visibility = "inherited" if near else "invisible"
                        _rain_look_override(stage, str(prim.GetPath()), "visibility", visibility, "Token")
                        if "_Head_02_" in prim.GetName():
                            local_lights.append((prim.GetAttribute('visibility'), float(position[0]), float(position[1])))
        _RAIN_LOCAL_LIGHTS[stage] = (None, local_lights)
    except ImportError:
        pass


def _configure_viewport_background(stage: Any, preset: EnvironmentPreset) -> None:
    """Match the visible weather sky to its diffuse lighting and reflections."""

    render_products = []
    try:
        render_products = [
            prim for prim in stage.Traverse() if prim.GetTypeName() == "RenderProduct"
        ]
    except (AttributeError, TypeError):
        pass

    background_texture = RAIN_SKY_TEXTURE if preset.key in ("rain", "fog") else DAY_BACKGROUND_TEXTURE
    use_weather_texture = (
        (preset.key == "day" or preset.key in WEATHER_TEXTURE_BACKGROUND_MODES)
        and background_texture.is_file()
    )
    use_dome = preset.key in ("day", "rain", "fog") and use_weather_texture
    if preset.key in ("rain", "fog"):
        _rain_look_override(stage, DAY_SKY_PATH, "inputs:texture:file",
                            _asset_path(RAIN_SKY_TEXTURE) if RAIN_SKY_TEXTURE.is_file() else "", "Asset")
        _rain_look_override(stage, DAY_SUN_PATH, "inputs:angle", 12.0, "Float")
        # Align this latlong cloud hemisphere with Kit's Z-up dome mapping.
        _rain_look_override(stage, DAY_SKY_PATH, "xformOp:rotateXYZ", (-90.0, 0.0, 18.0), "Float3")
    texture_luminance_scale = {
        "day": 1.0,
        "rain": 0.70,
        # A darker, desaturated sky texture makes the fog layer read as
        # overcast instead of looking like a clear blue day.
        "fog": FOG_BACKGROUND_TEXTURE_LUMINANCE,
    }.get(preset.key, 1.0)
    for render_product in render_products:
        if use_dome:
            # Remove the saved backplate as well as its override; otherwise
            # Kit can retain the screen-space image when settings synchronize.
            _set_attribute(render_product, "omni:rtx:background:source:texture:path", "", "Asset")
            _set_attribute(render_product, "omni:rtx:background:source:type", "domeLight", "Token")
            _set_attribute(render_product, "omni:rtx:lights:visibilityOverride", "perLight", "Token")
            _set_attribute(render_product, "omni:rtx:post:compositing:postTonemapBackgroundComposite", False, "Bool")
        elif use_weather_texture:
            _set_attribute(
                render_product,
                "omni:rtx:background:source:type",
                "backplate",
                "Token",
            )
            _set_attribute(
                render_product,
                "omni:rtx:background:source:texture:path",
                _asset_path(DAY_BACKGROUND_TEXTURE),
                "Asset",
            )
            _set_attribute(
                render_product,
                "omni:rtx:background:source:texture:luminanceScale",
                texture_luminance_scale,
                "Float",
            )
            _set_attribute(render_product, "omni:rtx:background:source:texture:colorSpace", "sRGB", "Token")
            _set_attribute(render_product, "omni:rtx:post:compositing:postTonemapBackgroundComposite", True, "Bool")
        elif preset.key == "day":
            # Use a visible blue fallback only when the local cloud image is missing.
            _set_attribute(render_product, "omni:rtx:background:source:type", "color", "Token")
            _set_attribute(render_product, "omni:rtx:background:source:color", DAY_BACKGROUND_FALLBACK_COLOR, "Color3f")
        else:
            color = preset.background_color or (0.018, 0.045, 0.12)
            _set_attribute(
                render_product,
                "omni:rtx:background:source:type",
                "color",
                "Token",
            )
            _set_attribute(
                render_product,
                "omni:rtx:background:source:color",
                color,
                "Color3f",
            )

    # Modern viewport products own these settings. Writing their legacy carb
    # aliases also causes Kit to author asynchronously into the root layer.
    if render_products:
        return
    # Viewports created after the stage was saved may use a transient render
    # product. Mirror the same override into the live RTX settings as a fallback.
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        if use_dome:
            settings.set_string("/rtx/background/source/texture/path", "")
            settings.set_int("/rtx/background/source/type", 0)
            settings.set_int("/rtx/raytracing/showLights", 0)
            settings.set_bool("/rtx/post/backgroundZeroAlpha/postTonemapBackgroundComposite", False)
        elif use_weather_texture:
            # RTX background override: 1 forces the assigned texture. This
            # keeps rain and fog on a textured overcast sky instead of a flat
            # color while the separate RTX fog layer controls visibility.
            settings.set_int("/rtx/background/source/type", 1)
            settings.set_int("/rtx/background/source/texture/colorSpace", 0)
            settings.set_bool("/rtx/post/backgroundZeroAlpha/postTonemapBackgroundComposite", True)
            settings.set_string(
                "/rtx/background/source/texture/path",
                DAY_BACKGROUND_TEXTURE.as_posix(),
            )
            settings.set_float(
                "/rtx/background/source/texture/luminanceScale",
                texture_luminance_scale,
            )
        elif preset.key == "day":
            settings.set_int("/rtx/background/source/type", 2)
            settings.set_float_array(
                "/rtx/background/source/color", list(DAY_BACKGROUND_FALLBACK_COLOR)
            )
        else:
            color = preset.background_color or (0.018, 0.045, 0.12)
            settings.set_int("/rtx/background/source/type", 2)
            settings.set_float_array(
                "/rtx/background/source/color",
                list(color),
            )
    except (AttributeError, ImportError, RuntimeError):
        pass


def _configure_fog_background(stage: Any, preset: EnvironmentPreset, fog_amount: str) -> None:
    """Separate fog levels through background, visibility, and direct light."""

    if preset.key != "fog":
        return
    amount = normalize_fog_amount(fog_amount)
    color = FOG_AMOUNT_BACKGROUND_COLORS[amount]
    sky_scale, sun_scale = {
        "light": (1.08, 0.65),
        "medium": (1.00, 0.32),
        "heavy": (0.94, 0.10),
    }[amount]
    try:
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        _set_attribute(environment, "smartPort:fogEffectRevision", FOG_EFFECT_REVISION, "String")
        distance_scale, density_scale, height_scale = FOG_AMOUNT_PROFILES[amount]
        _set_attribute(
            environment,
            "smartPort:fogVisibilityMeters",
            preset.fog_end_dist * distance_scale,
            "Float",
        )
        _set_attribute(environment, "smartPort:fogDensityScale", density_scale, "Float")
        _set_attribute(environment, "smartPort:fogHeightScale", height_scale, "Float")
        _set_attribute(environment, "smartPort:fogTint", color, "Color3f")
        # A featureless mist horizon should merge with distant geometry;
        # the cloud dome still supplies diffuse illumination and reflections.
        for product in stage.Traverse():
            if product.GetTypeName() == "RenderProduct":
                _set_attribute(product, "omni:rtx:background:source:type", "color", "Token")
                # Simple Fog adds in-scattering to shaded geometry, whereas
                # the constant sky only passes through tone mapping. Lift the
                # sky towards white to avoid a dark stripe at the horizon.
                sky_color = tuple(c + (1.0 - c) * 0.55 for c in color)
                _set_attribute(product, "omni:rtx:background:source:color", sky_color, "Color3f")
                _set_attribute(product, "omni:rtx:post:compositing:postTonemapBackgroundComposite", True, "Bool")
        _set_attribute(
            _valid_prim(stage, DAY_SKY_PATH),
            "inputs:intensity",
            preset.sky_intensity * sky_scale,
            "Float",
        )
        _set_attribute(
            _valid_prim(stage, DAY_SUN_PATH),
            "inputs:intensity",
            preset.sun_intensity * sun_scale,
            "Float",
        )
    except (AttributeError, RuntimeError, TypeError):
        pass


def _configure_rain_atmosphere(
    stage: Any,
    preset: EnvironmentPreset,
    rain_amount: str,
) -> None:
    """Give each rain grade a distinct daylight and visibility envelope."""

    if not preset.rain_visible:
        return
    amount = normalize_rain_amount(rain_amount)
    (
        sky_scale,
        sun_scale,
        background_color,
        visibility_m,
        distance_density,
        height_density,
    ) = RAIN_ATMOSPHERE_PROFILES[amount]
    try:
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        _set_attribute(
            environment,
            "smartPort:rainVisibilityMeters",
            visibility_m,
            "Float",
        )
        _set_attribute(
            environment,
            "smartPort:rainAtmosphereDensity",
            distance_density,
            "Float",
        )
        _set_attribute(
            _valid_prim(stage, DAY_SKY_PATH),
            "inputs:intensity",
            preset.sky_intensity * sky_scale,
            "Float",
        )
        _set_attribute(
            _valid_prim(stage, DAY_SUN_PATH),
            "inputs:intensity",
            preset.sun_intensity * sun_scale,
            "Float",
        )
    except (AttributeError, RuntimeError, TypeError):
        pass

    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        if any(p.GetTypeName() == "RenderProduct" for p in stage.Traverse()):
            return
        settings.set_bool("/rtx/fog/enabled", True)
        settings.set_float_array("/rtx/fog/fogColor", list(background_color))
        settings.set_float("/rtx/fog/fogStartDist", 4.0 if amount == "torrential" else 12.0)
        settings.set_float("/rtx/fog/fogEndDist", visibility_m)
        settings.set_float("/rtx/fog/fogDistanceDensity", distance_density)
        settings.set_float("/rtx/fog/fogHeightDensity", height_density)
    except (AttributeError, ImportError, RuntimeError):
        pass


def _configure_render_product_fog(
    stage: Any,
    preset: EnvironmentPreset,
    fog_amount: str,
    rain_amount: str,
) -> None:
    """Mirror the active fog profile onto the viewport RenderProduct.

    Kit can keep RenderProduct fog attributes authored in the USDA layer even
    after the global ``/rtx/fog/*`` settings change.  Updating both paths is
    important for a large yard: the bay visibility band must follow the
    selected light/medium/heavy profile in the actual viewport.
    """

    fog_amount = normalize_fog_amount(fog_amount)
    if preset.rain_visible:
        amount = normalize_rain_amount(rain_amount)
        _, _, _, end_dist, distance_density, height_density = RAIN_ATMOSPHERE_PROFILES[
            amount
        ]
        start_dist = 4.0 if amount == "torrential" else 12.0
    else:
        distance_scale, density_scale, height_scale = FOG_AMOUNT_PROFILES[fog_amount]
        start_dist = preset.fog_start_dist * distance_scale
        end_dist = preset.fog_end_dist * distance_scale
        distance_density = preset.fog_distance_density * density_scale
        height_density = preset.fog_height_density * height_scale

    try:
        render_products = [
            prim for prim in stage.Traverse() if prim.GetTypeName() == "RenderProduct"
        ]
        fog_color = (
            RAIN_ATMOSPHERE_PROFILES[normalize_rain_amount(rain_amount)][2]
            if preset.rain_visible
            else FOG_AMOUNT_BACKGROUND_COLORS[fog_amount]
        )
        for render_product in render_products:
            _set_attribute(
                render_product,
                "omni:rtx:fog:enabled",
                preset.fog_enabled,
                "Bool",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:color",
                fog_color,
                "Color3f",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:colorIntensity",
                1.0,
                "Float",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:distanceDensity",
                distance_density,
                "Float",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:endDist",
                end_dist,
                "Float",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:height:density",
                height_density,
                "Float",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:start:height",
                3.0,
                "Float",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:zUp:enabled",
                True,
                "Bool",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:start:distance",
                start_dist,
                "Float",
            )
            _set_attribute(
                render_product,
                "omni:rtx:fog:height:falloff",
                0.12,
                "Float",
            )
    except (AttributeError, RuntimeError, TypeError):
        pass


def _configure_atmosphere_overlay(
    stage: Any,
    preset: EnvironmentPreset,
    rain_amount: str,
    fog_amount: str,
) -> None:
    """Configure the one-quad visibility veil used by rain and fog grades."""

    try:
        overlay = _valid_prim(stage, ATMOSPHERE_OVERLAY_PATH)
        shader = _valid_prim(stage, ATMOSPHERE_OVERLAY_SHADER_PATH)
    except (AttributeError, RuntimeError, TypeError):
        # Older sessions may still have the previous weather layer loaded.
        # The RTX fog path remains available in that case.
        return

    mode = preset.key
    # RTX Real-Time 2.0 can treat this PreviewSurface veil as opaque.
    # Rain uses distance fog instead; never put a large mesh over the yard.
    enabled = False
    if mode == "rain":
        amount = normalize_rain_amount(rain_amount)
        opacity = ATMOSPHERE_OVERLAY_OPACITIES[mode][amount]
        color = RAIN_ATMOSPHERE_PROFILES[amount][2]
        visibility_m = RAIN_ATMOSPHERE_PROFILES[amount][3]
    elif mode == "fog":
        amount = normalize_fog_amount(fog_amount)
        opacity = ATMOSPHERE_OVERLAY_OPACITIES[mode][amount]
        color = FOG_AMOUNT_BACKGROUND_COLORS[amount]
        distance_scale = FOG_AMOUNT_PROFILES[amount][0]
        visibility_m = preset.fog_end_dist * distance_scale
    else:
        amount = "none"
        opacity = 0.0
        color = preset.background_color or DAY_BACKGROUND_FALLBACK_COLOR
        visibility_m = 0.0

    _set_attribute(
        overlay,
        "visibility",
        "inherited" if enabled else "invisible",
        "Token",
    )
    _set_attribute(overlay, "smartPort:mode", mode, "Token")
    _set_attribute(overlay, "smartPort:amount", amount, "Token")
    _set_attribute(overlay, "smartPort:opacity", opacity, "Float")
    _set_attribute(overlay, "smartPort:visibilityMeters", visibility_m, "Float")
    _set_attribute(shader, "inputs:diffuseColor", color, "Color3f")
    _set_attribute(
        shader,
        "inputs:emissiveColor",
        tuple(min(1.0, channel * 0.40) for channel in color),
        "Color3f",
    )
    _set_attribute(shader, "inputs:opacity", opacity, "Float")


def _camera_is_inside_weather_zone(camera_x: float, camera_y: float) -> bool:
    min_x, max_x, min_y, max_y = WEATHER_ZONE_BOUNDS
    return min_x <= camera_x <= max_x and min_y <= camera_y <= max_y


def _update_atmosphere_overlay_transform(stage: Any, elapsed_seconds: float) -> bool:
    """Keep the single visibility veil in front of the active viewport camera."""

    stage_id = id(stage)
    elapsed = float(elapsed_seconds)
    last_update = _ATMOSPHERE_OVERLAY_LAST_UPDATE.get(stage_id)
    if (
        last_update is not None
        and elapsed >= last_update
        and elapsed - last_update < 0.05
    ):
        return True
    if current_environment_mode(stage) == "rain":
        camera_x, camera_y = _active_camera_ground_xy(stage)
        with _session_edit_context(stage):
            _set_attribute_if_changed(
                _valid_prim(stage, ATMOSPHERE_OVERLAY_PATH),
                "visibility", "invisible", "Token",
            )
            _set_attribute_if_changed(
                _valid_prim(stage, RAIN_EFFECT_PATH), "visibility",
                "inherited" if _camera_is_inside_weather_zone(camera_x, camera_y) else "invisible",
                "Token",
            )
        _ATMOSPHERE_OVERLAY_LAST_UPDATE[stage_id] = elapsed
        return True
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore
        from pxr import Gf, Usd, UsdGeom  # type: ignore

        viewport = get_active_viewport()
        camera_path = getattr(viewport, "camera_path", None) if viewport else None
        camera_prim = stage.GetPrimAtPath(str(camera_path)) if camera_path else None
        if not camera_prim or not camera_prim.IsValid():
            return False
        camera_matrix = UsdGeom.Xformable(camera_prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        camera_translation = camera_matrix.ExtractTranslation()
        inside_weather_zone = _camera_is_inside_weather_zone(
            float(camera_translation[0]),
            float(camera_translation[1]),
        )
        local_matrix = Gf.Matrix4d(1.0)
        local_matrix.SetScale(
            Gf.Vec3d(
                ATMOSPHERE_OVERLAY_SCALE,
                ATMOSPHERE_OVERLAY_SCALE,
                ATMOSPHERE_OVERLAY_SCALE,
            )
        )
        local_matrix.SetTranslate(
            Gf.Vec3d(0.0, 0.0, -ATMOSPHERE_OVERLAY_DISTANCE_M)
        )
        world_matrix = camera_matrix * local_matrix
        with _session_edit_context(stage):
            overlay = _valid_prim(stage, ATMOSPHERE_OVERLAY_PATH)
            mode = current_environment_mode(stage)
            _set_attribute_if_changed(
                overlay,
                "visibility",
                "inherited"
                if inside_weather_zone and mode in ATMOSPHERE_OVERLAY_OPACITIES
                else "invisible",
                "Token",
            )
            rain_root = stage.GetPrimAtPath(RAIN_EFFECT_PATH)
            if rain_root and rain_root.IsValid():
                _set_attribute_if_changed(
                    rain_root,
                    "visibility",
                    "inherited"
                    if inside_weather_zone and mode == "rain"
                    else "invisible",
                    "Token",
                )
            _set_attribute_if_changed(
                overlay,
                "xformOp:transform",
                world_matrix,
                "Matrix4d",
            )
            _set_attribute_if_changed(
                overlay,
                "xformOpOrder",
                ["xformOp:transform"],
                "TokenArray",
            )
        _ATMOSPHERE_OVERLAY_LAST_UPDATE[stage_id] = elapsed
        return True
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return False


def _configure_rtx_fog(preset: EnvironmentPreset, fog_amount: str = DEFAULT_FOG_AMOUNT, stage: Any = None) -> None:
    """Apply low-cost RTX Simple Fog settings for rain and fog presets."""

    if stage is not None and any(p.GetTypeName() == "RenderProduct" for p in stage.Traverse()):
        return
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        settings.set_bool("/rtx/fog/enabled", preset.fog_enabled)
        settings.set_float_array("/rtx/fog/fogColor", list(preset.fog_color))
        settings.set_float("/rtx/fog/fogColorIntensity", 1.0)
        settings.set_bool("/rtx/fog/fogZup/enabled", True)
        fog_amount = normalize_fog_amount(fog_amount)
        # Use deliberately separated visibility bands; multiplying a saturated
        # exponential fog density alone produces almost identical frames.
        distance_scale, density_scale, height_scale = FOG_AMOUNT_PROFILES[fog_amount]
        settings.set_float("/rtx/fog/fogStartHeight", 3.0)
        settings.set_float(
            "/rtx/fog/fogHeightDensity",
            preset.fog_height_density * height_scale,
        )
        settings.set_float("/rtx/fog/fogHeightFalloff", 0.12)
        settings.set_float("/rtx/fog/fogStartDist", preset.fog_start_dist * distance_scale)
        settings.set_float("/rtx/fog/fogEndDist", preset.fog_end_dist * distance_scale)
        settings.set_float(
            "/rtx/fog/fogDistanceDensity",
            preset.fog_distance_density * density_scale,
        )
    except (AttributeError, ImportError, RuntimeError):
        pass


def _active_camera_ground_xy(stage: Any) -> tuple[float, float]:
    """Center the local rain volume on the ground just ahead of the camera."""

    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore
        from pxr import Usd, UsdGeom  # type: ignore

        viewport = get_active_viewport()
        camera_path = getattr(viewport, "camera_path", None) if viewport else None
        camera_prim = stage.GetPrimAtPath(str(camera_path)) if camera_path else None
        if not camera_prim:
            return (0.0, 0.0)
        matrix = UsdGeom.Xformable(camera_prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        translate = matrix.ExtractTranslation()
        # A camera-centered emitter wastes half its drops behind the view and
        # misses the box roofs in a typical RTG overview. Lead by 14 metres.
        forward_x, forward_y = -float(matrix[2][0]), -float(matrix[2][1])
        length = math.hypot(forward_x, forward_y)
        distance = min(14., max(0., float(translate[2])-1.)*length/max(.15, abs(float(matrix[2][2]))))
        lead = distance / length if length > .1 else 0.0
        return (float(translate[0]) + forward_x*lead,
                float(translate[1]) + forward_y*lead)
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return (0.0, 0.0)


def _update_weather_local_lights(stage: Any, camera_anchor: tuple) -> None:
    previous_anchor, local_lights = _RAIN_LOCAL_LIGHTS.get(stage, (None, ()))
    if previous_anchor != camera_anchor:
        for attr, x, y in local_lights:
            visibility = 'inherited' if math.hypot(x-camera_anchor[0], y-camera_anchor[1]) <= 70. else 'invisible'
            if attr.Get() != visibility:
                attr.Set(visibility)
        _RAIN_LOCAL_LIGHTS[stage] = (camera_anchor, local_lights)


def _update_rain_point_batches(stage: Any, visible_count: int, fall_travel: float,
                              wind_speed: float, camera_anchor: tuple, elapsed: float) -> None:
    from rain_particle_motion import RainPointBatch
    _update_weather_local_lights(stage, camera_anchor)
    try:
        from pxr import Sdf, Vt
        pack = Vt.Vec3fArray.FromNumpy
        block = Sdf.ChangeBlock
    except ImportError:
        pack = lambda array: array.tolist()
        block = nullcontext
        surfaces = None
    else:
        from rain_surface_effects import RainSurfaceEffects
        if stage not in _RAIN_SURFACE_EFFECTS:
            _RAIN_SURFACE_EFFECTS[stage] = RainSurfaceEffects(stage)
        surfaces = _RAIN_SURFACE_EFFECTS[stage]
        surfaces.refresh(camera_anchor, elapsed)
    batches = _RAIN_PARTICLE_BATCHES.setdefault(stage, {})
    pending = []
    contacts = []
    for layer_index, paths in enumerate(RAIN_LAYER_PHASE_PATHS[:visible_count]):
        for phase_index, path in enumerate(paths):
            if path not in batches:
                prim = _valid_prim(stage, path)
                attr = prim.GetAttribute("points")
                if not attr or attr.Get() is None:
                    continue
                points = attr.Get()
                batch = RainPointBatch(points, phase_index/3.0 + layer_index*.071,
                                       surface_z=RAIN_SURFACE_CLIP_Z_M)
                # Capture pre-rain session opinions for exact day/night restore.
                _rain_look_override(stage, path, "points", points, "Point3fArray")
                _rain_look_override(stage, path, "xformOp:translate", (0.0, 0.0, 0.0), "Double3")
                _rain_look_override(stage, path, "extent", [(-25., -19., .12), (65., 40., 28.)], "Float3Array")
                if surfaces:
                    _rain_look_override(stage, path, "visibility", "invisible", "Token")
                batches[path] = (attr, batch)
            attr, batch = batches[path]
            surface_key = (surfaces.version if surfaces else 0, camera_anchor, wind_speed)
            if batch.surface_key != surface_key:
                batch.set_surfaces(surfaces.roofs if surfaces else (), camera_anchor, wind_speed)
                batch.surface_key = surface_key
            points = batch.update(fall_travel, wind_speed)
            pending.append((attr, points if surfaces else pack(points)))
            contacts.append(batch.contacts)
    # All prim resolution and buffer conversion occurs before the USD block.
    with block():
        if surfaces:
            surfaces.update_motion([points for _, points in pending])
            surfaces.update_splashes(contacts, elapsed, visible_count)
        else:
            for attr, points in pending:
                attr.Set(points)


def update_weather_animation(stage: Any, elapsed_seconds: float) -> bool:
    """Advance weather visuals independently of the paused RTG timeline."""

    try:
        mode = current_environment_mode(stage)
        if mode not in ("rain", "fog"):
            return False
        if mode == "fog":
            # Distance/height fog is evaluated by RTX; no camera-facing mesh
            # or per-frame USD geometry changes are needed.
            anchor = _active_camera_ground_xy(stage)
            anchor = tuple(round(v / RAIN_CAMERA_FOLLOW_GRID_M) * RAIN_CAMERA_FOLLOW_GRID_M for v in anchor)
            with _session_edit_context(stage):
                _update_weather_local_lights(stage, anchor)
            return True
        elapsed = float(elapsed_seconds)
        stage_id = id(stage)
        last_update = _RAIN_LAST_ANIMATION_UPDATE.get(stage_id)
        if (
            last_update is not None
            and elapsed >= last_update
            and elapsed - last_update < RAIN_ANIMATION_INTERVAL_SECONDS
        ):
            # Both the bridge and Live receiver can call this in one frame.
            # Throttle before camera queries, overlay work and profile reads.
            return True
        _update_atmosphere_overlay_transform(stage, elapsed)
        rain_root = _valid_prim(stage, RAIN_EFFECT_PATH)
        if _get_attribute_value(rain_root, "visibility", "inherited") == "invisible":
            _RAIN_LAST_ANIMATION_UPDATE[stage_id] = elapsed
            return True
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        wind_speed = float(
            _get_attribute_value(environment, WIND_SPEED_ATTRIBUTE, DEFAULT_WIND_SPEED_MPS)
        )
        rain_amount = normalize_rain_amount(
            _get_attribute_value(environment, RAIN_AMOUNT_ATTRIBUTE, DEFAULT_RAIN_AMOUNT)
        )
        vertical_speed = float(RAIN_AMOUNT_PROFILES[rain_amount]["speed_mps"])
        # Integrate speed instead of multiplying absolute application time by
        # the selected speed (which teleports all batches when changing grade).
        dt = max(0.0, elapsed - last_update) if last_update is not None else 0.0
        fall_travel = _RAIN_FALL_DISTANCES.get(stage_id, 0.0) + dt * vertical_speed
        _RAIN_FALL_DISTANCES[stage_id] = fall_travel
        _RAIN_LAST_ANIMATION_UPDATE[stage_id] = elapsed
        camera_x, camera_y = _active_camera_ground_xy(stage)
        camera_anchor = (
            round(camera_x / RAIN_CAMERA_FOLLOW_GRID_M) * RAIN_CAMERA_FOLLOW_GRID_M,
            round(camera_y / RAIN_CAMERA_FOLLOW_GRID_M) * RAIN_CAMERA_FOLLOW_GRID_M,
        )
        with _session_edit_context(stage):
            rain_root = _valid_prim(stage, RAIN_EFFECT_PATH)
            _set_attribute_if_changed(rain_root, "smartPort:windSpeedMps", wind_speed, "Float")
            _set_attribute_if_changed(
                rain_root,
                "smartPort:windDescription",
                "Calm" if wind_speed < 1.0 else "Breeze" if wind_speed < 6.0 else "Strong Wind" if wind_speed < 12.0 else "Gale",
                "String",
            )
            air_rain = _valid_prim(stage, AIR_RAIN_EFFECT_PATH)
            anchor_missing = not air_rain.GetAttribute("xformOp:translate")
            if _RAIN_CAMERA_ANCHORS.get(stage_id) != camera_anchor or anchor_missing:
                _RAIN_CAMERA_ANCHORS[stage_id] = camera_anchor
                _set_attribute(
                    air_rain,
                    "xformOp:translate",
                    (camera_anchor[0], camera_anchor[1], 0.0),
                    "Double3",
                )
                _set_attribute(
                    air_rain,
                    "xformOpOrder",
                    ["xformOp:translate"],
                    "TokenArray",
                )
            visible_count = int(RAIN_AMOUNT_PROFILES[rain_amount]["visible_layers"])
            _update_rain_point_batches(stage, visible_count, fall_travel, wind_speed, camera_anchor, elapsed)
        return True
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _configure_hud_emission(stage: Any) -> None:
    """Keep textured 3D information panels readable independently of lighting."""

    try:
        from pxr import Sdf, UsdShade  # type: ignore

        for material_path in HUD_EMISSIVE_MATERIAL_PATHS:
            shader_prim = stage.GetPrimAtPath(f"{material_path}/PreviewSurface")
            texture_prim = stage.GetPrimAtPath(f"{material_path}/Texture")
            if not shader_prim or not texture_prim:
                continue
            shader = UsdShade.Shader(shader_prim)
            texture = UsdShade.Shader(texture_prim)
            shader.CreateInput(
                "emissiveColor",
                Sdf.ValueTypeNames.Color3f,
            ).ConnectToSource(texture.ConnectableAPI(), "rgb")
    except (AttributeError, ImportError, RuntimeError):
        pass


def _hide_viewport_light_icons() -> None:
    """Hide light manipulators without changing rendered illumination."""

    try:
        import carb.settings  # type: ignore
        from omni.kit.viewport.actions.actions import (  # type: ignore
            toggle_light_visibility,
        )
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        viewport = get_active_viewport()
        toggle_light_visibility(viewport_api=viewport, visible=False)

        # Also write both current and legacy visibility settings so custom Kit
        # templates that do not register the action listener still hide icons.
        settings = carb.settings.get_settings()
        settings.set_bool("/app/viewport/show/lights", False)
        display_options = int(
            settings.get("/persistent/app/viewport/displayOptions") or 0
        )
        settings.set_int(
            "/persistent/app/viewport/displayOptions",
            display_options & ~(1 << 8),
        )
        if viewport is not None:
            settings.set_bool(
                f"/persistent/app/viewport/{viewport.id}/scene/lights/visible",
                False,
            )
    except (AttributeError, ImportError, RuntimeError):
        pass


def current_environment_mode(stage: Any) -> str:
    try:
        prim = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        attribute = prim.GetAttribute(ENVIRONMENT_MODE_ATTRIBUTE)
        value = attribute.Get() if attribute else None
        return normalize_environment_mode(value)
    except Exception:
        return "day"


def _get_attribute_value(prim: Any, name: str, default: Any) -> Any:
    try:
        attribute = prim.GetAttribute(name)
        value = attribute.Get() if attribute else None
        return default if value is None else value
    except (AttributeError, RuntimeError):
        return default


def _configure_rain_layers(stage: Any, amount: str, enabled: bool) -> None:
    amount = normalize_rain_amount(amount)
    profile = RAIN_AMOUNT_PROFILES[amount]
    visible_count = int(profile["visible_layers"])
    coverage_scale = RAIN_COVERAGE_SCALES[amount]
    splash_count = 0
    mist_count = 0
    if not enabled:
        _RAIN_LAST_ANIMATION_UPDATE.pop(id(stage), None)
        _RAIN_FALL_DISTANCES.pop(id(stage), None)
        _RAIN_CAMERA_ANCHORS.pop(id(stage), None)
        _RAIN_PARTICLE_BATCHES.pop(stage, None)
        _RAIN_LOCAL_LIGHTS.pop(stage, None)
        surfaces = _RAIN_SURFACE_EFFECTS.pop(stage, None)
        if surfaces:
            surfaces.close()
    try:
        rain_root = _valid_prim(stage, RAIN_EFFECT_PATH)
        _set_attribute(rain_root, "smartPort:rainEffectRevision", RAIN_EFFECT_REVISION, "String")
        _set_attribute(rain_root, "smartPort:animationHz", 60, "Int")
        _set_attribute(rain_root, "smartPort:maxRainTriangles", 8000, "Int")
        _set_attribute(rain_root, "smartPort:maxSplashTriangles", 2048, "Int")
        _set_attribute(rain_root, "smartPort:splashCapacity", 128, "Int")
        _set_attribute(rain_root, "smartPort:visibleLayerCount", visible_count, "Int")
        _set_attribute(rain_root, "smartPort:coverageScale", coverage_scale, "Float")
        _set_attribute(rain_root, "smartPort:rainIntensity", float(profile["opacity"]), "Float")
        _set_attribute(rain_root, "smartPort:rainDropCount", int(profile["drop_count"]), "Int")
        _set_attribute(rain_root, "smartPort:rainRate24h", str(profile["rate_mm_24h"]), "String")
        _set_attribute(rain_root, "smartPort:groundWetness", float(profile["wetness"]), "Float")
        _set_attribute(
            rain_root,
            "smartPort:rainDropLengthMeters",
            _range_text(profile["streak_length_m"]),
            "String",
        )
        _set_attribute(
            rain_root,
            "smartPort:rainDropWidthMeters",
            _range_text(profile["streak_width_m"]),
            "String",
        )
        _set_attribute(
            rain_root,
            "smartPort:rainFallSpeedMps",
            float(profile["speed_mps"]),
            "Float",
        )
        _set_attribute(rain_root, "smartPort:visibleSplashLayerCount", splash_count, "Int")
        _set_attribute(rain_root, "smartPort:visibleMistLayerCount", mist_count, "Int")
        shader = _valid_prim(
            stage,
            f"{WEATHER_ROOT_PATH}/Looks/RainStreakMaterial/PreviewSurface",
        )
        _set_attribute(shader, "inputs:opacity", float(profile["opacity"]), "Float")
        _set_attribute(shader, "inputs:emissiveColor", (float(profile["emission"]),) * 3, "Color3f")
        # Small unlit fill stands in for unresolved refracted sky on subpixel
        # drops. Otherwise their back faces appear as black needles in RTX.
        _set_attribute(shader, "inputs:roughness", .65, "Float")
        wet_shader = _valid_prim(
            stage,
            f"{WEATHER_ROOT_PATH}/Looks/WetRoadMaterial/PreviewSurface",
        )
        wetness = float(profile["wetness"])
        _set_attribute(wet_shader, "inputs:opacity", wetness, "Float")
        # Water is dielectric, not metal. Keep a broad highlight instead of
        # turning the painted road into a blue mirror that obscures markings.
        _set_attribute(wet_shader, "inputs:metallic", 0.0, "Float")
        _set_attribute(wet_shader, "inputs:roughness", max(0.30, 0.58 - wetness * 0.55), "Float")
        wet_tone = max(0.018, 0.085 - wetness * 0.075)
        _set_attribute(
            wet_shader,
            "inputs:diffuseColor",
            (wet_tone, wet_tone * 1.08, wet_tone * 1.14),
            "Color3f",
        )
    except (AttributeError, RuntimeError, TypeError):
        # Unit-test doubles and partially loaded stages may not contain the
        # material yet; layer visibility still provides the intended fallback.
        pass
    for layer_index, layer_path in enumerate(RAIN_LAYER_PATHS):
        layer = _valid_prim(stage, layer_path)
        _set_attribute_if_changed(
            layer,
            "xformOp:scale",
            (coverage_scale, coverage_scale, 1.0),
            "Float3",
        )
        _set_attribute_if_changed(
            layer,
            "xformOpOrder",
            ["xformOp:scale"],
            "TokenArray",
        )
        layer_enabled = bool(enabled and layer_index < visible_count)
        visibility = "inherited" if layer_enabled else "invisible"
        _set_attribute_if_changed(
            layer,
            "visibility",
            visibility,
            "Token",
        )
        for phase_path in RAIN_LAYER_PHASE_PATHS[layer_index]:
            phase = stage.GetPrimAtPath(phase_path)
            if phase:
                _set_attribute_if_changed(
                    phase,
                    "visibility",
                    "invisible" if stage in _RAIN_SURFACE_EFFECTS else visibility,
                    "Token",
                )


def apply_weather_controls(
    stage: Any,
    wind_speed_mps: Any = None,
    rain_amount: Any = None,
    fog_amount: Any = None,
) -> dict[str, Any]:
    """Persist weather controls in the session layer and apply them immediately."""

    with _session_edit_context(stage):
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        current_wind = float(
            _get_attribute_value(environment, WIND_SPEED_ATTRIBUTE, DEFAULT_WIND_SPEED_MPS)
        )
        wind = current_wind if wind_speed_mps is None else float(wind_speed_mps)
        if not 0.0 <= wind <= 20.0:
            raise ValueError("wind_speed_mps must be between 0 and 20")
        rain = normalize_rain_amount(
            rain_amount
            if rain_amount is not None
            else _get_attribute_value(environment, RAIN_AMOUNT_ATTRIBUTE, DEFAULT_RAIN_AMOUNT)
        )
        fog = normalize_fog_amount(
            fog_amount
            if fog_amount is not None
            else _get_attribute_value(environment, FOG_AMOUNT_ATTRIBUTE, DEFAULT_FOG_AMOUNT)
        )
        _set_attribute(environment, WIND_SPEED_ATTRIBUTE, wind, "Float")
        _set_attribute(environment, RAIN_AMOUNT_ATTRIBUTE, rain, "Token")
        _set_attribute(environment, FOG_AMOUNT_ATTRIBUTE, fog, "Token")
        mode = current_environment_mode(stage)
        _configure_rain_layers(stage, rain, mode == "rain")
        if mode == "rain":
            _configure_rain_surface_and_lights(stage, rain)
        elif mode == "fog":
            _configure_weather_local_lights(stage)
    _configure_rtx_fog(ENVIRONMENT_PRESETS[mode], fog, stage)
    with _session_edit_context(stage):
        _configure_fog_background(stage, ENVIRONMENT_PRESETS[mode], fog)
        _configure_rain_atmosphere(stage, ENVIRONMENT_PRESETS[mode], rain)
        _configure_render_product_fog(stage, ENVIRONMENT_PRESETS[mode], fog, rain)
        _configure_atmosphere_overlay(stage, ENVIRONMENT_PRESETS[mode], rain, fog)
    from smart_port_time_of_day import reapply_if_set
    reapply_if_set(stage)
    return {
        "wind_speed_mps": wind,
        "rain_amount": rain,
        "rain_label": RAIN_AMOUNT_LABELS[rain],
        "rain_rate_24h": RAIN_AMOUNT_PROFILES[rain]["rate_mm_24h"],
        "rain_drop_count": RAIN_AMOUNT_PROFILES[rain]["drop_count"],
        "rain_drop_length_m": RAIN_AMOUNT_PROFILES[rain]["streak_length_m"],
        "rain_drop_width_m": RAIN_AMOUNT_PROFILES[rain]["streak_width_m"],
        "rain_fall_speed_mps": RAIN_AMOUNT_PROFILES[rain]["speed_mps"],
        "rain_wetness": RAIN_AMOUNT_PROFILES[rain]["wetness"],
        "fog_amount": fog,
        "fog_label": FOG_AMOUNT_LABELS[fog],
    }


def sts_night_light_paths(stage: Any) -> tuple[str, ...]:
    root = stage.GetPrimAtPath('/World/STSControls')
    if not root:
        return ()
    return tuple(str(crane.GetPath()) + '/Gantry/' + assembly + '/NightWorkLights'
                 for crane in root.GetChildren() for assembly in ('Fixed', 'Boom', 'Trolley')
                 if stage.GetPrimAtPath(str(crane.GetPath()) + '/Gantry/' + assembly + '/NightWorkLights'))


def _configure_rtg_floodlights(stage: Any, enabled: bool, work_rtg_only: bool = False) -> None:
    """Keep RTG fixtures visible and switch RTG/STS night work lights.

    The model roots are explicitly restored in every preset so an older
    session-layer visibility edit cannot hide the installed hardware.
    """

    # The fixtures are physical RTG equipment and remain visible by day and
    # night. Only their authored light output follows the environment preset.
    for root_path in RTG_LED_MODEL_ROOT_PATHS:
        _set_attribute(
            _valid_prim(stage, root_path),
            "visibility",
            "inherited",
            "Token",
        )
    for index, root_path in enumerate(RTG_LED_NIGHT_ROOT_PATHS):
        visibility = "inherited" if enabled or (work_rtg_only and index == 0) else "invisible"
        _set_attribute(
            _valid_prim(stage, root_path),
            "visibility",
            visibility,
            "Token",
        )
    for root_path in sts_night_light_paths(stage):
        _set_attribute(stage.GetPrimAtPath(root_path), 'visibility',
                       'inherited' if enabled or work_rtg_only else 'invisible', 'Token')


def apply_environment_mode(stage: Any, mode: Any) -> str:
    """Apply a registered preset and return its normalized key."""

    normalized = normalize_environment_mode(mode)
    preset = ENVIRONMENT_PRESETS[normalized]
    with _session_edit_context(stage):
        if normalized not in ("rain", "fog") or current_environment_mode(stage) != normalized:
            _restore_rain_look(stage)
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        sky = _valid_prim(stage, DAY_SKY_PATH)
        sun = _valid_prim(stage, DAY_SUN_PATH)
        street_lights = _valid_prim(stage, NIGHT_STREET_LIGHTS_PATH)

        _set_attribute(
            environment,
            ENVIRONMENT_MODE_ATTRIBUTE,
            normalized,
            "Token",
        )
        _set_attribute(
            environment,
            WIND_SPEED_ATTRIBUTE,
            _get_attribute_value(environment, WIND_SPEED_ATTRIBUTE, DEFAULT_WIND_SPEED_MPS),
            "Float",
        )
        rain_amount = normalize_rain_amount(
            _get_attribute_value(environment, RAIN_AMOUNT_ATTRIBUTE, DEFAULT_RAIN_AMOUNT)
        )
        fog_amount = normalize_fog_amount(
            _get_attribute_value(environment, FOG_AMOUNT_ATTRIBUTE, DEFAULT_FOG_AMOUNT)
        )
        _set_attribute(environment, RAIN_AMOUNT_ATTRIBUTE, rain_amount, "Token")
        _set_attribute(environment, FOG_AMOUNT_ATTRIBUTE, fog_amount, "Token")
        _set_attribute(sky, "inputs:intensity", preset.sky_intensity, "Float")
        _set_attribute(sky, "inputs:exposure", preset.sky_exposure, "Float")
        _set_attribute(sky, "inputs:color", preset.sky_color, "Color3f")
        _set_attribute(sky, "visibility", "inherited", "Token")
        _set_attribute(sun, "inputs:intensity", preset.sun_intensity, "Float")
        _set_attribute(sun, "inputs:color", preset.sun_color, "Color3f")
        _configure_viewport_background(stage, preset)
        _configure_hud_emission(stage)
        _set_attribute(
            street_lights,
            "visibility",
            "inherited" if preset.street_lights_visible else "invisible",
            "Token",
        )
        _configure_rtg_floodlights(
            stage,
            preset.street_lights_visible and normalized not in ("rain", "fog"),
            work_rtg_only=normalized in ("rain", "fog"),
        )
        _set_attribute(_valid_prim(stage, RAIN_EFFECT_PATH), "visibility", "inherited" if preset.rain_visible else "invisible", "Token")
        _configure_rain_layers(stage, rain_amount, preset.rain_visible)
        if preset.rain_visible:
            _configure_rain_surface_and_lights(stage, rain_amount)
        elif normalized == "fog":
            _configure_weather_local_lights(stage)
    _configure_rtx_fog(preset, fog_amount, stage)
    with _session_edit_context(stage):
        _configure_fog_background(stage, preset, fog_amount)
        _configure_rain_atmosphere(stage, preset, rain_amount)
        _configure_render_product_fog(stage, preset, fog_amount, rain_amount)
        _configure_atmosphere_overlay(stage, preset, rain_amount, fog_amount)
    _hide_viewport_light_icons()
    from smart_port_time_of_day import reapply_if_set
    reapply_if_set(stage)
    return normalized
