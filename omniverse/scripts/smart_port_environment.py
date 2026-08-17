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
RAIN_ANIMATION_INTERVAL_SECONDS = 1.0 / 24.0
RAIN_CAMERA_FOLLOW_GRID_M = 10.0
RAIN_EMITTER_MIN_Z_M = 20.0
# Stop visibly above the road surface. With splashes disabled, ending exactly
# at Z=0.15 m makes the last frame of a streak look like a rod planted in the
# ground. The extra clearance is only a visual cap; it still never penetrates
# the yard or water surface.
RAIN_SURFACE_CLIP_Z_M = 0.55
RAIN_STREAK_MAX_LENGTHS_M = (0.55, 0.90, 1.35, 2.00)
RAIN_ANIMATION_PATH = RAIN_LAYER_PATHS[0]
ENVIRONMENT_MODE_ATTRIBUTE = "smartPort:environmentMode"
WIND_SPEED_ATTRIBUTE = "smartPort:windSpeedMps"
RAIN_AMOUNT_ATTRIBUTE = "smartPort:rainAmount"
FOG_AMOUNT_ATTRIBUTE = "smartPort:fogAmount"
DEFAULT_WIND_SPEED_MPS = 4.5
DEFAULT_RAIN_AMOUNT = "medium"
DEFAULT_FOG_AMOUNT = "medium"
WEATHER_BUILD_ID = "2026-08-14-rtg-weather-contrast-v9"
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
        "visible_layers": 1, "opacity": 0.28, "emission": 0.03,
        "speed_mps": 6.0, "rate_mm_24h": "<10", "wetness": 0.05,
        "drop_count": 100,
        "streak_length_m": (0.25, 0.55), "streak_width_m": (0.006, 0.010),
    },
    "medium": {
        "visible_layers": 2, "opacity": 0.46, "emission": 0.06,
        "speed_mps": 9.0, "rate_mm_24h": "10-25", "wetness": 0.22,
        "drop_count": 350,
        "streak_length_m": (0.45, 0.90), "streak_width_m": (0.009, 0.014),
    },
    "heavy": {
        "visible_layers": 3, "opacity": 0.64, "emission": 0.12,
        "speed_mps": 14.0, "rate_mm_24h": "25-50", "wetness": 0.48,
        "drop_count": 850,
        "streak_length_m": (0.70, 1.35), "streak_width_m": (0.013, 0.022),
    },
    "torrential": {
        "visible_layers": 4, "opacity": 0.84, "emission": 0.20,
        "speed_mps": 20.0, "rate_mm_24h": "50-100; short burst >16 mm/h", "wetness": 0.82,
        "drop_count": 2000,
        "streak_length_m": (1.05, 2.00), "streak_width_m": (0.018, 0.032),
    },
}
RAIN_BACKGROUND_TEXTURE_LUMINANCE = {
    "light": 0.88,
    "medium": 0.70,
    "heavy": 0.52,
    "torrential": 0.34,
}
RAIN_COVERAGE_SCALES = {
    "light": 1.0,
    "medium": 1.0,
    "heavy": 1.0,
    "torrential": 1.0,
}
# Rainfall itself now drives a separate atmospheric veil.  This makes every
# grade legible even in a wide overview where individual streaks are small.
RAIN_ATMOSPHERE_PROFILES = {
    # sky scale, sun scale, background, visibility m, distance density,
    # height density
    "light": (1.05, 0.90, (0.34, 0.41, 0.46), 420.0, 0.08, 0.015),
    "medium": (0.82, 0.58, (0.24, 0.31, 0.36), 240.0, 0.30, 0.060),
    "heavy": (0.58, 0.30, (0.15, 0.21, 0.25), 110.0, 0.85, 0.16),
    "torrential": (0.34, 0.10, (0.075, 0.11, 0.14), 45.0, 1.80, 0.30),
}
_RAIN_LAST_ANIMATION_UPDATE: dict[int, float] = {}
_RAIN_CAMERA_ANCHORS: dict[int, tuple[float, float]] = {}
_ATMOSPHERE_OVERLAY_LAST_UPDATE: dict[int, float] = {}
FOG_AMOUNT_LABELS = {
    "light": "Light Fog",
    "medium": "Moderate Fog",
    "heavy": "Heavy Fog",
}
FOG_AMOUNT_PROFILES = {
    # distance scale, density scale, height scale; intentionally separated
    # enough to remain visible in a large yard camera.
    "light": (1.20, 0.18, 0.12),
    "medium": (0.65, 0.65, 0.45),
    "heavy": (0.28, 2.00, 1.80),
}
FOG_AMOUNT_BACKGROUND_COLORS = {
    "light": (0.40, 0.48, 0.53),
    "medium": (0.52, 0.58, 0.61),
    "heavy": (0.70, 0.73, 0.73),
}
# RTX Simple Fog is not consistently evaluated on every live viewport
# RenderProduct. This single camera-facing quad is a deliberately cheap
# visual fallback. Its opacity is kept low so it communicates visibility
# without turning the yard into a solid translucent wall.
ATMOSPHERE_OVERLAY_OPACITIES = {
    "rain": {
        "light": 0.025,
        "medium": 0.080,
        "heavy": 0.180,
        "torrential": 0.320,
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
        sky_intensity=390.0,
        sky_exposure=-0.25,
        sky_color=(0.58, 0.68, 0.76),
        sun_intensity=720.0,
        sun_color=(0.70, 0.76, 0.80),
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
        sky_color=(0.72, 0.77, 0.78),
        sun_intensity=460.0,
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


def _configure_viewport_background(stage: Any, preset: EnvironmentPreset) -> None:
    """Force a visible RTX background instead of relying on DomeLight display."""

    render_products = []
    try:
        render_products = [
            prim for prim in stage.Traverse() if prim.GetTypeName() == "RenderProduct"
        ]
    except (AttributeError, TypeError):
        pass

    use_weather_texture = (
        preset.key in WEATHER_TEXTURE_BACKGROUND_MODES
        and DAY_BACKGROUND_TEXTURE.is_file()
    )
    texture_luminance_scale = {
        "day": 1.0,
        "rain": 0.70,
        # A darker, desaturated sky texture makes the fog layer read as
        # overcast instead of looking like a clear blue day.
        "fog": FOG_BACKGROUND_TEXTURE_LUMINANCE,
    }.get(preset.key, 1.0)
    for render_product in render_products:
        if use_weather_texture:
            _set_attribute(
                render_product,
                "omni:rtx:background:source:type",
                "texture",
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
        elif preset.key == "day":
            # Let RTX use the rendered DomeLight. This is what displays the
            # authored harbor sky texture and avoids the black texture override
            # seen with this Kit version.
            _clear_attribute(render_product, "omni:rtx:background:source:type")
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

    # Viewports created after the stage was saved may use a transient render
    # product. Mirror the same override into the live RTX settings as a fallback.
    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        if use_weather_texture:
            # RTX background override: 1 forces the assigned texture. This
            # keeps rain and fog on a textured overcast sky instead of a flat
            # color while the separate RTX fog layer controls visibility.
            settings.set_int("/rtx/background/source/type", 1)
            settings.set_string(
                "/rtx/background/source/texture/path",
                DAY_BACKGROUND_TEXTURE.as_posix(),
            )
            settings.set_float(
                "/rtx/background/source/texture/luminanceScale",
                texture_luminance_scale,
            )
        elif preset.key == "day":
            settings.set_int("/rtx/background/source/type", 0)
            if DAY_BACKGROUND_TEXTURE.is_file():
                settings.set_string(
                    "/rtx/background/source/texture/path",
                    DAY_BACKGROUND_TEXTURE.as_posix(),
                )
            else:
                settings.set_int("/rtx/background/source/type", 2)
                settings.set_float_array(
                    "/rtx/background/source/color",
                    list(DAY_BACKGROUND_FALLBACK_COLOR),
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

    if not preset.fog_enabled:
        return
    amount = normalize_fog_amount(fog_amount)
    color = FOG_AMOUNT_BACKGROUND_COLORS[amount]
    sky_scale, sun_scale = {
        "light": (1.08, 0.92),
        "medium": (0.78, 0.48),
        "heavy": (0.48, 0.12),
    }[amount]
    try:
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
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
        if DAY_BACKGROUND_TEXTURE.is_file():
            background_luminance = RAIN_BACKGROUND_TEXTURE_LUMINANCE[amount]
            for render_product in (
                prim for prim in stage.Traverse() if prim.GetTypeName() == "RenderProduct"
            ):
                _set_attribute(
                    render_product,
                    "omni:rtx:background:source:type",
                    "texture",
                    "Token",
                )
                _set_attribute(
                    render_product,
                    "omni:rtx:background:source:texture:luminanceScale",
                    background_luminance,
                    "Float",
                )
    except (AttributeError, RuntimeError, TypeError):
        pass

    try:
        import carb.settings  # type: ignore

        settings = carb.settings.get_settings()
        settings.set_bool("/rtx/fog/enabled", True)
        settings.set_float_array("/rtx/fog/fogColor", list(background_color))
        settings.set_float("/rtx/fog/fogStartDist", 4.0 if amount == "torrential" else 12.0)
        settings.set_float("/rtx/fog/fogEndDist", visibility_m)
        settings.set_float("/rtx/fog/fogDistanceDensity", distance_density)
        settings.set_float("/rtx/fog/fogHeightDensity", height_density)
        if DAY_BACKGROUND_TEXTURE.is_file():
            settings.set_int("/rtx/background/source/type", 1)
            settings.set_string(
                "/rtx/background/source/texture/path",
                DAY_BACKGROUND_TEXTURE.as_posix(),
            )
            settings.set_float(
                "/rtx/background/source/texture/luminanceScale",
                RAIN_BACKGROUND_TEXTURE_LUMINANCE[amount],
            )
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

    if not preset.fog_enabled:
        return
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
                True,
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
                "omni:rtx:fog:startDist",
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
    enabled = mode in ATMOSPHERE_OVERLAY_OPACITIES
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
            _set_attribute(
                overlay,
                "visibility",
                "inherited"
                if inside_weather_zone and mode in ATMOSPHERE_OVERLAY_OPACITIES
                else "invisible",
                "Token",
            )
            rain_root = stage.GetPrimAtPath(RAIN_EFFECT_PATH)
            if rain_root and rain_root.IsValid():
                _set_attribute(
                    rain_root,
                    "visibility",
                    "inherited"
                    if inside_weather_zone and mode == "rain"
                    else "invisible",
                    "Token",
                )
            _set_attribute(
                overlay,
                "xformOp:transform",
                world_matrix,
                "Matrix4d",
            )
            _set_attribute(
                overlay,
                "xformOpOrder",
                ["xformOp:transform"],
                "TokenArray",
            )
        _ATMOSPHERE_OVERLAY_LAST_UPDATE[stage_id] = elapsed
        return True
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return False


def _configure_rtx_fog(preset: EnvironmentPreset, fog_amount: str = DEFAULT_FOG_AMOUNT) -> None:
    """Apply low-cost RTX Simple Fog settings for rain and fog presets."""

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
    """Return active viewport camera XY for the local rain volume."""

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
        return (float(translate[0]), float(translate[1]))
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return (0.0, 0.0)


def update_weather_animation(stage: Any, elapsed_seconds: float) -> bool:
    """Advance weather visuals independently of the paused RTG timeline."""

    try:
        mode = current_environment_mode(stage)
        if mode not in ("rain", "fog"):
            return False
        overlay_updated = _update_atmosphere_overlay_transform(stage, elapsed_seconds)
        if mode == "fog":
            return overlay_updated
        environment = _valid_prim(stage, ENVIRONMENT_ROOT_PATH)
        wind_speed = float(
            _get_attribute_value(environment, WIND_SPEED_ATTRIBUTE, DEFAULT_WIND_SPEED_MPS)
        )
        rain_amount = normalize_rain_amount(
            _get_attribute_value(environment, RAIN_AMOUNT_ATTRIBUTE, DEFAULT_RAIN_AMOUNT)
        )
        vertical_speed = float(RAIN_AMOUNT_PROFILES[rain_amount]["speed_mps"])
        elapsed = float(elapsed_seconds)
        stage_id = id(stage)
        last_update = _RAIN_LAST_ANIMATION_UPDATE.get(stage_id)
        if (
            last_update is not None
            and elapsed >= last_update
            and elapsed - last_update < RAIN_ANIMATION_INTERVAL_SECONDS
        ):
            return True
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
            anchor_missing = air_rain.GetAttribute("xformOp:translate") is None
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
            for layer_index, phase_paths in enumerate(RAIN_LAYER_PHASE_PATHS):
                # Three height phases per grade provide continuous rainfall.
                # The travel limit uses that grade's longest streak, so no
                # vertex can cross the ground/water clipping height.
                fall_distance = (
                    RAIN_EMITTER_MIN_Z_M
                    - RAIN_STREAK_MAX_LENGTHS_M[layer_index]
                    - RAIN_SURFACE_CLIP_Z_M
                )
                for phase_index, path in enumerate(phase_paths):
                    phase_offset = phase_index / len(phase_paths) + layer_index * 0.071
                    cycle = (
                        elapsed * vertical_speed / fall_distance + phase_offset
                    ) % 1.0
                    gust = math.sin(
                        elapsed * (1.15 + layer_index * 0.21)
                        + phase_index * 0.83
                    )
                    travel_scale = fall_distance / 12.0
                    lateral_x = (
                        cycle * wind_speed * 0.82 * travel_scale
                        + gust * wind_speed * 0.16
                    )
                    # The modeled harbor water lies on the negative-X side of
                    # the yard shoreline; never drift a land rain batch back
                    # over that edge where below-surface streaks are exposed.
                    lateral_x = max(0.0, lateral_x)
                    lateral_y = (
                        cycle * wind_speed * 0.24 * travel_scale
                        + gust * wind_speed * 0.05
                    )
                    translation = (
                        lateral_x,
                        lateral_y,
                        -cycle * fall_distance,
                    )
                    rain_mesh = _valid_prim(stage, path)
                    _set_attribute(
                        rain_mesh,
                        "xformOp:translate",
                        translation,
                        "Double3",
                    )
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
    try:
        rain_root = _valid_prim(stage, RAIN_EFFECT_PATH)
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
        _set_attribute(shader, "inputs:emissiveColor", (float(profile["emission"]), float(profile["emission"] * 1.25), float(profile["emission"] * 1.5)), "Color3f")
        wet_shader = _valid_prim(
            stage,
            f"{WEATHER_ROOT_PATH}/Looks/WetRoadMaterial/PreviewSurface",
        )
        wetness = float(profile["wetness"])
        _set_attribute(wet_shader, "inputs:opacity", wetness, "Float")
        _set_attribute(wet_shader, "inputs:metallic", min(0.18, wetness * 0.4), "Float")
        _set_attribute(wet_shader, "inputs:roughness", max(0.42, 0.72 - wetness * 0.45), "Float")
        wet_tone = max(0.018, 0.085 - wetness * 0.075)
        _set_attribute(
            wet_shader,
            "inputs:diffuseColor",
            (wet_tone, wet_tone * 1.35, wet_tone * 1.65),
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
                    visibility,
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
    _configure_rtx_fog(ENVIRONMENT_PRESETS[mode], fog)
    _configure_fog_background(stage, ENVIRONMENT_PRESETS[mode], fog)
    _configure_rain_atmosphere(stage, ENVIRONMENT_PRESETS[mode], rain)
    _configure_render_product_fog(stage, ENVIRONMENT_PRESETS[mode], fog, rain)
    _configure_atmosphere_overlay(stage, ENVIRONMENT_PRESETS[mode], rain, fog)
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


def _configure_rtg_floodlights(stage: Any, enabled: bool) -> None:
    """Keep LED fixtures visible and switch their night downlights.

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
    visibility = "inherited" if enabled else "invisible"
    for root_path in RTG_LED_NIGHT_ROOT_PATHS:
        _set_attribute(
            _valid_prim(stage, root_path),
            "visibility",
            visibility,
            "Token",
        )


def apply_environment_mode(stage: Any, mode: Any) -> str:
    """Apply a registered preset and return its normalized key."""

    normalized = normalize_environment_mode(mode)
    preset = ENVIRONMENT_PRESETS[normalized]
    with _session_edit_context(stage):
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
        _configure_rtg_floodlights(stage, preset.street_lights_visible)
        _set_attribute(_valid_prim(stage, RAIN_EFFECT_PATH), "visibility", "inherited" if preset.rain_visible else "invisible", "Token")
        _configure_rain_layers(stage, rain_amount, preset.rain_visible)
    _configure_rtx_fog(preset, fog_amount)
    _configure_fog_background(stage, preset, fog_amount)
    _configure_rain_atmosphere(stage, preset, rain_amount)
    _configure_render_product_fog(stage, preset, fog_amount, rain_amount)
    _configure_atmosphere_overlay(stage, preset, rain_amount, fog_amount)
    _hide_viewport_light_icons()
    return normalized
