"""Relocate the AI console and rebuild the yard from the 1C-4C template."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, Vt


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from yard_coordinate_mapping import (  # noqa: E402
    BAY_GAP_M,
    BAY_LENGTH_M,
    YARD_BLOCKS,
    YARD_LAYOUT_START_Y_M,
)


SOURCE = ROOT / "omniverse" / "assets" / "rtg" / "RTG_Model.usdc"
OUTPUT = ROOT / "omniverse" / "scenes" / "yard_expansion_prep.usda"

PORT_PATH = "/World/PortAndRTG"
SOURCE_ROOT_PATH = "/World"
AI_SOURCE_PIVOT = Gf.Vec2d(-18.0, 17.0)
AI_TARGET_CENTER = Gf.Vec2d(138.0, 470.0)
AI_ROTATION_DEGREES = 90.0
YARD_X_MIN = -8.0
YARD_X_MAX = 8.0
YARD_COLUMN_CENTERS_X = (-60.0, -30.0, 0.0, 30.0, 60.0, 90.0)
YARD_COLUMN_WIDTH = YARD_X_MAX - YARD_X_MIN
RTG_TIRE_TRACK_OFFSET_X = 6.50
RTG_TIRE_TRACK_WIDTH = 0.10
YARD_BLOCK_SPECS = tuple(
    (
        block.name,
        block.bay_count,
        YARD_LAYOUT_START_Y_M + block.start_m,
        YARD_LAYOUT_START_Y_M + block.end_m,
    )
    for block in YARD_BLOCKS
)
BAY_PITCH_Y = BAY_LENGTH_M + BAY_GAP_M
MARKING_WIDTH = 0.055
MARKING_Z = 0.145
MARKING_COLOR = Gf.Vec3f(0.88, 0.9, 0.87)
YELLOW_MARKING_COLOR = Gf.Vec3f(0.96, 0.76, 0.04)
LABEL_SIDE_OFFSETS_X = (0.0,)
LABEL_PANEL_SIZE = (1.15, 0.74)
LABEL_PANEL_Z = 0.149
LABEL_PANEL_COLOR = Gf.Vec3f(0.82, 0.84, 0.8)
LABEL_DIGIT_SIZE = (0.24, 0.43)
LABEL_DIGIT_STROKE = 0.055
LABEL_DIGIT_GAP = 0.08
LABEL_DIGIT_Z = 0.151
LABEL_DIGIT_COLOR = Gf.Vec3f(0.018, 0.025, 0.03)
# Keep block identifiers outside the narrowed painted corridor.  One centered
# label per block avoids covering the central bay-001 number mark.
BLOCK_LABEL_OFFSET_X = YARD_X_MAX + 1.4
REBUILT_ROOT_PATH = "/World/SMART_PORT_REBUILT_YARD_LAYOUT"
REBUILT_MARKINGS_NAME = "SMART_PORT_CLEAN_BAY_MARKINGS"
REBUILT_LABEL_BACKINGS_NAME = "SMART_PORT_CLEAN_BAY_NUMBER_BACKINGS"
REBUILT_LABEL_DIGITS_NAME = "SMART_PORT_CLEAN_BAY_NUMBER_DIGITS"
REBUILT_BLOCK_LABEL_BACKINGS_NAME = "SMART_PORT_TERMINAL_BLOCK_LABEL_BACKINGS"
REBUILT_BLOCK_LABEL_GLYPHS_NAME = "SMART_PORT_TERMINAL_BLOCK_LABEL_GLYPHS"
REBUILT_RTG_TRACKS_NAME = "SMART_PORT_RTG_TIRE_TRACK_YELLOW_SOLID"

DIGIT_SEGMENTS = {
    "0": ("top", "upper_left", "upper_right", "lower_left", "lower_right", "bottom"),
    "1": ("upper_right", "lower_right"),
    "2": ("top", "upper_right", "middle", "lower_left", "bottom"),
    "3": ("top", "upper_right", "middle", "lower_right", "bottom"),
    "4": ("upper_left", "upper_right", "middle", "lower_right"),
    "5": ("top", "upper_left", "middle", "lower_right", "bottom"),
    "6": ("top", "upper_left", "middle", "lower_left", "lower_right", "bottom"),
    "7": ("top", "upper_right", "lower_right"),
    "8": (
        "top",
        "upper_left",
        "upper_right",
        "middle",
        "lower_left",
        "lower_right",
        "bottom",
    ),
    "9": ("top", "upper_left", "upper_right", "middle", "lower_right", "bottom"),
    "C": ("top", "upper_left", "lower_left", "bottom"),
}

AI_COMPONENT_PREFIXES = (
    "SMART_PORT_control_center_",
    "SMART_PORT_roof_solar_panel_",
    "SMART_PORT_smart_control_center_",
    "SMART_PORT_smart_tower_",
    "SMART_PORT_tower_",
)

OLD_YARD_ROOTS = (
    "YARD_LAYOUT_1C4C_ROOT",
    "YARD_LAYOUT_1C4C_INFILL_LEFT_CENTRAL_BOUNDARIES",
    "YARD_LAYOUT_1C4C_INFILL_RIGHT_CENTRAL_BOUNDARIES",
    "SMART_PORT_UNIFIED_SIDEYARD_cyan_lane_lines",
    "SMART_PORT_UNIFIED_SIDEYARD_light_grey_40ft_slots",
    "SMART_PORT_UNIFIED_SIDEYARD_yellow_boundaries",
    "SMART_PORT_UNIFIED_SIDEYARD_white_slot_separators",
)

GROUND_OBJECT_PREFIXES = (
    "SMART_PORT_agv_body_",
    "SMART_PORT_agv_cyan_sensor_panel_",
    "SMART_PORT_floating_data_",
    "SMART_PORT_smart_cyan_camera_",
    "SMART_PORT_smart_light_head_",
    "SMART_PORT_smart_light_pole_",
    "SMART_PORT_SIDEYARD_left_zone_label_",
    "SMART_PORT_SIDEYARD_right_zone_label_",
    "Y_WHEEL_ground_tire_track_",
)
GROUND_MARKING_PREFIXES = (
    "LIVE_internal_truck_lane_",
    "LIVE_RTG_leg_line_reference_",
    "SMART_PORT_white_lane_dash_",
)


def _append_rectangle(
    points: list[Gf.Vec3f],
    counts: list[int],
    indices: list[int],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z: float = MARKING_Z,
) -> None:
    start = len(points)
    points.extend(
        (
            Gf.Vec3f(x_min, y_min, z),
            Gf.Vec3f(x_max, y_min, z),
            Gf.Vec3f(x_max, y_max, z),
            Gf.Vec3f(x_min, y_max, z),
        )
    )
    counts.append(4)
    indices.extend((start, start + 1, start + 2, start + 3))


def _append_centered_rectangle(
    points: list[Gf.Vec3f],
    counts: list[int],
    indices: list[int],
    center_x: float,
    center_y: float,
    width: float,
    height: float,
    z: float,
) -> None:
    _append_rectangle(
        points,
        counts,
        indices,
        center_x - width * 0.5,
        center_x + width * 0.5,
        center_y - height * 0.5,
        center_y + height * 0.5,
        z,
    )


def _define_flat_mesh(
    output_stage: Usd.Stage,
    name: str,
    points: list[Gf.Vec3f],
    counts: list[int],
    indices: list[int],
    color: Gf.Vec3f,
) -> int:
    output_path = f"{REBUILT_ROOT_PATH}/{name}"
    mesh = UsdGeom.Mesh.Define(output_stage, output_path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDisplayColorAttr([color])
    mesh.GetDisplayColorPrimvar().SetInterpolation(UsdGeom.Tokens.constant)
    minimum = Gf.Vec3f(
        *(min(float(point[index]) for point in points) for index in range(3))
    )
    maximum = Gf.Vec3f(
        *(max(float(point[index]) for point in points) for index in range(3))
    )
    mesh.CreateExtentAttr([minimum, maximum])
    return len(counts)


def _odd_bay_slots(bay_count: int, y_min: float, y_max: float):
    for bay_number in range(1, bay_count + 1, 2):
        slot_index = (bay_number - 1) // 2
        slot_start = y_min + slot_index * BAY_PITCH_Y
        slot_end = min(slot_start + BAY_LENGTH_M, y_max)
        yield bay_number, slot_start, slot_end


def _author_clean_bay_markings(output_stage: Usd.Stage) -> int:
    points: list[Gf.Vec3f] = []
    counts: list[int] = []
    indices: list[int] = []
    for center_x in YARD_COLUMN_CENTERS_X:
        x_min = center_x + YARD_X_MIN
        x_max = center_x + YARD_X_MAX
        for _block_name, bay_count, y_min, y_max in YARD_BLOCK_SPECS:
            # Keep only the bay separators.  The outer white perimeter was a
            # visual frame and is intentionally omitted from the final yard.
            for _bay_number, _slot_start, slot_end in _odd_bay_slots(
                bay_count, y_min, y_max
            ):
                if slot_end >= y_max - 1e-6:
                    continue
                position = slot_end + BAY_GAP_M * 0.5
                _append_rectangle(
                    points,
                    counts,
                    indices,
                    x_min,
                    x_max,
                    position - MARKING_WIDTH * 0.5,
                    position + MARKING_WIDTH * 0.5,
                )

    return _define_flat_mesh(
        output_stage, REBUILT_MARKINGS_NAME, points, counts, indices, MARKING_COLOR
    )


def _author_rtg_tire_tracks(output_stage: Usd.Stage) -> int:
    points: list[Gf.Vec3f] = []
    counts: list[int] = []
    indices: list[int] = []
    for center_x in YARD_COLUMN_CENTERS_X:
        for _block_name, _bay_count, y_min, y_max in YARD_BLOCK_SPECS:
            for offset_x in (-RTG_TIRE_TRACK_OFFSET_X, RTG_TIRE_TRACK_OFFSET_X):
                _append_rectangle(
                    points,
                    counts,
                    indices,
                    center_x + offset_x - RTG_TIRE_TRACK_WIDTH * 0.5,
                    center_x + offset_x + RTG_TIRE_TRACK_WIDTH * 0.5,
                    y_min,
                    y_max,
                )
    return _define_flat_mesh(
        output_stage,
        REBUILT_RTG_TRACKS_NAME,
        points,
        counts,
        indices,
        YELLOW_MARKING_COLOR,
    )


def _append_digit(
    points: list[Gf.Vec3f],
    counts: list[int],
    indices: list[int],
    digit: str,
    center_x: float,
    center_y: float,
) -> None:
    digit_width, digit_height = LABEL_DIGIT_SIZE
    segment_boxes = {
        "top": (
            center_x,
            center_y + digit_height * 0.5,
            digit_width,
            LABEL_DIGIT_STROKE,
        ),
        "middle": (center_x, center_y, digit_width, LABEL_DIGIT_STROKE),
        "bottom": (
            center_x,
            center_y - digit_height * 0.5,
            digit_width,
            LABEL_DIGIT_STROKE,
        ),
        "upper_left": (
            center_x - digit_width * 0.5,
            center_y + digit_height * 0.25,
            LABEL_DIGIT_STROKE,
            digit_height * 0.5,
        ),
        "upper_right": (
            center_x + digit_width * 0.5,
            center_y + digit_height * 0.25,
            LABEL_DIGIT_STROKE,
            digit_height * 0.5,
        ),
        "lower_left": (
            center_x - digit_width * 0.5,
            center_y - digit_height * 0.25,
            LABEL_DIGIT_STROKE,
            digit_height * 0.5,
        ),
        "lower_right": (
            center_x + digit_width * 0.5,
            center_y - digit_height * 0.25,
            LABEL_DIGIT_STROKE,
            digit_height * 0.5,
        ),
    }
    for segment in DIGIT_SEGMENTS[digit]:
        _append_centered_rectangle(
            points, counts, indices, *segment_boxes[segment], LABEL_DIGIT_Z
        )


def _author_bay_number_labels(output_stage: Usd.Stage) -> dict[str, int]:
    backing_points: list[Gf.Vec3f] = []
    backing_counts: list[int] = []
    backing_indices: list[int] = []
    digit_points: list[Gf.Vec3f] = []
    digit_counts: list[int] = []
    digit_indices: list[int] = []
    digit_width, _digit_height = LABEL_DIGIT_SIZE
    text_width = digit_width * 2 + LABEL_DIGIT_GAP

    for center_x in YARD_COLUMN_CENTERS_X:
        for _block_name, bay_count, y_min, y_max in YARD_BLOCK_SPECS:
            for bay_number, slot_start, slot_end in _odd_bay_slots(
                bay_count, y_min, y_max
            ):
                bay_center_y = (slot_start + slot_end) * 0.5
                for offset_x in LABEL_SIDE_OFFSETS_X:
                    label_x = center_x + offset_x
                    _append_centered_rectangle(
                        backing_points,
                        backing_counts,
                        backing_indices,
                        label_x,
                        bay_center_y,
                        LABEL_PANEL_SIZE[0],
                        LABEL_PANEL_SIZE[1],
                        LABEL_PANEL_Z,
                    )
                    for digit_index, digit in enumerate(f"{bay_number:02d}"):
                        digit_x = (
                            label_x
                            - text_width * 0.5
                            + digit_width * 0.5
                            + digit_index * (digit_width + LABEL_DIGIT_GAP)
                        )
                        _append_digit(
                            digit_points,
                            digit_counts,
                            digit_indices,
                            digit,
                            digit_x,
                            bay_center_y,
                        )

    return {
        REBUILT_LABEL_BACKINGS_NAME: _define_flat_mesh(
            output_stage,
            REBUILT_LABEL_BACKINGS_NAME,
            backing_points,
            backing_counts,
            backing_indices,
            LABEL_PANEL_COLOR,
        ),
        REBUILT_LABEL_DIGITS_NAME: _define_flat_mesh(
            output_stage,
            REBUILT_LABEL_DIGITS_NAME,
            digit_points,
            digit_counts,
            digit_indices,
            LABEL_DIGIT_COLOR,
        ),
    }


def _author_terminal_block_labels(output_stage: Usd.Stage) -> dict[str, int]:
    backing_points: list[Gf.Vec3f] = []
    backing_counts: list[int] = []
    backing_indices: list[int] = []
    glyph_points: list[Gf.Vec3f] = []
    glyph_counts: list[int] = []
    glyph_indices: list[int] = []
    glyph_width, _glyph_height = LABEL_DIGIT_SIZE
    text_width = glyph_width * 2 + LABEL_DIGIT_GAP

    for center_x in YARD_COLUMN_CENTERS_X:
        label_x = center_x + BLOCK_LABEL_OFFSET_X
        for block_name, _bay_count, y_min, y_max in YARD_BLOCK_SPECS:
            label_y = (y_min + y_max) * 0.5
            _append_centered_rectangle(
                backing_points,
                backing_counts,
                backing_indices,
                label_x,
                label_y,
                LABEL_PANEL_SIZE[0],
                LABEL_PANEL_SIZE[1],
                LABEL_PANEL_Z,
            )
            for glyph_index, glyph in enumerate(block_name):
                glyph_x = (
                    label_x
                    - text_width * 0.5
                    + glyph_width * 0.5
                    + glyph_index * (glyph_width + LABEL_DIGIT_GAP)
                )
                _append_digit(
                    glyph_points,
                    glyph_counts,
                    glyph_indices,
                    glyph,
                    glyph_x,
                    label_y,
                )

    return {
        REBUILT_BLOCK_LABEL_BACKINGS_NAME: _define_flat_mesh(
            output_stage,
            REBUILT_BLOCK_LABEL_BACKINGS_NAME,
            backing_points,
            backing_counts,
            backing_indices,
            LABEL_PANEL_COLOR,
        ),
        REBUILT_BLOCK_LABEL_GLYPHS_NAME: _define_flat_mesh(
            output_stage,
            REBUILT_BLOCK_LABEL_GLYPHS_NAME,
            glyph_points,
            glyph_counts,
            glyph_indices,
            LABEL_DIGIT_COLOR,
        ),
    }


def _is_ground_object(name: str) -> bool:
    if name.startswith(GROUND_OBJECT_PREFIXES + GROUND_MARKING_PREFIXES):
        return True
    return name.startswith("SMART_PORT_SIDEYARD_") and "_agv_" in name


def _remove_ground_objects(source_stage: Usd.Stage, output_stage: Usd.Stage) -> int:
    removed = 0
    for child in source_stage.GetPrimAtPath(SOURCE_ROOT_PATH).GetChildren():
        if not _is_ground_object(child.GetName()):
            continue
        output_stage.OverridePrim(f"{PORT_PATH}/{child.GetName()}").SetActive(False)
        removed += 1
    return removed


def _remove_original_bay_number_labels(
    source_stage: Usd.Stage, output_stage: Usd.Stage
) -> int:
    layout_root = source_stage.GetPrimAtPath(
        f"{SOURCE_ROOT_PATH}/YARD_LAYOUT_1C4C_ROOT"
    )
    if not layout_root:
        return 0
    removed = 0
    for child in layout_root.GetChildren():
        if "_ODD_BAY_NUMBER_" not in child.GetName():
            continue
        relative_path = str(child.GetPath())[len(SOURCE_ROOT_PATH) :]
        output_stage.OverridePrim(f"{PORT_PATH}{relative_path}").SetActive(False)
        removed += 1
    return removed


def _relocate_ai_console(source_stage: Usd.Stage, output_stage: Usd.Stage) -> int:
    source_root = source_stage.GetPrimAtPath(SOURCE_ROOT_PATH)
    moved = 0
    for child in source_root.GetChildren():
        if not child.GetName().startswith(AI_COMPONENT_PREFIXES):
            continue
        source_translate = child.GetAttribute("xformOp:translate")
        source_rotate = child.GetAttribute("xformOp:rotateXYZ")
        value = source_translate.Get() if source_translate else None
        rotate_value = source_rotate.Get() if source_rotate else None
        if value is None or rotate_value is None:
            raise RuntimeError(f"AI component has no translate op: {child.GetPath()}")
        offset_x = float(value[0]) - AI_SOURCE_PIVOT[0]
        offset_y = float(value[1]) - AI_SOURCE_PIVOT[1]
        moved_value = Gf.Vec3d(
            round(float(AI_TARGET_CENTER[0] - offset_y), 6),
            round(float(AI_TARGET_CENTER[1] + offset_x), 6),
            round(float(value[2]), 6),
        )
        moved_rotate = Gf.Vec3f(
            round(float(rotate_value[0]), 6),
            round(float(rotate_value[1]), 6),
            round(float(rotate_value[2] + AI_ROTATION_DEGREES), 6),
        )
        target_path = f"{PORT_PATH}/{child.GetName()}"
        target = output_stage.OverridePrim(target_path)
        target.CreateAttribute("xformOp:translate", source_translate.GetTypeName()).Set(
            moved_value
        )
        target.CreateAttribute("xformOp:rotateXYZ", source_rotate.GetTypeName()).Set(
            moved_rotate
        )
        moved += 1
    if moved < 40:
        raise RuntimeError(
            f"AI console component set is incomplete: only {moved} prims"
        )
    return moved


def build() -> tuple[int, int, dict[str, int]]:
    source_stage = Usd.Stage.Open(str(SOURCE))
    if source_stage is None:
        raise RuntimeError(f"Could not open source stage: {SOURCE}")

    temporary = OUTPUT.with_name(f"{OUTPUT.stem}.tmp.usda")
    if temporary.exists():
        temporary.unlink()
    output_stage = Usd.Stage.CreateNew(str(temporary))
    output_stage.SetMetadata("metersPerUnit", 1.0)
    output_stage.SetMetadata("upAxis", "Z")
    port = output_stage.OverridePrim(PORT_PATH)
    port.CreateAttribute("yard:preservedCorridor", Sdf.ValueTypeNames.String).Set(
        "1C-4C; painted X=-8..8; logical bay coordinates unchanged"
    )
    port.CreateAttribute("yard:futureLayoutRule", Sdf.ValueTypeNames.String).Set(
        "Six yards repeat the 1C-4C bay template; the former coastal yard is split into a road and green tree belt"
    )
    port.CreateAttribute("yard:aiConsoleTarget", Sdf.ValueTypeNames.Double2).Set(
        AI_TARGET_CENTER
    )
    port.CreateAttribute("yard:aiConsoleRotation", Sdf.ValueTypeNames.Double).Set(
        AI_ROTATION_DEGREES
    )

    moved_count = _relocate_ai_console(source_stage, output_stage)
    removed_count = _remove_ground_objects(source_stage, output_stage)
    removed_count += _remove_original_bay_number_labels(source_stage, output_stage)
    for root_name in OLD_YARD_ROOTS:
        output_stage.OverridePrim(f"{PORT_PATH}/{root_name}").SetActive(False)

    rebuilt_root = output_stage.DefinePrim(REBUILT_ROOT_PATH, "Xform")
    rebuilt_root.CreateAttribute(
        "yard:columnCentersX", Sdf.ValueTypeNames.DoubleArray
    ).Set(Vt.DoubleArray(YARD_COLUMN_CENTERS_X))
    rebuilt_root.CreateAttribute("yard:columnWidth", Sdf.ValueTypeNames.Double).Set(
        YARD_COLUMN_WIDTH
    )
    rebuilt_root.CreateAttribute(
        "yard:rtgTireTrackOffsetsX", Sdf.ValueTypeNames.DoubleArray
    ).Set(Vt.DoubleArray((-RTG_TIRE_TRACK_OFFSET_X, RTG_TIRE_TRACK_OFFSET_X)))
    rebuilt_root.CreateAttribute("yard:bayPitch", Sdf.ValueTypeNames.Double).Set(
        BAY_PITCH_Y
    )
    rebuilt_root.CreateAttribute("yard:firstBayStartY", Sdf.ValueTypeNames.Double).Set(
        YARD_LAYOUT_START_Y_M
    )
    rebuilt_root.CreateAttribute("yard:lastBayEndY", Sdf.ValueTypeNames.Double).Set(
        YARD_BLOCK_SPECS[-1][3]
    )
    rebuilt_root.CreateAttribute("yard:template", Sdf.ValueTypeNames.String).Set(
        "Clean procedural 1C-4C bay markings with narrowed edges, yellow RTG tire tracks, and one central bay-number group"
    )
    rebuilt_root.CreateAttribute(
        "yard:terminalBlockLabelRule", Sdf.ValueTypeNames.String
    ).Set("one block label outside each yard block, centered longitudinally")
    rebuilt_root.CreateAttribute(
        "yard:terminalBlockLabelCount", Sdf.ValueTypeNames.Int
    ).Set(len(YARD_COLUMN_CENTERS_X) * len(YARD_BLOCK_SPECS))

    face_counts = {REBUILT_MARKINGS_NAME: _author_clean_bay_markings(output_stage)}
    face_counts[REBUILT_RTG_TRACKS_NAME] = _author_rtg_tire_tracks(output_stage)
    face_counts.update(_author_bay_number_labels(output_stage))
    face_counts.update(_author_terminal_block_labels(output_stage))

    output_stage.GetRootLayer().Save()
    output_stage = None
    os.replace(temporary, OUTPUT)
    return moved_count, removed_count, face_counts


def main() -> None:
    moved_count, removed_count, face_counts = build()
    print(
        f"Wrote {OUTPUT}; moved_ai_components={moved_count}; "
        f"removed_ground_objects={removed_count}; rebuilt_faces={face_counts}"
    )


if __name__ == "__main__":
    main()
