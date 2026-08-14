"""Generate the lightweight rain and wet-road layer for Smart Port."""

from __future__ import annotations

import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "omniverse" / "scenes" / "weather_effects.usda"
RAIN_LAYER_DROP_COUNTS = (100, 250, 500, 1150)
RAIN_DROP_COUNT = sum(RAIN_LAYER_DROP_COUNTS)
# Each cumulative layer represents the additional character of the next rain
# grade.  Keeping the ribbons thin avoids the old "white rods" look, while
# longer streaks in the dense layers make heavy/torrential rain immediately
# distinguishable in a wide yard camera.
RAIN_LAYER_STREAK_PROFILES = (
    ((0.35, 0.65), (0.008, 0.014), 0.055),
    ((0.75, 1.25), (0.012, 0.022), 0.100),
    ((1.40, 2.30), (0.018, 0.032), 0.165),
    ((2.50, 4.20), (0.030, 0.055), 0.260),
)
SPLASH_LAYER_COUNTS = ()
SPLASH_LAYER_SIZE_PROFILES = (
    ((0.035, 0.075), (0.025, 0.060)),
    ((0.050, 0.105), (0.040, 0.085)),
    ((0.070, 0.145), (0.060, 0.125)),
    ((0.100, 0.220), (0.090, 0.190)),
)
MIST_LAYER_COUNTS = ()
MIST_LAYER_SIZE_PROFILES = (
    ((0.35, 0.85), (0.12, 0.32)),
    ((0.65, 1.50), (0.20, 0.55)),
)
RAIN_SEED = 20260813
AIR_RAIN_X_RANGE = (-40.0, 40.0)
AIR_RAIN_Y_RANGE = (-40.0, 40.0)
GROUND_EFFECT_X_RANGE = (-35.0, 35.0)
GROUND_EFFECT_Y_RANGE = (-35.0, 35.0)
RAIN_Z_RANGE = (20.0, 28.0)
RAIN_PHASE_COUNT = 3
RAIN_CYCLE_FRAMES = 12
STAGE_END_TIME_CODE = 925
ROAD_X_RANGES = (
    (-85.875, -70.0),
    (-50.0, -40.0),
    (-20.0, -10.0),
    (10.0, 20.0),
    (40.0, 50.0),
    (70.0, 80.0),
    (108.75, 150.0),
)
CROSS_ROAD_CENTERS_Y = (-5.375, 126.05, 256.30, 386.55, 496.30)


def fmt(value: float) -> str:
    rounded = round(value, 4)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _mesh_data(quads: list[tuple[tuple[float, float, float], ...]]) -> str:
    points = [point for quad in quads for point in quad]
    point_text = ",\n".join(
        f"                    ({fmt(x)}, {fmt(y)}, {fmt(z)})"
        for x, y, z in points
    )
    counts = ", ".join("4" for _ in quads)
    indices = ", ".join(str(index) for index in range(len(points)))
    return f"""int[] faceVertexCounts = [{counts}]
            int[] faceVertexIndices = [{indices}]
            point3f[] points = [
{point_text}
            ]"""


def rain_streak_quads() -> list[tuple[tuple[float, float, float], ...]]:
    rng = random.Random(RAIN_SEED)
    quads = []
    for drop_count, (length_range, width_range, slant_ratio) in zip(
        RAIN_LAYER_DROP_COUNTS,
        RAIN_LAYER_STREAK_PROFILES,
    ):
        for _ in range(drop_count):
            x = rng.uniform(*AIR_RAIN_X_RANGE)
            y = rng.uniform(*AIR_RAIN_Y_RANGE)
            z = rng.uniform(*RAIN_Z_RANGE)
            length = rng.uniform(*length_range)
            width = rng.uniform(*width_range)
            wind_x = length * slant_ratio
            wind_y = length * slant_ratio * 0.34
            bottom = (x + wind_x, y + wind_y, z - length)
            top = (x, y, z)
            # Two crossed ribbons keep each thin streak visible from every
            # yard camera without making the rain itself look thick.
            quads.append(
                (
                    (top[0] - width, top[1], top[2]),
                    (top[0] + width, top[1], top[2]),
                    (bottom[0] + width, bottom[1], bottom[2]),
                    (bottom[0] - width, bottom[1], bottom[2]),
                )
            )
            quads.append(
                (
                    (top[0], top[1] - width, top[2]),
                    (top[0], top[1] + width, top[2]),
                    (bottom[0], bottom[1] + width, bottom[2]),
                    (bottom[0], bottom[1] - width, bottom[2]),
                )
            )
    return quads


def splash_layer_quads() -> list[list[tuple[tuple[float, float, float], ...]]]:
    """Build crossed four-arm crowns that read as raindrop ground impacts."""

    rng = random.Random(RAIN_SEED + 17)
    layers = []
    ground_z = 0.145
    for count, (radius_range, height_range) in zip(
        SPLASH_LAYER_COUNTS,
        SPLASH_LAYER_SIZE_PROFILES,
    ):
        quads = []
        for _ in range(count):
            x = rng.uniform(*GROUND_EFFECT_X_RANGE)
            y = rng.uniform(*GROUND_EFFECT_Y_RANGE)
            radius = rng.uniform(*radius_range)
            height = rng.uniform(*height_range)
            arm_width = max(0.006, radius * 0.11)
            for dx, dy in ((1.0, 0.0), (0.0, 1.0)):
                nx, ny = -dy * arm_width, dx * arm_width
                for sign in (-1.0, 1.0):
                    tip_x = x + dx * radius * sign
                    tip_y = y + dy * radius * sign
                    quads.append(
                        (
                            (x - nx, y - ny, ground_z),
                            (x + nx, y + ny, ground_z),
                            (tip_x + nx * 0.35, tip_y + ny * 0.35, ground_z + height),
                            (tip_x - nx * 0.35, tip_y - ny * 0.35, ground_z + height),
                        )
                    )
        layers.append(quads)
    return layers


def mist_layer_quads() -> list[list[tuple[tuple[float, float, float], ...]]]:
    """Build crossed translucent diamonds for heavy-rain impact spray."""

    rng = random.Random(RAIN_SEED + 31)
    layers = []
    ground_z = 0.16
    for count, (width_range, height_range) in zip(
        MIST_LAYER_COUNTS,
        MIST_LAYER_SIZE_PROFILES,
    ):
        quads = []
        for _ in range(count):
            x = rng.uniform(*GROUND_EFFECT_X_RANGE)
            y = rng.uniform(*GROUND_EFFECT_Y_RANGE)
            width = rng.uniform(*width_range)
            height = rng.uniform(*height_range)
            for axis in ("x", "y"):
                if axis == "x":
                    quads.append(
                        (
                            (x, y, ground_z),
                            (x - width * 0.5, y, ground_z + height * 0.45),
                            (x, y, ground_z + height),
                            (x + width * 0.5, y, ground_z + height * 0.45),
                        )
                    )
                else:
                    quads.append(
                        (
                            (x, y, ground_z),
                            (x, y - width * 0.5, ground_z + height * 0.45),
                            (x, y, ground_z + height),
                            (x, y + width * 0.5, ground_z + height * 0.45),
                        )
                    )
        layers.append(quads)
    return layers


def wet_road_quads() -> list[tuple[tuple[float, float, float], ...]]:
    z = 0.126
    quads = [
        ((x0, -10.25, z), (x1, -10.25, z), (x1, 500.0, z), (x0, 500.0, z))
        for x0, x1 in ROAD_X_RANGES
    ]
    for center_y in CROSS_ROAD_CENTERS_Y:
        y0 = center_y - 3.7
        y1 = center_y + 3.7
        quads.append(((-101.75, y0, z), (125.5, y0, z), (125.5, y1, z), (-101.75, y1, z)))
    return quads


def rain_translation_time_samples() -> str:
    """Legacy authoring helper retained for generated-layer compatibility."""

    samples = []
    for start in range(1, STAGE_END_TIME_CODE + 1, RAIN_CYCLE_FRAMES):
        end = min(start + RAIN_CYCLE_FRAMES - 1, STAGE_END_TIME_CODE)
        samples.append(f"                {start}: (0, 0, 0)")
        if end > start:
            samples.append(f"                {end}: (1.75, 0.6, -11)")
    return ",\n".join(samples)


def rain_layer_usda(
    name: str,
    quads: list[tuple[tuple[float, float, float], ...]],
    drop_count: int,
    length_range: tuple[float, float],
) -> str:
    drop_counts = [drop_count // RAIN_PHASE_COUNT] * RAIN_PHASE_COUNT
    for index in range(drop_count % RAIN_PHASE_COUNT):
        drop_counts[index] += 1
    phase_blocks = []
    quad_cursor = 0
    for phase_index, phase_drop_count in enumerate(drop_counts, start=1):
        phase_quad_count = phase_drop_count * 2
        phase_quads = quads[quad_cursor : quad_cursor + phase_quad_count]
        quad_cursor += phase_quad_count
        phase_blocks.append(
            f'''                def Mesh "Phase{phase_index:02d}"
                {{
                    custom int smartPort:dropCount = {phase_drop_count}
                    uniform bool doubleSided = true
                    rel material:binding = </World/SMART_PORT_WEATHER/Looks/RainStreakMaterial>
                    uniform token orientation = "rightHanded"
                    double3 xformOp:translate = (0, 0, 0)
                    uniform token[] xformOpOrder = ["xformOp:translate"]
{_mesh_data(phase_quads)}
                    uniform token subdivisionScheme = "none"
                }}'''
        )
    return f'''            def Xform "{name}"
            {{
                custom int smartPort:dropCount = {drop_count}
                custom string smartPort:streakLengthMeters = "{fmt(length_range[0])}-{fmt(length_range[1])}"
                custom int smartPort:phaseCount = {RAIN_PHASE_COUNT}
{chr(10).join(phase_blocks)}
            }}'''


def ground_effect_layer_usda(
    name: str,
    quads: list[tuple[tuple[float, float, float], ...]],
    effect_count: int,
    count_attribute: str,
    material_name: str,
) -> str:
    return f'''            def Mesh "{name}"
            {{
                custom int smartPort:{count_attribute} = {effect_count}
                uniform bool doubleSided = true
                rel material:binding = </World/SMART_PORT_WEATHER/Looks/{material_name}>
                uniform token orientation = "rightHanded"
                double3 xformOp:translate = (0, 0, 0)
                float3 xformOp:scale = (1, 1, 1)
                uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
{_mesh_data(quads)}
                uniform token subdivisionScheme = "none"
            }}'''


def build_usda() -> str:
    all_rain_quads = rain_streak_quads()
    rain_layers = []
    quad_cursor = 0
    for index, drop_count in enumerate(RAIN_LAYER_DROP_COUNTS, start=1):
        quad_count = drop_count * 2
        length_range = RAIN_LAYER_STREAK_PROFILES[index - 1][0]
        rain_layers.append(
            rain_layer_usda(
                f"RainLayer{index:02d}",
                all_rain_quads[quad_cursor : quad_cursor + quad_count],
                drop_count,
                length_range,
            )
        )
        quad_cursor += quad_count
    wet_mesh = _mesh_data(wet_road_quads())
    cumulative_counts = []
    running_count = 0
    for drop_count in RAIN_LAYER_DROP_COUNTS:
        running_count += drop_count
        cumulative_counts.append(running_count)
    rain_layer_description = (
        f"Light={cumulative_counts[0]}; "
        f"Moderate={cumulative_counts[1]} cumulative; "
        f"Heavy={cumulative_counts[2]} cumulative; "
        f"Torrential={cumulative_counts[3]} cumulative"
    )
    splash_layers = splash_layer_quads()
    splash_layer_text = []
    for index, (count, quads) in enumerate(
        zip(SPLASH_LAYER_COUNTS, splash_layers),
        start=1,
    ):
        splash_layer_text.append(
            ground_effect_layer_usda(
                f"SplashLayer{index:02d}",
                quads,
                count,
                "splashCount",
                "RainSplashMaterial",
            )
        )
    mist_layers = mist_layer_quads()
    mist_layer_text = []
    for index, (count, quads) in enumerate(
        zip(MIST_LAYER_COUNTS, mist_layers),
        start=1,
    ):
        mist_layer_text.append(
            ground_effect_layer_usda(
                f"MistLayer{index:02d}",
                quads,
                count,
                "mistPuffCount",
                "RainMistMaterial",
            )
        )
    return f'''#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

over Xform "World"
{{
    def Xform "SMART_PORT_WEATHER"
    {{
        custom string smartPort:description = "Four strongly separated rain densities with wet-road sheen and torrential near-screen streaks"
        custom string smartPort:rainAmountScale = "Light <10 mm/24h; Moderate 10-25 mm/24h; Heavy 25-50 mm/24h; Torrential 50-100 mm/24h plus short burst >16 mm/h"
        custom string smartPort:rainAmountBehavior = "Light: sparse visible drops and slow wetting; Moderate: continuous streaks and visible puddling; Heavy: dense blurred streaks and fast wetting; Torrential: near-screen-filling rain, reduced visibility, and strong wet sheen"

        def Scope "Looks"
        {{
            def Material "RainStreakMaterial"
            {{
                token outputs:surface.connect = </World/SMART_PORT_WEATHER/Looks/RainStreakMaterial/PreviewSurface.outputs:surface>
                def Shader "PreviewSurface"
                {{
                    uniform token info:id = "UsdPreviewSurface"
                    color3f inputs:diffuseColor = (0.62, 0.76, 0.86)
                    color3f inputs:emissiveColor = (0.08, 0.12, 0.16)
                    float inputs:metallic = 0
                    float inputs:opacity = 0.32
                    float inputs:roughness = 0.18
                    token outputs:surface
                }}
            }}

            def Material "WetRoadMaterial"
            {{
                token outputs:surface.connect = </World/SMART_PORT_WEATHER/Looks/WetRoadMaterial/PreviewSurface.outputs:surface>
                def Shader "PreviewSurface"
                {{
                    uniform token info:id = "UsdPreviewSurface"
                    color3f inputs:diffuseColor = (0.07, 0.11, 0.14)
                    float inputs:metallic = 0.35
                    float inputs:opacity = 0.12
                    float inputs:roughness = 0.08
                    token outputs:surface
                }}
            }}

        }}

        def Xform "Rain"
        {{
            token visibility = "invisible"
            custom int smartPort:rainDropCount = {RAIN_DROP_COUNT}
            custom string smartPort:rainLayerDropCounts = "{rain_layer_description}"
            custom string smartPort:performance = "Camera-local air rain only; puddles, splashes, and impact mist are disabled"
            custom string smartPort:animationDriver = "smartport.rtg_bridge update loop"
            custom string smartPort:groundClampRule = "Each grade uses three phase-shifted emitter batches that reset before their longest streak crosses Z=0.15 m"

            def Xform "AirRain"
            {{
                custom string smartPort:followRule = "Air rain follows the active camera in XY"
{chr(10).join(rain_layers)}
            }}

            def Mesh "WetRoadSheen"
            {{
                uniform bool doubleSided = true
                rel material:binding = </World/SMART_PORT_WEATHER/Looks/WetRoadMaterial>
                uniform token orientation = "rightHanded"
                {wet_mesh}
                uniform token subdivisionScheme = "none"
            }}
        }}
    }}
}}
'''


def main() -> None:
    OUTPUT.write_text(build_usda(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
