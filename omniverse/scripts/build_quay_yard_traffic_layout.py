"""Generate the lightweight traffic apron along the long 4C-side shoreline."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "omniverse" / "scenes" / "quay_yard_traffic_layout.usda"

PREFIX = "SMART_PORT_QUAY_YARD_TRAFFIC_"
LAND_EDGE_X = 108.75
APRON_EDGE_X = 150.0
LAYOUT_START_Y = 120.0
LAYOUT_END_Y = 420.0


def rect(x_min: float, y_min: float, x_max: float, y_max: float, z: float):
    return (
        (x_min, y_min, z),
        (x_max, y_min, z),
        (x_max, y_max, z),
        (x_min, y_max, z),
    )


def stripe(x_min: float, x_max: float, center_y: float, rise: float, width: float, z: float):
    half = width * 0.5
    return (
        (x_min, center_y - rise - half, z),
        (x_max, center_y + rise - half, z),
        (x_max, center_y + rise + half, z),
        (x_min, center_y - rise + half, z),
    )


def fmt(value: float) -> str:
    value = round(value, 4)
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def mesh_usda(name: str, color: tuple[float, float, float], quads: list[tuple]) -> str:
    points = [point for quad in quads for point in quad]
    face_counts = ", ".join("4" for _ in quads)
    indices = ", ".join(str(index) for index in range(len(points)))
    point_lines = ",\n".join(
        f"                ({fmt(x)}, {fmt(y)}, {fmt(z)})" for x, y, z in points
    )
    return f'''        def Mesh "{name}"
        {{
            bool doubleSided = 1
            uniform token subdivisionScheme = "none"
            int[] faceVertexCounts = [{face_counts}]
            int[] faceVertexIndices = [{indices}]
            color3f[] primvars:displayColor = [({fmt(color[0])}, {fmt(color[1])}, {fmt(color[2])})]
            uniform token primvars:displayColor:interpolation = "constant"
            point3f[] points = [
{point_lines}
            ]
        }}'''


def waiting_bay_quads() -> list[tuple]:
    quads = []
    for group_start_y in (148.0, 290.0):
        for index in range(10):
            y_min = group_start_y + index * 5.4
            y_max = y_min + 4.4
            x_min = 129.0
            x_max = 147.0
            quads.extend(
                (
                    rect(x_min, y_min, x_max, y_min + 0.22, 0.155),
                    rect(x_min, y_max - 0.22, x_max, y_max, 0.155),
                    rect(x_min, y_min, x_min + 0.22, y_max, 0.155),
                    rect(x_max - 0.22, y_min, x_max, y_max, 0.155),
                )
            )
    return quads


def lane_marking_quads() -> list[tuple]:
    quads = [
        rect(110.55, 125.0, 110.77, 415.0, 0.145),
        rect(125.23, 125.0, 125.45, 415.0, 0.145),
    ]
    dash_y = 128.0
    while dash_y < 412.0:
        quads.append(rect(117.89, dash_y, 118.11, min(dash_y + 4.0, 412.0), 0.145))
        dash_y += 8.0

    for center_y in (124.85, 252.70, 380.55):
        quads.extend(
            (
                rect(101.0, center_y - 3.6, 110.75, center_y - 3.38, 0.145),
                rect(101.0, center_y + 3.38, 110.75, center_y + 3.6, 0.145),
                rect(110.55, center_y - 3.15, 110.77, center_y + 3.15, 0.147),
            )
        )
    return quads


def safety_hatch_quads() -> list[tuple]:
    quads = []
    for y_min, y_max in ((216.0, 276.0), (352.0, 410.0)):
        quads.extend(
            (
                rect(128.0, y_min, 128.22, y_max, 0.158),
                rect(147.78, y_min, 148.0, y_max, 0.158),
                rect(128.0, y_min, 148.0, y_min + 0.22, 0.158),
                rect(128.0, y_max - 0.22, 148.0, y_max, 0.158),
            )
        )
        center_y = y_min + 5.0
        while center_y <= y_max - 5.0:
            quads.append(stripe(128.3, 147.7, center_y, 3.2, 0.2, 0.158))
            center_y += 7.0
    return quads


def build_layer() -> str:
    apron_quads = [rect(LAND_EDGE_X, LAYOUT_START_Y, APRON_EDGE_X, LAYOUT_END_Y, 0.085)]
    road_quads = [rect(110.5, 125.0, 125.5, 415.0, 0.108)]
    for center_y in (124.85, 252.70, 380.55):
        road_quads.append(rect(101.0, center_y - 3.7, 110.5, center_y + 3.7, 0.108))

    meshes = (
        mesh_usda(f"{PREFIX}SHORE_APRON", (0.72, 0.74, 0.72), apron_quads),
        mesh_usda(f"{PREFIX}ROAD_SURFACE", (0.24, 0.26, 0.25), road_quads),
        mesh_usda(f"{PREFIX}WHITE_LANE_MARKINGS", (0.88, 0.90, 0.87), lane_marking_quads()),
        mesh_usda(f"{PREFIX}YELLOW_WAITING_BAYS", (0.96, 0.76, 0.04), waiting_bay_quads()),
        mesh_usda(f"{PREFIX}SAFETY_HATCH_MARKINGS", (0.96, 0.76, 0.04), safety_hatch_quads()),
    )
    mesh_text = "\n\n".join(meshes)
    return f'''#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "World"
{{
    def Xform "{PREFIX}LAYOUT"
    {{
        custom string trafficCoordinateRange = "X=108.75..150.00; Y=120.00..420.00; longShoreline=positiveX"
        custom string trafficLayoutRule = "Long side shoreline apron; three yard connectors; two groups of ten waiting bays"
        custom string trafficPrimitiveRule = "Five merged meshes; no labels, collision, shadows, or runtime vehicles"
{mesh_text}
    }}
}}
'''


def main() -> None:
    OUTPUT.write_text(build_layer(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
