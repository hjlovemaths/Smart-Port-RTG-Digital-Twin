"""Shared bright bounding-box highlight for live RTG viewport selections."""

from __future__ import annotations

from typing import Any, Optional, Sequence


HIGHLIGHT_ROOT_PATH = "/World/RTG_LiveSelectionHighlight"
HIGHLIGHT_CORE_PATH = f"{HIGHLIGHT_ROOT_PATH}/Core"
HIGHLIGHT_GLOW_PATH = f"{HIGHLIGHT_ROOT_PATH}/Glow"
HIGHLIGHT_CORE_MATERIAL_PATH = "/World/Looks/RTG_LiveSelectionHighlight_Core"
HIGHLIGHT_GLOW_MATERIAL_PATH = "/World/Looks/RTG_LiveSelectionHighlight_Glow"


def show_selection_highlight(
    stage: Any,
    target_path: str,
    *,
    owner: str,
    padding: float = 0.18,
    exclude_name_tokens: Optional[Sequence[str]] = None,
) -> bool:
    """Draw a session-layer world-space bounding box around ``target_path``."""

    if stage is None or not target_path:
        return False

    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade
    except Exception as exc:
        print(f"[RTG Selection Highlight] pxr unavailable: {exc}")
        return False

    prim = stage.GetPrimAtPath(str(target_path))
    if not prim:
        return False

    bounds = _world_bounds(prim, exclude_name_tokens=exclude_name_tokens)
    if bounds is None:
        return False
    min_v, max_v = _pad_bounds(bounds[0], bounds[1], float(padding))
    points = _box_edge_points(min_v, max_v, Gf)

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        old = stage.GetPrimAtPath(HIGHLIGHT_ROOT_PATH)
        if old:
            stage.RemovePrim(old.GetPath())

        root = UsdGeom.Xform.Define(stage, HIGHLIGHT_ROOT_PATH).GetPrim()
        root.CreateAttribute("rtg:highlightOwner", Sdf.ValueTypeNames.String).Set(owner)
        root.CreateAttribute("rtg:highlightTarget", Sdf.ValueTypeNames.String).Set(
            str(target_path)
        )

        glow_mat = _highlight_material(
            stage,
            HIGHLIGHT_GLOW_MATERIAL_PATH,
            color=(0.02, 0.88, 1.0),
            opacity=0.30,
            UsdShade=UsdShade,
            Sdf=Sdf,
            Gf=Gf,
        )
        core_mat = _highlight_material(
            stage,
            HIGHLIGHT_CORE_MATERIAL_PATH,
            color=(0.86, 1.0, 1.0),
            opacity=1.0,
            UsdShade=UsdShade,
            Sdf=Sdf,
            Gf=Gf,
        )
        _define_edge_curves(
            stage,
            HIGHLIGHT_GLOW_PATH,
            points,
            width=0.16,
            material=glow_mat,
            UsdGeom=UsdGeom,
            UsdShade=UsdShade,
        )
        _define_edge_curves(
            stage,
            HIGHLIGHT_CORE_PATH,
            points,
            width=0.045,
            material=core_mat,
            UsdGeom=UsdGeom,
            UsdShade=UsdShade,
        )
    return True


def hide_selection_highlight(stage: Any, *, owner: Optional[str] = None) -> None:
    """Remove the shared highlight, optionally only when owned by ``owner``."""

    if stage is None:
        return
    try:
        from pxr import Usd
    except Exception:
        return

    root = stage.GetPrimAtPath(HIGHLIGHT_ROOT_PATH)
    if not root:
        return
    if owner is not None:
        attr = root.GetAttribute("rtg:highlightOwner")
        current_owner = attr.Get() if attr else None
        if current_owner != owner:
            return
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        root = stage.GetPrimAtPath(HIGHLIGHT_ROOT_PATH)
        if root:
            stage.RemovePrim(root.GetPath())


def _world_bounds(
    prim: Any,
    *,
    exclude_name_tokens: Optional[Sequence[str]] = None,
) -> Optional[tuple[Any, Any]]:
    try:
        from pxr import Usd, UsdGeom

        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            ["default", "render", "proxy"],
            useExtentsHint=False,
        )
        exclude_tokens = tuple(
            str(token).upper() for token in (exclude_name_tokens or ()) if str(token)
        )
        if exclude_tokens:
            boxes = []
            for candidate in prim.GetAllChildren():
                if _excluded_prim(candidate, exclude_tokens):
                    continue
                try:
                    imageable = UsdGeom.Imageable(candidate)
                    if not imageable:
                        continue
                    bound = cache.ComputeWorldBound(candidate).ComputeAlignedBox()
                    min_v = bound.GetMin()
                    max_v = bound.GetMax()
                    if any(not _finite(float(value)) for value in (*min_v, *max_v)):
                        continue
                    boxes.append((min_v, max_v))
                except Exception:
                    continue
            if boxes:
                return _combine_bounds(boxes)

        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        min_v = box.GetMin()
        max_v = box.GetMax()
        if any(not _finite(float(value)) for value in (*min_v, *max_v)):
            return None
        return min_v, max_v
    except Exception as exc:
        print(f"[RTG Selection Highlight] cannot compute bounds: {exc}")
        return None


def _excluded_prim(prim: Any, exclude_tokens: Sequence[str]) -> bool:
    try:
        text = f"{prim.GetName()} {prim.GetPath()}".upper()
    except Exception:
        return False
    return any(token in text for token in exclude_tokens)


def _combine_bounds(bounds: Sequence[tuple[Any, Any]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for min_v, max_v in bounds:
        for index in range(3):
            mins[index] = min(mins[index], float(min_v[index]))
            maxs[index] = max(maxs[index], float(max_v[index]))
    return tuple(mins), tuple(maxs)


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _pad_bounds(min_v: Any, max_v: Any, padding: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    mins = [float(value) for value in min_v]
    maxs = [float(value) for value in max_v]
    for index, (lo, hi) in enumerate(zip(mins, maxs)):
        if hi - lo < 0.05:
            center = (hi + lo) * 0.5
            mins[index] = center - 0.025
            maxs[index] = center + 0.025
        mins[index] -= padding
        maxs[index] += padding
    return tuple(mins), tuple(maxs)


def _box_edge_points(min_v: Sequence[float], max_v: Sequence[float], Gf: Any) -> list[Any]:
    x0, y0, z0 = min_v
    x1, y1, z1 = max_v
    corners = [
        Gf.Vec3f(x0, y0, z0),
        Gf.Vec3f(x1, y0, z0),
        Gf.Vec3f(x1, y1, z0),
        Gf.Vec3f(x0, y1, z0),
        Gf.Vec3f(x0, y0, z1),
        Gf.Vec3f(x1, y0, z1),
        Gf.Vec3f(x1, y1, z1),
        Gf.Vec3f(x0, y1, z1),
    ]
    edge_indices = (
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 0),
        (4, 5),
        (5, 6),
        (6, 7),
        (7, 4),
        (0, 4),
        (1, 5),
        (2, 6),
        (3, 7),
    )
    points = []
    for start, end in edge_indices:
        points.extend((corners[start], corners[end]))
    return points


def _highlight_material(
    stage: Any,
    path: str,
    *,
    color: tuple[float, float, float],
    opacity: float,
    UsdShade: Any,
    Sdf: Any,
    Gf: Any,
) -> Any:
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    rgb = Gf.Vec3f(*color)
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(rgb)
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(rgb)
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.18)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _define_edge_curves(
    stage: Any,
    path: str,
    points: Sequence[Any],
    *,
    width: float,
    material: Any,
    UsdGeom: Any,
    UsdShade: Any,
) -> None:
    curves = UsdGeom.BasisCurves.Define(stage, path)
    curves.CreateTypeAttr("linear")
    curves.CreateCurveVertexCountsAttr([2] * 12)
    curves.CreatePointsAttr(list(points))
    curves.CreateWidthsAttr([float(width)] * len(points))
    UsdShade.MaterialBindingAPI(curves.GetPrim()).Bind(material)
