"""Create virtual USD cameras at the authored RTG camera marker positions.

The RTG asset already contains 33 green marker prims named like ``C1`` through
``C33``.  This module scans the currently open Omniverse stage, creates virtual
cameras named ``C01`` through ``C33`` under ``/World/RTGVirtualCameras``, and
aims each camera toward the RTG work area so it can render the 3D world from
that installation point.

Run in Omniverse Script Editor::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_virtual_cameras.py", encoding="utf-8").read())
    create_rtg_virtual_cameras()

Switch the active viewport to one of the generated cameras::

    open_rtg_virtual_camera(1)
"""

from __future__ import annotations

import math
import re
import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


def _resolve_omniverse_root() -> Path:
    candidates = [Path(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse")]
    if "__file__" in globals():
        candidates.append(Path(__file__).resolve().parents[1])
    candidates.append(Path.cwd() / "omniverse")
    for candidate in candidates:
        if (candidate / "scenes" / "smart_port.usda").is_file():
            return candidate
    return candidates[0]


OMNIVERSE_ROOT = _resolve_omniverse_root()
PRIMARY_RTG_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
VIRTUAL_CAMERA_ROOT_PATH = "/World/RTGVirtualCameras"
DEFAULT_VIRTUAL_CAMERA_LAYER_PATH = OMNIVERSE_ROOT / "scenes" / "rtg_virtual_cameras.usda"
DEFAULT_LOOK_TARGET_PATH = (
    PRIMARY_RTG_PATH
    + "/ANIM_CTRL_RTG_GANTRY_TRAVEL/ANIM_CTRL_RTG_TROLLEY_TRAVEL/"
    "ANIM_CTRL_RTG_HOIST_VERTICAL"
)
VIRTUAL_CAMERA_NAME = "VirtualCamera"
DEFAULT_CAMERA_COUNT = 33
DEFAULT_FOCAL_LENGTH_MM = 18.0
DEFAULT_HORIZONTAL_APERTURE_MM = 20.955
DEFAULT_VERTICAL_APERTURE_MM = 15.2908
DEFAULT_CLIPPING_RANGE_M = (0.05, 2500.0)
# The imported asset keeps the Cxx label as a sibling of the green marker.  A
# small, explicit gap reads better than the old asset offsets while keeping the
# text clear of the marker mesh.
DEFAULT_LABEL_OFFSET_M = (0.0, 0.0, 0.14)
LABEL_ALIGNMENT_OP_SUFFIX = "rtgLabelAlignment"
MARKER_NAME_RE = re.compile(
    r"^(?:(?:CAMERA[_-]?MARKER|CAMERA|CAM)[_-]?C?|C)[_-]?(\d+)(?:[_-].*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VirtualCameraRecord:
    seq: int
    marker_path: str
    camera_path: str


@dataclass(frozen=True)
class VirtualCameraSpec:
    seq: int
    marker_path: str
    camera_path: str
    transform: Any
    focus_distance: float


def _marker_number(name_or_path: str) -> Optional[int]:
    """Return the camera marker number from a prim name or USD path."""

    components = [part for part in str(name_or_path).rstrip("/").split("/") if part]
    for component in reversed(components):
        match = MARKER_NAME_RE.match(component)
        if match is None:
            continue
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _virtual_camera_path(seq: int) -> str:
    return f"{VIRTUAL_CAMERA_ROOT_PATH}/C{int(seq):02d}"


def _is_camera_label_name(name: str) -> bool:
    """Return whether a prim name is the authored label for a camera marker."""

    return "label" in str(name).lower() and _marker_number(str(name)) is not None


def _is_generated_virtual_camera_path(path: str) -> bool:
    text = str(path)
    return text.startswith(f"{VIRTUAL_CAMERA_ROOT_PATH}/") or f"/{VIRTUAL_CAMERA_NAME}" in text


def _iter_prim_tree(root: Any) -> Iterable[Any]:
    stack = [root]
    while stack:
        prim = stack.pop()
        if not prim:
            continue
        yield prim
        try:
            children = list(prim.GetChildren())
        except Exception:
            children = []
        stack.extend(reversed(children))


def _candidate_marker_prims(stage: Any, expected_count: int) -> list[Any]:
    """Find marker prims, preferring Xform-like parents over mesh children."""

    search_roots = []
    primary = stage.GetPrimAtPath(PRIMARY_RTG_PATH)
    if primary:
        search_roots.append(primary)
    search_roots.append(stage.GetPseudoRoot())

    by_seq: dict[int, Any] = {}
    visited_paths: set[str] = set()
    for root in search_roots:
        for prim in _iter_prim_tree(root):
            path = str(prim.GetPath())
            if path in visited_paths or _is_generated_virtual_camera_path(path):
                continue
            visited_paths.add(path)

            seq = _marker_number(prim.GetName())
            if (
                seq is None
                or seq < 1
                or seq > expected_count
                or _is_camera_label_name(prim.GetName())
            ):
                continue
            previous = by_seq.get(seq)
            if previous is None or _marker_rank(prim) < _marker_rank(previous):
                by_seq[seq] = prim

    return [by_seq[seq] for seq in sorted(by_seq)]


def _camera_label_prims(stage: Any, expected_count: int) -> dict[int, Any]:
    """Find the authored Cxx label parents, preferring Xform/Scope prims."""

    primary = stage.GetPrimAtPath(PRIMARY_RTG_PATH)
    if not primary:
        return {}

    by_seq: dict[int, Any] = {}
    for prim in _iter_prim_tree(primary):
        if not _is_camera_label_name(prim.GetName()):
            continue
        seq = _marker_number(prim.GetName())
        if seq is None or seq < 1 or seq > expected_count:
            continue
        previous = by_seq.get(seq)
        if previous is None or _marker_rank(prim) < _marker_rank(previous):
            by_seq[seq] = prim
    return by_seq


def _marker_rank(prim: Any) -> tuple[int, int]:
    type_name = prim.GetTypeName() if prim else ""
    path_depth = len(str(prim.GetPath()).split("/"))
    if type_name in ("Xform", "Scope"):
        return (0, path_depth)
    if type_name == "Mesh":
        return (1, path_depth)
    return (2, path_depth)


def _current_stage() -> Any:
    import omni.usd  # type: ignore

    return omni.usd.get_context().get_stage()


def _world_position(stage: Any, prim_path: str) -> Optional[Any]:
    from pxr import UsdGeom

    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        return None
    return UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()


def _default_look_target(stage: Any, target_path: str) -> Any:
    from pxr import Gf, Usd, UsdGeom

    target = _world_position(stage, target_path)
    if target is not None:
        return target

    primary = stage.GetPrimAtPath(PRIMARY_RTG_PATH)
    if primary:
        bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        aligned = bbox.ComputeWorldBound(primary).ComputeAlignedBox()
        return (aligned.GetMin() + aligned.GetMax()) * 0.5

    return Gf.Vec3d(0.0, 0.0, 0.0)


def _distance(a: Any, b: Any) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def _camera_world_matrix(eye: Any, target: Any) -> Any:
    from pxr import Gf

    up = Gf.Vec3d(0.0, 0.0, 1.0)
    if _distance(eye, target) < 0.001:
        target = Gf.Vec3d(float(eye[0]), float(eye[1]) - 1.0, float(eye[2]) - 0.25)

    matrix = Gf.Matrix4d(1.0)
    try:
        matrix.SetLookAt(eye, target, up)
        return matrix.GetInverse()
    except Exception:
        forward = Gf.Vec3d(
            float(target[0]) - float(eye[0]),
            float(target[1]) - float(eye[1]),
            float(target[2]) - float(eye[2]),
        ).GetNormalized()
        right = Gf.Cross(up, forward)
        if right.GetLength() < 0.001:
            right = Gf.Vec3d(1.0, 0.0, 0.0)
        else:
            right.Normalize()
        true_up = Gf.Cross(forward, right).GetNormalized()
        return Gf.Matrix4d(
            right[0],
            right[1],
            right[2],
            0.0,
            true_up[0],
            true_up[1],
            true_up[2],
            0.0,
            -forward[0],
            -forward[1],
            -forward[2],
            0.0,
            eye[0],
            eye[1],
            eye[2],
            1.0,
        )


def _set_camera_transform(camera_prim: Any, matrix: Any) -> None:
    from pxr import UsdGeom

    xform = UsdGeom.Xformable(camera_prim)
    op = xform.AddTransformOp()
    op.Set(matrix)


def _hide_camera_helper(camera_prim: Any) -> None:
    """Hide the viewport camera helper while keeping the camera bindable."""

    from pxr import UsdGeom

    imageable = UsdGeom.Imageable(camera_prim)
    imageable.MakeInvisible()


def _set_attr(prim: Any, name: str, type_name: Any, value: Any) -> None:
    attr = prim.GetAttribute(name)
    if not attr:
        attr = prim.CreateAttribute(name, type_name)
    attr.Set(value)


def _virtual_camera_specs(
    stage: Any,
    *,
    expected_count: int = DEFAULT_CAMERA_COUNT,
    look_target_path: str = DEFAULT_LOOK_TARGET_PATH,
) -> list[VirtualCameraSpec]:
    markers = _candidate_marker_prims(stage, expected_count)
    if not markers:
        raise RuntimeError("No C1-C33 RTG camera marker prims were found in the current stage.")

    look_target = _default_look_target(stage, look_target_path)
    specs: list[VirtualCameraSpec] = []
    for marker in markers:
        seq = _marker_number(marker.GetName())
        if seq is None:
            continue
        marker_path = str(marker.GetPath())
        marker_position = _world_position(stage, marker_path)
        if marker_position is None:
            continue
        specs.append(
            VirtualCameraSpec(
                seq=seq,
                marker_path=marker_path,
                camera_path=_virtual_camera_path(seq),
                transform=_camera_world_matrix(marker_position, look_target),
                focus_distance=max(_distance(marker_position, look_target), 0.1),
            )
        )
    return specs


def _author_virtual_cameras(
    target_stage: Any,
    specs: list[VirtualCameraSpec],
    *,
    look_target_path: str,
    focal_length_mm: float,
) -> list[VirtualCameraRecord]:
    from pxr import Gf, Sdf, UsdGeom

    world = UsdGeom.Xform.Define(target_stage, "/World").GetPrim()
    target_stage.SetDefaultPrim(world)
    root = UsdGeom.Xform.Define(target_stage, VIRTUAL_CAMERA_ROOT_PATH).GetPrim()
    _set_attr(
        root,
        "rtg:virtualCameraSource",
        Sdf.ValueTypeNames.String,
        "C1-C33 green marker world positions",
    )

    records: list[VirtualCameraRecord] = []
    for spec in specs:
        camera = UsdGeom.Camera.Define(target_stage, spec.camera_path)
        camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
        camera.CreateFocalLengthAttr(float(focal_length_mm))
        camera.CreateHorizontalApertureAttr(DEFAULT_HORIZONTAL_APERTURE_MM)
        camera.CreateVerticalApertureAttr(DEFAULT_VERTICAL_APERTURE_MM)
        camera.CreateClippingRangeAttr(Gf.Vec2f(*DEFAULT_CLIPPING_RANGE_M))
        camera.CreateFocusDistanceAttr(spec.focus_distance)

        prim = camera.GetPrim()
        _set_camera_transform(prim, spec.transform)
        _hide_camera_helper(prim)
        try:
            prim.SetCustomDataByKey("omni:kit:hide_in_stage_window", False)
        except Exception:
            pass
        _set_attr(prim, "rtg:virtualCameraSeq", Sdf.ValueTypeNames.Int, spec.seq)
        _set_attr(prim, "rtg:cameraLabel", Sdf.ValueTypeNames.String, f"C{spec.seq}")
        _set_attr(prim, "rtg:markerPath", Sdf.ValueTypeNames.String, spec.marker_path)
        _set_attr(prim, "rtg:lookTargetPath", Sdf.ValueTypeNames.String, look_target_path)
        records.append(VirtualCameraRecord(spec.seq, spec.marker_path, spec.camera_path))
    return records


def _align_camera_labels(
    stage: Any,
    specs: list[VirtualCameraSpec],
    *,
    expected_count: int,
    offset_m: tuple[float, float, float] = DEFAULT_LABEL_OFFSET_M,
) -> list[str]:
    """Move each authored Cxx label close to its matching green marker.

    The labels are siblings of the marker meshes in the imported asset, so
    changing a label's local transform is the least invasive fix: it preserves
    the original text geometry/material and keeps the correction in the USD
    session layer.  A named additive op makes the correction idempotent when
    ``create_rtg_virtual_cameras()`` is run repeatedly.
    """

    from pxr import Gf, UsdGeom

    labels = _camera_label_prims(stage, expected_count)
    cache = UsdGeom.XformCache()
    aligned: list[str] = []
    for spec in specs:
        label = labels.get(spec.seq)
        marker_position = _world_position(stage, spec.marker_path)
        if label is None or marker_position is None:
            continue
        label_position = _world_position(stage, str(label.GetPath()))
        if label_position is None:
            continue

        target = marker_position + Gf.Vec3d(*offset_m)
        world_delta = target - label_position
        parent = label.GetParent()
        if parent:
            parent_world = cache.GetLocalToWorldTransform(parent)
            local_delta = parent_world.GetInverse().TransformDir(world_delta)
        else:
            local_delta = world_delta

        xformable = UsdGeom.Xformable(label)
        alignment_op = next(
            (
                op
                for op in xformable.GetOrderedXformOps()
                if op.GetOpName() == f"xformOp:translate:{LABEL_ALIGNMENT_OP_SUFFIX}"
            ),
            None,
        )
        if alignment_op is None:
            alignment_op = xformable.AddTranslateOp(
                opSuffix=LABEL_ALIGNMENT_OP_SUFFIX,
                precision=UsdGeom.XformOp.PrecisionDouble,
            )
            previous_delta = Gf.Vec3d(0.0, 0.0, 0.0)
        else:
            previous_delta = alignment_op.Get() or Gf.Vec3d(0.0, 0.0, 0.0)

        alignment_op.Set(
            Gf.Vec3d(
                float(previous_delta[0]) + float(local_delta[0]),
                float(previous_delta[1]) + float(local_delta[1]),
                float(previous_delta[2]) + float(local_delta[2]),
            )
        )
        aligned.append(str(label.GetPath()))
    if aligned:
        print(f"[RTG Virtual Cameras] aligned {len(aligned)} Cxx labels to green markers")
    return aligned


def _start_camera_popup_service() -> None:
    """Start the click-to-open RTSP popup, including Script Editor execution."""

    try:
        module = importlib.import_module("rtg_camera_popup")
    except Exception:
        script_path = OMNIVERSE_ROOT / "scripts" / "rtg_camera_popup.py"
        try:
            spec = importlib.util.spec_from_file_location("rtg_camera_popup", script_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {script_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules["rtg_camera_popup"] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            print(f"[RTG Virtual Cameras] click popup unavailable: {exc}")
            return

    try:
        module.start_rtg_camera_popup()
        print("[RTG Virtual Cameras] click a green Cxx marker to open its RTSP camera")
    except Exception as exc:
        print(f"[RTG Virtual Cameras] click popup start skipped: {exc}")


def _existing_virtual_camera_records(
    stage: Any,
    *,
    expected_count: int = DEFAULT_CAMERA_COUNT,
) -> list[VirtualCameraRecord]:
    records: list[VirtualCameraRecord] = []
    root = stage.GetPrimAtPath(VIRTUAL_CAMERA_ROOT_PATH)
    if not root:
        return records
    for prim in root.GetChildren():
        seq = _marker_number(prim.GetName())
        if seq is None or seq < 1 or seq > expected_count:
            continue
        marker_attr = prim.GetAttribute("rtg:markerPath")
        marker_path = marker_attr.Get() if marker_attr else ""
        records.append(VirtualCameraRecord(seq, str(marker_path), str(prim.GetPath())))
    return sorted(records, key=lambda record: record.seq)


def cleanup_rtg_virtual_cameras(stage: Optional[Any] = None) -> list[str]:
    """Remove generated virtual cameras from the current session layer."""

    from pxr import Sdf, Usd

    stage = stage or _current_stage()
    if stage is None:
        raise RuntimeError("No Omniverse USD stage is open.")

    paths: list[str] = []
    root = stage.GetPrimAtPath(VIRTUAL_CAMERA_ROOT_PATH)
    if root:
        paths.append(VIRTUAL_CAMERA_ROOT_PATH)

    for prim in _iter_prim_tree(stage.GetPseudoRoot()):
        path = str(prim.GetPath())
        if path == VIRTUAL_CAMERA_ROOT_PATH or path.startswith(f"{VIRTUAL_CAMERA_ROOT_PATH}/"):
            paths.append(path)
            continue
        if path.endswith(f"/{VIRTUAL_CAMERA_NAME}"):
            paths.append(path)

    removed: list[str] = []
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for path in sorted(set(paths), key=len, reverse=True):
            if stage.GetPrimAtPath(path):
                stage.RemovePrim(Sdf.Path(path))
                removed.append(path)
    if removed:
        print(f"[RTG Virtual Cameras] removed {len(removed)} old generated cameras")
    return removed


def create_rtg_virtual_cameras(
    stage: Optional[Any] = None,
    *,
    expected_count: int = DEFAULT_CAMERA_COUNT,
    look_target_path: str = DEFAULT_LOOK_TARGET_PATH,
    focal_length_mm: float = DEFAULT_FOCAL_LENGTH_MM,
    align_labels: bool = True,
    start_camera_popup: bool = True,
) -> list[VirtualCameraRecord]:
    """Create virtual cameras and enable click-to-open RTSP previews.

    ``start_camera_popup`` is enabled by default so the same call used to
    create C01-C33 also makes the green markers interactive.  Pass ``False``
    when running headless or when the popup service is managed separately.
    """

    from pxr import Usd

    stage = stage or _current_stage()
    if stage is None:
        raise RuntimeError("No Omniverse USD stage is open.")

    cleanup_rtg_virtual_cameras(stage)
    specs = _virtual_camera_specs(
        stage,
        expected_count=expected_count,
        look_target_path=look_target_path,
    )

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        if align_labels:
            _align_camera_labels(
                stage,
                specs,
                expected_count=expected_count,
            )
        records = _author_virtual_cameras(
            stage,
            specs,
            look_target_path=look_target_path,
            focal_length_mm=focal_length_mm,
        )

    if start_camera_popup:
        _start_camera_popup_service()

    print(f"[RTG Virtual Cameras] created {len(records)} cameras")
    for record in records:
        print(
            f"[RTG Virtual Cameras] C{record.seq:02d}: "
            f"{record.camera_path} at {record.marker_path}"
        )
    return records


def save_rtg_virtual_camera_layer(
    stage: Optional[Any] = None,
    *,
    layer_path: Optional[str] = None,
    expected_count: int = DEFAULT_CAMERA_COUNT,
    look_target_path: str = DEFAULT_LOOK_TARGET_PATH,
    focal_length_mm: float = DEFAULT_FOCAL_LENGTH_MM,
) -> list[VirtualCameraRecord]:
    """Bake C01-C33 cameras to ``scenes/rtg_virtual_cameras.usda``."""

    from pxr import Sdf, Usd, UsdGeom

    stage = stage or _current_stage()
    if stage is None:
        raise RuntimeError("No Omniverse USD stage is open.")

    output_path = Path(layer_path) if layer_path else DEFAULT_VIRTUAL_CAMERA_LAYER_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    specs = _virtual_camera_specs(
        stage,
        expected_count=expected_count,
        look_target_path=look_target_path,
    )

    layer = Sdf.Layer.CreateAnonymous("rtg_virtual_cameras.usda")
    target_stage = Usd.Stage.Open(layer)
    try:
        UsdGeom.SetStageUpAxis(target_stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(target_stage, 1.0)
    except Exception:
        pass
    target_stage.SetStartTimeCode(stage.GetStartTimeCode())
    target_stage.SetEndTimeCode(stage.GetEndTimeCode())
    target_stage.SetFramesPerSecond(stage.GetFramesPerSecond())
    target_stage.SetTimeCodesPerSecond(stage.GetTimeCodesPerSecond())
    records = _author_virtual_cameras(
        target_stage,
        specs,
        look_target_path=look_target_path,
        focal_length_mm=focal_length_mm,
    )
    if not layer.Export(str(output_path)):
        raise RuntimeError(f"Failed to save virtual camera layer: {output_path}")

    print(f"[RTG Virtual Cameras] saved {len(records)} cameras to {output_path}")
    return records


def open_rtg_virtual_camera(seq: int, stage: Optional[Any] = None) -> str:
    """Bind the active viewport to camera Cxx, creating session cameras if needed."""

    stage = stage or _current_stage()
    if stage is None:
        raise RuntimeError("No Omniverse USD stage is open.")

    records = _existing_virtual_camera_records(stage)
    if not records:
        records = create_rtg_virtual_cameras(stage)
    selected = next((record for record in records if record.seq == int(seq)), None)
    if selected is None:
        raise RuntimeError(f"Virtual camera C{int(seq):02d} was not found.")

    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore

        viewport = get_active_viewport()
        if viewport is not None:
            viewport.camera_path = selected.camera_path
    except Exception as exc:
        print(f"[RTG Virtual Cameras] viewport bind skipped: {exc}")

    try:
        import omni.usd  # type: ignore

        selection = omni.usd.get_context().get_selection()
        try:
            selection.clear_selected_prim_paths()
        except Exception:
            selection.set_selected_prim_paths([], False)
    except Exception:
        pass

    print(f"[RTG Virtual Cameras] active camera: C{selected.seq:02d} {selected.camera_path}")
    return selected.camera_path


if __name__ == "__main__":
    create_rtg_virtual_cameras()
