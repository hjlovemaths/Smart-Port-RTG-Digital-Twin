"""Generate the lightweight traffic apron along the long 4C-side shoreline."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "omniverse" / "scenes" / "quay_yard_traffic_layout.usda"

PREFIX = "SMART_PORT_QUAY_YARD_TRAFFIC_"
LAND_EDGE_X = 108.75
APRON_EDGE_X = 150.0
YARD_INLAND_EDGE_X = -101.75
LAYOUT_START_Y = -10.25
LAYOUT_END_Y = 500.0
# Each quay lane lands in the clear approach space immediately before bay 001.
# The first uses the 1C forecourt; the others use the 7.4 m inter-block roads.
BLOCK_APPROACH_LANE_CENTERS_Y = (-5.375, 126.05, 256.30, 386.55)
TERMINAL_CROSS_ROAD_CENTER_Y = 496.30
ALL_CROSS_ROAD_CENTERS_Y = (
    *BLOCK_APPROACH_LANE_CENTERS_Y,
    TERMINAL_CROSS_ROAD_CENTER_Y,
)
COASTAL_GREEN_BELT_X_RANGE = (-101.75, -85.875)
COASTAL_YARD_ROAD_X_RANGE = (-85.875, -70.0)
# The right-edge green belt occupies the former narrow lane beside the outer road.
RIGHT_EDGE_GREEN_BELT_X_RANGE = (105.25, 110.5)
INTER_YARD_ROAD_X_RANGES = (
    (-50.0, -40.0),
    (-20.0, -10.0),
    (10.0, 20.0),
    (40.0, 50.0),
    (70.0, 80.0),
)
YARD_ROAD_X_RANGES = (
    COASTAL_YARD_ROAD_X_RANGE,
    *INTER_YARD_ROAD_X_RANGES,
)
TERMINAL_INTERSECTION_X_RANGES = (
    *YARD_ROAD_X_RANGES,
    (110.5, 125.5),
)
GREEN_BELT_CAUSEWAY_CLEARANCE_M = 5.5
ROAD_INTERSECTION_CLEARANCE_M = 3.82
CAUSEWAY_LANDING_HALF_WIDTH_M = 4.5
CAUSEWAY_EDGE_LINE_OFFSET_M = 4.18
CAUSEWAY_EDGE_LINE_WIDTH_M = 0.16
YARD_CROSS_ROAD_HALF_WIDTH_M = 3.7


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


def box_quads(
    center_x: float,
    center_y: float,
    x_size: float,
    y_size: float,
    z_min: float,
    z_max: float,
) -> list[tuple]:
    x_min = center_x - x_size * 0.5
    x_max = center_x + x_size * 0.5
    y_min = center_y - y_size * 0.5
    y_max = center_y + y_size * 0.5
    return [
        ((x_min, y_min, z_min), (x_max, y_min, z_min), (x_max, y_max, z_min), (x_min, y_max, z_min)),
        ((x_min, y_min, z_max), (x_min, y_max, z_max), (x_max, y_max, z_max), (x_max, y_min, z_max)),
        ((x_min, y_min, z_min), (x_min, y_min, z_max), (x_max, y_min, z_max), (x_max, y_min, z_min)),
        ((x_max, y_min, z_min), (x_max, y_min, z_max), (x_max, y_max, z_max), (x_max, y_max, z_min)),
        ((x_max, y_max, z_min), (x_max, y_max, z_max), (x_min, y_max, z_max), (x_min, y_max, z_min)),
        ((x_min, y_max, z_min), (x_min, y_max, z_max), (x_min, y_min, z_max), (x_min, y_min, z_min)),
    ]


def fmt(value: float) -> str:
    value = round(value, 4)
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def segments_without(
    start: float, end: float, exclusions: tuple[tuple[float, float], ...]
) -> list[tuple[float, float]]:
    segments = []
    cursor = start
    for excluded_start, excluded_end in sorted(exclusions):
        clipped_start = max(start, excluded_start)
        clipped_end = min(end, excluded_end)
        if clipped_end <= cursor:
            continue
        if clipped_start > cursor:
            segments.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < end:
        segments.append((cursor, end))
    return segments


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
    for group_start_y in (65.0, 160.0):
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


def lane_marking_quads() -> tuple[list[tuple], list[tuple]]:
    white_quads = []
    yellow_centerline_quads = []
    cross_road_clear_ranges = tuple(
        (
            center_y - ROAD_INTERSECTION_CLEARANCE_M,
            center_y + ROAD_INTERSECTION_CLEARANCE_M,
        )
        for center_y in ALL_CROSS_ROAD_CENTERS_Y
    )

    for y_min, y_max in segments_without(
        LAYOUT_START_Y, LAYOUT_END_Y - 5.0, cross_road_clear_ranges
    ):
        white_quads.extend(
            (
                rect(110.55, y_min, 110.77, y_max, 0.145),
                rect(125.23, y_min, 125.45, y_max, 0.145),
            )
        )
    dash_y = LAYOUT_START_Y + 8.0
    while dash_y < LAYOUT_END_Y - 8.0:
        dash_end = min(dash_y + 4.0, LAYOUT_END_Y - 8.0)
        for y_min, y_max in segments_without(
            dash_y, dash_end, cross_road_clear_ranges
        ):
            yellow_centerline_quads.append(
                rect(117.89, y_min, 118.11, y_max, 0.145)
            )
        dash_y += 8.0

    for center_y in BLOCK_APPROACH_LANE_CENTERS_Y:
        for x_min, x_max in segments_without(
            YARD_INLAND_EDGE_X, 110.5, YARD_ROAD_X_RANGES
        ):
            if x_max <= COASTAL_YARD_ROAD_X_RANGE[0]:
                edge_offset = CAUSEWAY_EDGE_LINE_OFFSET_M
                edge_half_width = CAUSEWAY_EDGE_LINE_WIDTH_M * 0.5
            else:
                edge_offset = YARD_CROSS_ROAD_HALF_WIDTH_M - 0.21
                edge_half_width = 0.11
            white_quads.extend(
                (
                    rect(
                        x_min,
                        center_y - edge_offset - edge_half_width,
                        x_max,
                        center_y - edge_offset + edge_half_width,
                        0.145,
                    ),
                    rect(
                        x_min,
                        center_y + edge_offset - edge_half_width,
                        x_max,
                        center_y + edge_offset + edge_half_width,
                        0.145,
                    ),
                )
            )
        dash_x = YARD_INLAND_EDGE_X + 4.0
        while dash_x < 107.0:
            dash_end = min(dash_x + 4.0, 107.0)
            for x_min, x_max in segments_without(
                dash_x, dash_end, YARD_ROAD_X_RANGES
            ):
                yellow_centerline_quads.append(
                    rect(
                        x_min,
                        center_y - 0.11,
                        x_max,
                        center_y + 0.11,
                        0.146,
                    )
                )
            dash_x += 8.0

    terminal_y = TERMINAL_CROSS_ROAD_CENTER_Y
    for x_min, x_max in segments_without(
        YARD_INLAND_EDGE_X, APRON_EDGE_X, TERMINAL_INTERSECTION_X_RANGES
    ):
        white_quads.extend(
            (
                rect(
                    x_min,
                    terminal_y - YARD_CROSS_ROAD_HALF_WIDTH_M + 0.10,
                    x_max,
                    terminal_y - YARD_CROSS_ROAD_HALF_WIDTH_M + 0.32,
                    0.145,
                ),
                rect(
                    x_min,
                    terminal_y + YARD_CROSS_ROAD_HALF_WIDTH_M - 0.32,
                    x_max,
                    terminal_y + YARD_CROSS_ROAD_HALF_WIDTH_M - 0.10,
                    0.145,
                ),
            )
        )
    dash_x = YARD_INLAND_EDGE_X + 4.0
    while dash_x < APRON_EDGE_X - 3.0:
        dash_end = min(dash_x + 4.0, APRON_EDGE_X - 3.0)
        for x_min, x_max in segments_without(
            dash_x, dash_end, TERMINAL_INTERSECTION_X_RANGES
        ):
            yellow_centerline_quads.append(
                rect(
                    x_min,
                    terminal_y - 0.11,
                    x_max,
                    terminal_y + 0.11,
                    0.146,
                )
            )
        dash_x += 8.0

    for x_min, x_max in YARD_ROAD_X_RANGES:
        for y_min, y_max in segments_without(
            LAYOUT_START_Y, LAYOUT_END_Y, cross_road_clear_ranges
        ):
            white_quads.extend(
                (
                    rect(x_min + 0.18, y_min, x_min + 0.40, y_max, 0.147),
                    rect(x_max - 0.40, y_min, x_max - 0.18, y_max, 0.147),
                )
            )
        center_x = (x_min + x_max) * 0.5
        dash_y = LAYOUT_START_Y + 4.0
        while dash_y < LAYOUT_END_Y - 4.0:
            dash_end = min(dash_y + 4.0, LAYOUT_END_Y - 4.0)
            for y_min, y_max in segments_without(
                dash_y, dash_end, cross_road_clear_ranges
            ):
                yellow_centerline_quads.append(
                    rect(center_x - 0.11, y_min, center_x + 0.11, y_max, 0.148)
                )
            dash_y += 8.0
    return white_quads, yellow_centerline_quads


def safety_hatch_quads() -> list[tuple]:
    quads = []
    for y_min, y_max in ((290.0, 346.0),):
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


def segmented_green_belt_quads(x_range: tuple[float, float]) -> list[tuple]:
    x_min, x_max = x_range
    quads = []
    segment_start = LAYOUT_START_Y
    for center_y in ALL_CROSS_ROAD_CENTERS_Y:
        if center_y == TERMINAL_CROSS_ROAD_CENTER_Y:
            clear_start = center_y - YARD_CROSS_ROAD_HALF_WIDTH_M
            clear_end = LAYOUT_END_Y
        else:
            clear_start = max(
                LAYOUT_START_Y, center_y - GREEN_BELT_CAUSEWAY_CLEARANCE_M
            )
            clear_end = min(
                LAYOUT_END_Y, center_y + GREEN_BELT_CAUSEWAY_CLEARANCE_M
            )
        if clear_start > segment_start:
            quads.append(rect(x_min, segment_start, x_max, clear_start, 0.111))
        segment_start = max(segment_start, clear_end)
    if segment_start < LAYOUT_END_Y:
        quads.append(rect(x_min, segment_start, x_max, LAYOUT_END_Y, 0.111))
    return quads


def tree_quads(x_positions: tuple[float, ...]) -> tuple[list[tuple], list[tuple]]:
    trunk_quads = []
    crown_quads = []
    y = LAYOUT_START_Y + 7.0
    while y < LAYOUT_END_Y - 5.0:
        if all(
            abs(y - center_y) > GREEN_BELT_CAUSEWAY_CLEARANCE_M + 1.0
            for center_y in ALL_CROSS_ROAD_CENTERS_Y
        ):
            for x in x_positions:
                trunk_quads.extend(box_quads(x, y, 0.36, 0.36, 0.112, 2.25))
                crown_quads.extend(box_quads(x, y, 1.9, 1.9, 1.75, 4.15))
        y += 13.0
    return trunk_quads, crown_quads


def build_layer() -> str:
    apron_quads = [rect(LAND_EDGE_X, LAYOUT_START_Y, APRON_EDGE_X, LAYOUT_END_Y, 0.085)]
    road_quads = [rect(110.5, LAYOUT_START_Y, 125.5, LAYOUT_END_Y - 5.0, 0.108)]
    road_quads.extend(
        rect(x_min, LAYOUT_START_Y, x_max, LAYOUT_END_Y, 0.109)
        for x_min, x_max in YARD_ROAD_X_RANGES
    )
    for center_y in BLOCK_APPROACH_LANE_CENTERS_Y:
        road_quads.append(
            rect(
                YARD_INLAND_EDGE_X,
                center_y - YARD_CROSS_ROAD_HALF_WIDTH_M,
                110.5,
                center_y + YARD_CROSS_ROAD_HALF_WIDTH_M,
                0.108,
            )
        )
        road_quads.append(
            rect(
                YARD_INLAND_EDGE_X,
                center_y - CAUSEWAY_LANDING_HALF_WIDTH_M,
                COASTAL_YARD_ROAD_X_RANGE[0],
                center_y + CAUSEWAY_LANDING_HALF_WIDTH_M,
                0.110,
            )
        )
    road_quads.append(
        rect(
            YARD_INLAND_EDGE_X,
            TERMINAL_CROSS_ROAD_CENTER_Y - YARD_CROSS_ROAD_HALF_WIDTH_M,
            APRON_EDGE_X,
            LAYOUT_END_Y,
            0.110,
        )
    )

    coastal_tree_trunks, coastal_tree_crowns = tree_quads((-97.5, -90.125))
    right_edge_tree_trunks, right_edge_tree_crowns = tree_quads((107.875,))
    white_lane_quads, yellow_centerline_quads = lane_marking_quads()

    meshes = (
        mesh_usda(f"{PREFIX}SHORE_APRON", (0.035, 0.08, 0.15), apron_quads),
        mesh_usda(f"{PREFIX}ROAD_SURFACE", (0.018, 0.035, 0.06), road_quads),
        mesh_usda(f"{PREFIX}WHITE_LANE_MARKINGS", (0.88, 0.90, 0.87), white_lane_quads),
        mesh_usda(f"{PREFIX}YELLOW_LANE_CENTERLINES", (0.96, 0.76, 0.04), yellow_centerline_quads),
        mesh_usda(f"{PREFIX}YELLOW_WAITING_BAYS", (0.96, 0.76, 0.04), waiting_bay_quads()),
        mesh_usda(f"{PREFIX}SAFETY_HATCH_MARKINGS", (0.96, 0.76, 0.04), safety_hatch_quads()),
        mesh_usda(f"{PREFIX}COASTAL_GREEN_BELT", (0.08, 0.24, 0.10), segmented_green_belt_quads(COASTAL_GREEN_BELT_X_RANGE)),
        mesh_usda(f"{PREFIX}COASTAL_TREE_TRUNKS", (0.22, 0.12, 0.055), coastal_tree_trunks),
        mesh_usda(f"{PREFIX}COASTAL_TREE_CROWNS", (0.06, 0.34, 0.11), coastal_tree_crowns),
        mesh_usda(f"{PREFIX}RIGHT_EDGE_GREEN_BELT", (0.09, 0.27, 0.11), segmented_green_belt_quads(RIGHT_EDGE_GREEN_BELT_X_RANGE)),
        mesh_usda(f"{PREFIX}RIGHT_EDGE_TREE_TRUNKS", (0.22, 0.12, 0.055), right_edge_tree_trunks),
        mesh_usda(f"{PREFIX}RIGHT_EDGE_TREE_CROWNS", (0.07, 0.38, 0.12), right_edge_tree_crowns),
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
        custom string trafficCoordinateRange = "X=-101.75..150.00; Y=-10.25..500.00; longShoreline=positiveX"
        custom string trafficLayoutRule = "Four straight quay-to-yard lanes use the clear approach space before bay 001 of 1C, 2C, 3C, and 4C; connected by the long-shore road and a full-width terminal cross-road; the former coastal yard is split evenly into a waterside green tree belt and a yard-side road; the far-right former narrow lane is a green tree belt beside the outer continuous road; five inter-yard gaps are traffic lanes; two groups of ten waiting bays"
        custom string trafficPrimitiveRule = "Twelve merged meshes; no labels, collision, shadows, or runtime vehicles"
{mesh_text}
    }}
}}
'''


def main() -> None:
    OUTPUT.write_text(build_layer(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
