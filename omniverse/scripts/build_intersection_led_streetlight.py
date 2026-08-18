"""Build the composite intersection light asset with the new LED heads."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "omniverse" / "assets" / "resource" / "streetLight" / "Intersection_Street_Light_LED.usda"
HEAD_LOCAL_SPECS = (
    ((-0.00040, -0.91993), 0.0, "240"),
    ((0.79688, 0.45962), 120.0, "120"),
    ((-0.79649, 0.46031), 240.0, "000"),
)
HEAD_LOCAL_Z = 11.35871
 # The imported Sketchfab scene carries an internal scale; 0.004 keeps the
 # replacement head close to the original 0.2 m lamp size in this stage.
LED_LOCAL_SCALE = 0.004
LED_LOCAL_CENTER = (0.45834, -1.20518, -0.02968)


def fmt(value: float) -> str:
    value = round(value, 5)
    if value == int(value):
        return str(int(value))
    return f"{value:.5f}".rstrip("0").rstrip(".")


def _centered_translation(x: float, y: float, angle_deg: float) -> tuple[float, float, float]:
    """Offset the LED reference so its local mesh center lands on the old lens."""

    import math

    angle = math.radians(angle_deg)
    cx = LED_LOCAL_CENTER[0] * LED_LOCAL_SCALE
    cy = LED_LOCAL_CENTER[1] * LED_LOCAL_SCALE
    rotated_x = cx * math.cos(angle) - cy * math.sin(angle)
    rotated_y = cx * math.sin(angle) + cy * math.cos(angle)
    return x - rotated_x, y - rotated_y, HEAD_LOCAL_Z - LED_LOCAL_CENTER[2] * LED_LOCAL_SCALE


def build_usda() -> str:
    head_blocks = []
    for (x, y), angle_deg, label in HEAD_LOCAL_SPECS:
        tx, ty, tz = _centered_translation(x, y, angle_deg)
        head_blocks.append(
            f'''            def Xform "LED_Floodlight_{label}" (
                prepend references = @../led/LED_Floodlight.usdz@
            )
            {{
                double3 xformOp:translate = ({fmt(tx)}, {fmt(ty)}, {fmt(tz)})
                float3 xformOp:rotateXYZ = (0, 0, {fmt(angle_deg)})
                float3 xformOp:scale = ({fmt(LED_LOCAL_SCALE)}, {fmt(LED_LOCAL_SCALE)}, {fmt(LED_LOCAL_SCALE)})
                uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]
            }}'''
        )
    return f'''#usda 1.0
(
    defaultPrim = "root"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "root" (
    prepend references = @./Intersection_Street_Light.usdc@
)
{{
    over "IntersectionStreetLight"
    {{
        over "Lamp_240"
        {{
            token visibility = "invisible"
        }}
        over "Lamp_000"
        {{
            token visibility = "invisible"
        }}
        over "Lamp_120"
        {{
            token visibility = "invisible"
        }}
{chr(10).join(head_blocks)}
    }}
}}
'''


def main() -> None:
    OUTPUT.write_text(build_usda(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
