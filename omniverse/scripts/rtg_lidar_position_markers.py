"""Show the RTG's L1-L24 LiDAR installation points as blue spheres.

The supplied RTG drawing identifies the LiDAR locations relative to the
existing green C1-C33 camera markers, but it does not provide survey-grade
XYZ coordinates.  This helper therefore uses the authored C markers as
runtime anchors and adds a small blue offset.  The spheres are temporary
session-layer geometry and do not modify the source RTG model.  They are
renderable by default for visibility; pass ``renderable=False`` when a scan
must exclude the marker geometry.

Run in the Isaac Sim Script Editor after the stage is open::

    exec(open(r"D:/dnt/project/Smart Port RTG Digital Twin/omniverse/scripts/rtg_lidar_position_markers.py", encoding="utf-8").read())
    show_rtg_lidar_position_markers()

The markers follow the primary/middle RTG when its gantry, trolley, or hoist
is moved.  Remove them with ``remove_rtg_lidar_position_markers()``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from typing import Any, Optional


PRIMARY_PATH = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
GANTRY_PATH = f"{PRIMARY_PATH}/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY_PATH = f"{GANTRY_PATH}/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST_PATH = f"{TROLLEY_PATH}/ANIM_CTRL_RTG_HOIST_VERTICAL"
MARKER_ROOT_PATH = "/World/RTG_LIDAR_POSITION_MARKERS"
DEFAULT_MARKER_RADIUS_M = 0.16
BLUE_RGB = (0.02, 0.20, 1.0)
BLUE_EMISSIVE_RGB = (0.0, 0.14, 1.6)

_CAMERA_MARKER_RE = re.compile(
    r"^(?:(?:CAMERA[_-]?MARKER|CAMERA|CAM)[_-]?C?|C)[_-]?(\d+)(?:[_-].*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LidarMarkerSpec:
    """One drawing label and its approximate relation to a C marker."""

    name: str
    anchor_camera: Optional[int]
    world_offset: tuple[float, float, float]
    fallback_parent: str
    fallback_local: tuple[float, float, float]


# The C anchors are the green markers already present in RTG_Model.usdc.  The
# offsets are deliberately small: they keep the blue spheres legible beside
# the green camera markers without claiming millimetre-level survey accuracy.
# L1-L5/L23 are on the trolley, L6-L13 are on the spreader/hoist, L14-L17 are
# in the electrical-room side, L18-L21 are at the two gantry ends, L22 is on
# the outer electrical-room side, and L24 is on the main girder.
LIDAR_MARKER_SPECS: tuple[LidarMarkerSpec, ...] = (
    LidarMarkerSpec("L1", 29, (0.28, 0.00, -0.18), TROLLEY_PATH, (-3.78, 5.00, 9.15)),
    LidarMarkerSpec("L2", 1, (0.28, 0.00, -0.18), TROLLEY_PATH, (-3.78, 4.10, 9.15)),
    LidarMarkerSpec("L3", 3, (0.28, 0.00, -0.18), TROLLEY_PATH, (-3.78, 5.90, 9.15)),
    LidarMarkerSpec("L4", 2, (0.28, 0.00, -0.18), TROLLEY_PATH, (-3.05, 5.00, 9.15)),
    LidarMarkerSpec("L5", 3, (-0.28, 0.00, -0.18), TROLLEY_PATH, (-4.50, 5.00, 9.15)),
    LidarMarkerSpec("L6", 4, (0.00, 0.28, 0.18), HOIST_PATH, (2.10, -5.55, 0.00)),
    LidarMarkerSpec("L7", 4, (0.00, -0.28, 0.18), HOIST_PATH, (2.10, -4.45, 0.00)),
    LidarMarkerSpec("L8", 5, (0.00, 0.28, 0.18), HOIST_PATH, (1.00, -5.55, 0.00)),
    LidarMarkerSpec("L9", 5, (0.00, -0.28, 0.18), HOIST_PATH, (1.00, -4.45, 0.00)),
    LidarMarkerSpec("L10", 30, (0.00, 0.00, 0.25), HOIST_PATH, (1.55, -5.00, 0.00)),
    LidarMarkerSpec("L11", 30, (0.25, 0.00, 0.18), HOIST_PATH, (2.55, -5.00, 0.00)),
    LidarMarkerSpec("L12", 7, (0.00, 0.28, 0.18), HOIST_PATH, (2.55, -4.45, 0.00)),
    LidarMarkerSpec("L13", 31, (0.00, 0.00, 0.18), HOIST_PATH, (2.55, -5.55, 0.00)),
    LidarMarkerSpec("L14", 14, (0.20, 0.00, 0.20), GANTRY_PATH, (-4.00, -2.50, 5.00)),
    LidarMarkerSpec("L15", 15, (0.20, 0.00, 0.20), GANTRY_PATH, (-4.00, -1.70, 5.00)),
    LidarMarkerSpec("L16", 16, (-0.20, 0.00, 0.20), GANTRY_PATH, (-4.00, -0.90, 5.00)),
    LidarMarkerSpec("L17", 17, (-0.20, 0.00, 0.20), GANTRY_PATH, (-4.00, -0.10, 5.00)),
    LidarMarkerSpec("L18", 18, (0.00, 0.00, 0.24), GANTRY_PATH, (-4.80, -5.00, 0.65)),
    LidarMarkerSpec("L19", 19, (0.00, 0.00, 0.24), GANTRY_PATH, (-4.80, 5.00, 0.65)),
    LidarMarkerSpec("L20", 20, (0.00, 0.00, 0.24), GANTRY_PATH, (5.00, -5.00, 0.65)),
    LidarMarkerSpec("L21", 21, (0.00, 0.00, 0.24), GANTRY_PATH, (5.00, 5.00, 0.65)),
    LidarMarkerSpec("L22", 9, (0.25, 0.00, 0.20), GANTRY_PATH, (-4.00, 1.00, 5.60)),
    LidarMarkerSpec("L23", 29, (0.00, 0.00, 0.36), TROLLEY_PATH, (-3.78, 5.00, 9.70)),
    LidarMarkerSpec("L24", 8, (0.00, 0.00, 0.30), GANTRY_PATH, (0.00, 0.00, 8.20)),
)

_MARKER_SUBSCRIPTIONS: dict[int, Any] = {}
_MARKER_STATES: dict[int, dict[str, Any]] = {}


def _resolve_stage(stage: Any = None) -> Any:
    if stage is not None:
        return stage
    import omni.usd  # type: ignore

    resolved = omni.usd.get_context().get_stage()
    if resolved is None:
        raise RuntimeError("Open smart_port.usda before showing RTG LiDAR markers")
    return resolved


def _marker_number(name_or_path: str) -> Optional[int]:
    for component in str(name_or_path).rstrip("/").split("/"):
        match = _CAMERA_MARKER_RE.match(component)
        if match is None:
            continue
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _iter_prim_tree(root: Any):
    stack = [root]
    while stack:
        prim = stack.pop()
        if not prim:
            continue
        yield prim
        try:
            stack.extend(reversed(list(prim.GetChildren())))
        except Exception:
            continue


def _camera_marker_paths(stage: Any) -> dict[int, str]:
    """Find authored C1-C33 markers, preferring Xform/Scope parents."""

    primary = stage.GetPrimAtPath(PRIMARY_PATH)
    if not primary:
        return {}
    found: dict[int, Any] = {}
    for prim in _iter_prim_tree(primary):
        name = str(prim.GetName())
        if "label" in name.lower():
            continue
        seq = _marker_number(name)
        if seq is None or not 1 <= seq <= 33:
            continue
        previous = found.get(seq)
        if previous is None:
            found[seq] = prim
            continue
        preferred = ("Xform", "Scope")
        if prim.GetTypeName() in preferred and previous.GetTypeName() not in preferred:
            found[seq] = prim
    return {seq: str(prim.GetPath()) for seq, prim in found.items()}


def _world_transform(stage: Any, prim_path: str, cache: Any) -> Any:
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None
    return cache.GetLocalToWorldTransform(prim)


def _set_translate(prim: Any, position: Any, Gf: Any, UsdGeom: Any) -> None:
    xform = UsdGeom.Xformable(prim)
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            op.Set(Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
            return
    xform.AddTranslateOp().Set(
        Gf.Vec3d(float(position[0]), float(position[1]), float(position[2]))
    )


def _blue_material(stage: Any, root_path: str, Gf: Any, Sdf: Any, UsdShade: Any) -> Any:
    material_path = f"{root_path}/BlueLidarMaterial"
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, f"{material_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*BLUE_RGB))
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*BLUE_EMISSIVE_RGB)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.18)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _position_for_spec(
    stage: Any,
    spec: LidarMarkerSpec,
    camera_paths: dict[int, str],
    cache: Any,
    Gf: Any,
    UsdGeom: Any,
) -> tuple[Any, str]:
    if spec.anchor_camera is not None:
        anchor_path = camera_paths.get(spec.anchor_camera)
        if anchor_path:
            transform = _world_transform(stage, anchor_path, cache)
            if transform is not None:
                position = transform.ExtractTranslation() + Gf.Vec3d(*spec.world_offset)
                return position, f"C{spec.anchor_camera}"
    fallback_transform = _world_transform(stage, spec.fallback_parent, cache)
    if fallback_transform is None:
        raise RuntimeError(f"Missing RTG marker fallback parent: {spec.fallback_parent}")
    position = fallback_transform.Transform(Gf.Vec3d(*spec.fallback_local))
    position += Gf.Vec3d(*spec.world_offset)
    return position, spec.fallback_parent


def update_rtg_lidar_position_markers(stage: Any = None) -> dict[str, Any]:
    """Recompute all blue sphere positions from the current primary RTG pose."""

    resolved = _resolve_stage(stage)
    state = _MARKER_STATES.get(id(resolved))
    if state is None:
        return {"started": False, "count": 0, "message": "markers are not active"}
    import omni.usd  # type: ignore  # noqa: F401
    from pxr import Gf, Usd, UsdGeom

    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    camera_paths = _camera_marker_paths(resolved)
    updates: list[dict[str, Any]] = []
    with Usd.EditContext(resolved, resolved.GetSessionLayer()):
        for spec in LIDAR_MARKER_SPECS:
            prim = resolved.GetPrimAtPath(f"{MARKER_ROOT_PATH}/{spec.name}")
            if not prim or not prim.IsValid():
                continue
            position, source = _position_for_spec(
                resolved, spec, camera_paths, cache, Gf, UsdGeom
            )
            _set_translate(prim, position, Gf, UsdGeom)
            updates.append(
                {
                    "name": spec.name,
                    "path": str(prim.GetPath()),
                    "anchor": source,
                    "position": [float(position[0]), float(position[1]), float(position[2])],
                }
            )
    state["last_update"] = updates
    return {
        "started": True,
        "count": len(updates),
        "anchorCount": sum(1 for item in updates if str(item["anchor"]).startswith("C")),
        "fallbackCount": sum(1 for item in updates if not str(item["anchor"]).startswith("C")),
        "rootPath": MARKER_ROOT_PATH,
        "markers": updates,
    }


def show_rtg_lidar_position_markers(
    stage: Any = None,
    *,
    radius_m: float = DEFAULT_MARKER_RADIUS_M,
    renderable: bool = True,
) -> dict[str, Any]:
    """Create and continuously update blue L1-L24 spheres.

    ``renderable=True`` is the useful interactive mode: the spheres appear in
    the normal Rendered viewport.  Pass ``renderable=False`` to use USD guide
    purpose, which is safer for a capture-only session because guide geometry
    is normally excluded from RTX sensor products.
    """

    resolved = _resolve_stage(stage)
    if radius_m <= 0:
        raise ValueError("radius_m must be greater than zero")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

    key = id(resolved)
    if key in _MARKER_STATES and resolved.GetPrimAtPath(MARKER_ROOT_PATH):
        with Usd.EditContext(resolved, resolved.GetSessionLayer()):
            purpose = (
                UsdGeom.Tokens.default if renderable else UsdGeom.Tokens.guide
            )
            for spec in LIDAR_MARKER_SPECS:
                prim = resolved.GetPrimAtPath(
                    f"{MARKER_ROOT_PATH}/{spec.name}"
                )
                if prim and prim.IsValid():
                    UsdGeom.Imageable(prim).CreatePurposeAttr().Set(purpose)
        _MARKER_STATES[key]["renderable"] = bool(renderable)
        return update_rtg_lidar_position_markers(resolved)

    with Usd.EditContext(resolved, resolved.GetSessionLayer()):
        existing = resolved.GetPrimAtPath(MARKER_ROOT_PATH)
        if existing and existing.IsValid():
            resolved.RemovePrim(Sdf.Path(MARKER_ROOT_PATH))
        root = UsdGeom.Xform.Define(resolved, MARKER_ROOT_PATH).GetPrim()
        root.SetCustomDataByKey("rtg:markerType", "RTX LiDAR installation point")
        root.SetCustomDataByKey("rtg:markerLabels", "L1-L24")
        root.SetCustomDataByKey(
            "rtg:calibrationNote",
            "Approximate offsets from authored C camera markers; survey calibration required",
        )
        material = _blue_material(resolved, MARKER_ROOT_PATH, Gf, Sdf, UsdShade)
        purpose = UsdGeom.Tokens.default if renderable else UsdGeom.Tokens.guide
        for spec in LIDAR_MARKER_SPECS:
            sphere = UsdGeom.Sphere.Define(
                resolved, f"{MARKER_ROOT_PATH}/{spec.name}"
            )
            sphere.CreateRadiusAttr().Set(float(radius_m))
            sphere.CreatePurposeAttr().Set(purpose)
            sphere.CreateDisplayColorAttr().Set([Gf.Vec3f(*BLUE_RGB)])
            sphere.GetPrim().SetCustomDataByKey("rtg:lidarLabel", spec.name)
            sphere.GetPrim().SetCustomDataByKey(
                "rtg:anchorCamera",
                "" if spec.anchor_camera is None else f"C{spec.anchor_camera}",
            )
            UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)

    _MARKER_STATES[key] = {
        "stage": resolved,
        "last_update": [],
        "renderable": bool(renderable),
    }
    result = update_rtg_lidar_position_markers(resolved)

    try:
        import omni.kit.app  # type: ignore

        app = omni.kit.app.get_app()
        post_update = getattr(app, "get_post_update_event_stream", None)
        stream = post_update() if callable(post_update) else app.get_update_event_stream()

        def on_update(_event: Any) -> None:
            try:
                update_rtg_lidar_position_markers(resolved)
            except Exception as error:
                print(f"[RTG LiDAR markers] update skipped: {error}")

        _MARKER_SUBSCRIPTIONS[key] = stream.create_subscription_to_pop(
            on_update,
            name="RTG L1-L24 LiDAR position markers",
        )
        result["followPrimaryRtg"] = True
    except Exception as error:
        # A one-shot marker placement is still useful in minimal Kit sessions.
        result["followPrimaryRtg"] = False
        result["subscriptionError"] = str(error)

    print(
        "[RTG LiDAR markers] blue L1-L24 spheres created: "
        f"{result.get('anchorCount', 0)} C-anchored, "
        f"{result.get('fallbackCount', 0)} fallback; root={MARKER_ROOT_PATH}"
    )
    return result


def remove_rtg_lidar_position_markers(stage: Any = None) -> dict[str, Any]:
    """Remove the temporary blue marker spheres and their update callback."""

    resolved = _resolve_stage(stage)
    key = id(resolved)
    subscription = _MARKER_SUBSCRIPTIONS.pop(key, None)
    if subscription is not None:
        unsubscribe = getattr(subscription, "unsubscribe", None)
        if callable(unsubscribe):
            unsubscribe()
    from pxr import Sdf, Usd

    removed = False
    with Usd.EditContext(resolved, resolved.GetSessionLayer()):
        if resolved.GetPrimAtPath(MARKER_ROOT_PATH):
            resolved.RemovePrim(Sdf.Path(MARKER_ROOT_PATH))
            removed = True
    _MARKER_STATES.pop(key, None)
    print(f"[RTG LiDAR markers] removed={removed}")
    return {"removed": removed, "rootPath": MARKER_ROOT_PATH}


create_rtg_lidar_position_markers = show_rtg_lidar_position_markers
stop_rtg_lidar_position_markers = remove_rtg_lidar_position_markers


__all__ = [
    "LIDAR_MARKER_SPECS",
    "MARKER_ROOT_PATH",
    "create_rtg_lidar_position_markers",
    "remove_rtg_lidar_position_markers",
    "show_rtg_lidar_position_markers",
    "stop_rtg_lidar_position_markers",
    "update_rtg_lidar_position_markers",
]
