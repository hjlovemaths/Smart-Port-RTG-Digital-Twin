"""Mount four LED floodlights on each RTG upper long girder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "omniverse" / "scenes" / "rtg_led_floodlights.usda"
LED_ASSET = "../assets/resource/led/RTG_LED_Floodlight.usdc"
LED_MODEL_ROOT = "SMART_PORT_RTG_LED_MODELS"
LED_NIGHT_ROOT = "SMART_PORT_RTG_LED_NIGHT_LIGHTS"
LED_SCALE = (0.0048, 0.006, 0.006)
# The source fixture includes its U bracket and screw hardware.  These two
# rotations keep that bracket pressed against the girder while pointing the
# light's -Z axis down and inward from each side.
NEAR_LED_ROTATION = (55.0, 0.0, 0.0)
FAR_LED_ROTATION = (-125.0, 180.0, 0.0)
# Keep the physical fixture angle unchanged, but aim the photometric lights
# more steeply at the yard. From Z=7.53 these vectors meet the ground about
# 2.7 m inward, almost exactly between the two 5.3 m-spaced girders.
NEAR_LIGHT_ROTATION = (20.0, 0.0, 0.0)
FAR_LIGHT_ROTATION = (-160.0, 180.0, 0.0)
# The main girder spans Z=7.74..8.36.  The source bracket reaches 0.403 m
# above its origin after rotation, so Z=7.33 seats the screw bar just below
# the girder underside instead of burying the fixture inside the box section.
LED_TRANSLATE_Z_M = 7.33
LED_LIGHT_Z_M = 7.53
LED_LENS_INWARD_OFFSET_M = 0.242
LED_WORLD_FRACTIONS = (0.18, 0.393333, 0.606667, 0.82)
LED_LIGHT_INTENSITY = 60000.0
LED_LIGHT_EXPOSURE = 2.0
LED_LIGHT_RADIUS_M = 0.24
LED_LIGHT_CONE_ANGLE_DEG = 70.0
LED_LIGHT_CONE_SOFTNESS = 0.65


@dataclass(frozen=True)
class RtgPlacement:
    root_name: str
    nested_parent: str | None
    style_prefix: str
    local_x_range: tuple[float, float]
    beam_local_y_centers: tuple[float, float]


RTG_PLACEMENTS = (
    RtgPlacement(
        root_name="RTG_PRIMARY_DYNAMIC",
        nested_parent="ANIM_CTRL_RTG_GANTRY_TRAVEL",
        style_prefix="",
        local_x_range=(-6.399999, 4.805813),
        # Parent-local centres of the two moving upper long girders.
        beam_local_y_centers=(-7.65, -2.35),
    ),
    RtgPlacement(
        root_name="RTG_STATIC_LEFT",
        nested_parent=None,
        style_prefix="RTG_STATIC_PRIMARY_STYLE_left_",
        local_x_range=(-45.564694, -34.443514),
        beam_local_y_centers=(6.641, 11.941),
    ),
    RtgPlacement(
        root_name="RTG_STATIC_RIGHT",
        nested_parent=None,
        style_prefix="RTG_STATIC_PRIMARY_STYLE_right_",
        local_x_range=(47.435304, 58.556485),
        beam_local_y_centers=(6.641, 11.941),
    ),
)


def fmt(value: float) -> str:
    rounded = round(value, 6)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


def lamp_local_positions(
    spec: RtgPlacement,
) -> tuple[tuple[str, float, float, tuple[float, float, float]], ...]:
    local_min, local_max = spec.local_x_range
    span = local_max - local_min
    positions = []
    for side_name, beam_y, rotation in zip(
        ("Near", "Far"),
        spec.beam_local_y_centers,
        (NEAR_LED_ROTATION, FAR_LED_ROTATION),
    ):
        for index, fraction in enumerate(LED_WORLD_FRACTIONS, start=1):
            positions.append(
                (f"{side_name}_{index:02d}", local_min + span * fraction, beam_y, rotation)
            )
    return tuple(positions)


def old_floodlight_overrides(spec: RtgPlacement) -> str:
    overrides = []
    for part in ("glass", "housing", "bracket"):
        for index in range(5):
            name = f"{spec.style_prefix}under_beam_floodlight_{part}_{index}"
            overrides.append(
                f'''            over "{name}" (
                active = false
            )
            {{
            }}'''
            )
    return "\n\n".join(overrides)


def led_models(spec: RtgPlacement) -> str:
    instances = []
    for name, x, beam_y, rotation in lamp_local_positions(spec):
        instances.append(
            f'''                def Xform "{name}" (
                    prepend references = @{LED_ASSET}@
                )
                {{
                    double3 xformOp:translate = ({fmt(x)}, {fmt(beam_y)}, {fmt(LED_TRANSLATE_Z_M)})
                    float3 xformOp:rotateXYZ = ({fmt(rotation[0])}, {fmt(rotation[1])}, {fmt(rotation[2])})
                    float3 xformOp:scale = ({fmt(LED_SCALE[0])}, {fmt(LED_SCALE[1])}, {fmt(LED_SCALE[2])})
                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]
                }}'''
        )
    return f'''            def Xform "{LED_MODEL_ROOT}"
            {{
                custom int lampsPerBeam = 4
                custom int totalLampCount = 8
                custom double girderContactHeight = 7.73
                custom string attachment = "Source U bracket and screw bar fixed to both upper long girders"
                custom string mountHardware = "The LED_Floodlight source asset supplies the visible bracket and screws"
{chr(10).join(instances)}
            }}'''


def led_night_group(spec: RtgPlacement) -> str:
    lights = []
    for name, x, beam_y, rotation in lamp_local_positions(spec):
        light_rotation = (
            NEAR_LIGHT_ROTATION if name.startswith("Near_") else FAR_LIGHT_ROTATION
        )
        lens_y = beam_y + (
            LED_LENS_INWARD_OFFSET_M if name.startswith("Near_") else -LED_LENS_INWARD_OFFSET_M
        )
        lights.append(
            f'''                def DiskLight "{name}_Downlight" (
                    prepend apiSchemas = ["ShapingAPI", "ShadowAPI"]
                )
                {{
                    bool inputs:enableColorTemperature = true
                    float inputs:colorTemperature = 4200
                    color3f inputs:color = (1, 1, 1)
                    float inputs:intensity = {fmt(LED_LIGHT_INTENSITY)}
                    float inputs:exposure = {fmt(LED_LIGHT_EXPOSURE)}
                    bool inputs:normalize = true
                    float inputs:radius = {fmt(LED_LIGHT_RADIUS_M)}
                    float inputs:shaping:cone:angle = {fmt(LED_LIGHT_CONE_ANGLE_DEG)}
                    float inputs:shaping:cone:softness = {fmt(LED_LIGHT_CONE_SOFTNESS)}
                    float inputs:shaping:focus = 0.1
                    bool inputs:shadow:enable = false
                    double3 xformOp:translate = ({fmt(x)}, {fmt(lens_y)}, {fmt(LED_LIGHT_Z_M)})
                    float3 xformOp:rotateXYZ = ({fmt(light_rotation[0])}, {fmt(light_rotation[1])}, {fmt(light_rotation[2])})
                    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]
                }}'''
        )
    return f'''            def Xform "{LED_NIGHT_ROOT}"
            {{
                token visibility = "invisible"
                custom int effectiveLightCount = 8
                custom string aimDirection = "Near and far rows converge on the ground beneath the RTG center"
                custom string performanceRule = "Each mounted LED owns one shadow-free DiskLight; all eight are night-only"

{chr(10).join(lights)}
            }}'''


def rtg_override(spec: RtgPlacement) -> str:
    body = "\n\n".join(
        (old_floodlight_overrides(spec), led_models(spec), led_night_group(spec))
    )
    if spec.nested_parent:
        body = f'''            over Xform "{spec.nested_parent}"
            {{
{body}
            }}'''
    return f'''        over Xform "{spec.root_name}"
        {{
{body}
        }}'''


def build_layer() -> str:
    overrides = "\n\n".join(rtg_override(spec) for spec in RTG_PLACEMENTS)
    return f'''#usda 1.0
(
    metersPerUnit = 1
    upAxis = "Z"
)

over Xform "World"
{{
    over Xform "PortAndRTG"
    {{
{overrides}
    }}
}}
'''


def main() -> None:
    OUTPUT.write_text(build_layer(), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
