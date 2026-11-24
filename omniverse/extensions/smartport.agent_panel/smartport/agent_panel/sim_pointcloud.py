"""Capture and export RTG yard and STS_01 ship point clouds."""

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from rtg_lidar_mounts import GANTRY_PATH, HOIST_PATH, SIM_MOUNTS, TROLLEY_PATH, mount_transform, world_pose
from sts_lidar_mounts import STS_MOUNTS


RANGES_M = (20, 40, 60)
FORMATS = ("ply", "pcd", "npy")
RTG_PATH = GANTRY_PATH.rsplit("/", 1)[0]
POINT_COLORS = ((123, 145, 161), (245, 179, 89), (90, 217, 237))
LATEST_FILES = {"rtg": "latest-combined.json", "sts01": "latest-sts01.json"}
MOUNTS = {"rtg": SIM_MOUNTS, "sts01": STS_MOUNTS}


def mount_settings(equipment="rtg"):
    return [
        {
            "id": mount.id,
            "group": mount.group,
            "offset_m": mount.offset,
            "yaw_deg": mount.angles[0],
            "pitch_down_deg": mount.angles[1],
        }
        for mount in MOUNTS[equipment]
    ]


def _scan_directions(yaw, pitch, *, rotary, downward=False):
    import numpy as np

    azimuth_angles = np.arange(0, 360, 2) if rotary else np.arange(-70, 71, 2)
    azimuth, elevation = np.meshgrid(
        np.deg2rad(azimuth_angles), np.deg2rad(np.arange(-22, 24, 2)),
    )
    x, y, z = np.cos(elevation) * np.cos(azimuth), np.cos(elevation) * np.sin(azimuth), np.sin(elevation)
    yaw, pitch = np.deg2rad([yaw, pitch])
    horizontal = x * np.cos(pitch) + z * np.sin(pitch)
    vertical = -x * np.sin(pitch) + z * np.cos(pitch)
    directions = np.stack((
        horizontal * np.cos(yaw) - y * np.sin(yaw),
        horizontal * np.sin(yaw) + y * np.cos(yaw),
        vertical,
    ), axis=-1)
    return directions[vertical < 0] if rotary or downward else directions.reshape(-1, 3)


async def _raycast(query, raycast, app, rays):
    sequence = raycast.add_raycast_sequence()
    if sequence < 0:
        raise RuntimeError("三维场景无法创建射线采集队列。")
    try:
        result = raycast.submit_ray_to_raycast_sequence_array(sequence, rays)
        if result != query.Result.SUCCESS:
            raise RuntimeError(f"射线采集提交失败（{result.name}）。")
        try:
            async with asyncio.timeout(12):
                while True:
                    await app.next_update_async()
                    status, _, hits = raycast.get_latest_result_from_raycast_sequence_array(sequence)
                    if status == query.Result.SUCCESS and len(hits) == len(rays):
                        if any(hit.valid for hit in hits):
                            return hits
                    elif status not in (query.Result.SUCCESS, query.Result.RAYCAST_RESULT_NOT_READY):
                        raise RuntimeError(f"射线采集失败（{status.name}）。")
        except TimeoutError as error:
            raise RuntimeError("未收到有效点云。请确认数字港区画面正在渲染后重试。") from error
    finally:
        raycast.remove_raycast_sequence(sequence)


def _mounted_rays(query, stage, time_code, metres_per_unit, range_m, equipment):
    mounts = MOUNTS[equipment]
    parent_paths = dict.fromkeys(mount.parent_path for mount in mounts)
    transforms = {path: mount_transform(stage, path, time_code) for path in parent_paths}
    rays = []
    segments = {}
    for mount in mounts:
        position, yaw, pitch = world_pose(transforms[mount.parent_path], mount.offset, mount.angles)
        first = len(rays)
        rays.extend(
            query.Ray(position, tuple(direction), 0.25 / metres_per_unit, range_m / metres_per_unit, False)
            for direction in _scan_directions(
                yaw, pitch, rotary=mount.group == "trolley", downward=equipment == "sts01",
            )
        )
        segments[mount.id] = slice(first, len(rays))
    return rays, segments


def _structure_views(stage, time_code):
    import numpy as np
    from pxr import Usd, UsdGeom

    prim = stage.GetPrimAtPath(RTG_PATH)
    if not prim:
        raise ValueError("场景中找不到主 RTG。")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default() if time_code is None else time_code,
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
    )
    bounds = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    if bounds.IsEmpty():
        raise ValueError("主 RTG 暂无可扫描的结构。")
    lower = np.asarray(bounds.GetMin())
    upper = np.asarray(bounds.GetMax())
    center = (lower + upper) / 2
    half_span = (upper - lower) / 2
    corners = np.array(np.meshgrid(*zip(lower, upper))).reshape(3, -1).T

    # Scan the sides and underside from low stations, then the top from above.
    for height in (lower[2] + half_span[2] * .44, upper[2] + half_span[2] * .85):
        for azimuth in np.deg2rad(np.arange(0, 360, 45)):
            offset = (half_span[:2] + 6) * [np.cos(azimuth), np.sin(azimuth)]
            origin = np.array([*(center[:2] + offset), height])
            yield origin, center, corners


def _structure_rays(query, views, metres_per_unit, range_m):
    import numpy as np

    for origin, center, corners in views:
        forward = center - origin
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, [0, 0, 1])
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        relative = corners - origin
        depths = relative @ forward
        u, v = np.vstack((relative @ right, relative @ up)) / depths
        horizontal = np.linspace(u.min() - .025, u.max() + .025, 76)
        vertical = np.linspace(v.min() - .025, v.max() + .025, 62)
        x, y = np.meshgrid(horizontal, vertical)
        directions = forward + x.reshape(-1, 1) * right + y.reshape(-1, 1) * up
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        yield [
            query.Ray(tuple(origin), tuple(direction), .15 / metres_per_unit, range_m / metres_per_unit, False)
            for direction in directions
        ]


def _structure_part(path):
    if path.startswith(HOIST_PATH + "/"):
        return "spreader"
    name = path.rsplit("/", 1)[-1].lower()
    if path.startswith(TROLLEY_PATH + "/"):
        return "trolley"
    if any(term in name for term in ("wheel", "bogie", "tire", "tyre", "running_gear")):
        return "running_gear"
    if any(term in name for term in ("column", "diagonal_brace", "portal_side_diagonal")):
        return "columns"
    if "girder" in name or "beam" in name:
        return "beams"
    return "other"


def _yard_stats(hits, paths, segments):
    return_counts = {name: sum(hit.valid for hit in hits[segment]) for name, segment in segments.items()}
    container_hits = ["container" in path.lower() for path in paths]
    return_counts["containers"] = sum(container_hits)
    return_counts["column_containers"] = sum(
        sum(container_hits[segments[mount.id]]) for mount in SIM_MOUNTS if mount.group == "column"
    )
    # The main gantry travels along scene Y. Use the hit normal to distinguish
    # longitudinal box ends from box roofs and the sides parallel to travel.
    end_faces = Counter(positive_y=0, negative_y=0)
    end_face_scanners = {}
    for name, segment in segments.items():
        faces = Counter(
            "positive_y" if normal_y > 0 else "negative_y"
            for hit, is_container in zip(hits[segment], container_hits[segment])
            if is_container and abs(normal_y := hit.normal[1]) >= .9
        )
        end_faces.update(faces)
        end_face_scanners[name] = sum(faces.values())
    return {"returns": return_counts, "end_faces": dict(end_faces), "end_face_scanners": end_face_scanners}


async def _scan_structure(query, raycast, app, stage, time_code, metres_per_unit, range_m):
    views = list(_structure_views(stage, time_code))
    point_hits = []
    parts = Counter()
    rays = []
    for index, view_rays in enumerate(_structure_rays(query, views, metres_per_unit, range_m), 1):
        rays.extend(view_rays)
        if index % 4 and index < len(views):
            continue
        for hit in await _raycast(query, raycast, app, rays):
            if not hit.valid:
                continue
            path = hit.get_target_usd_path()
            if path.startswith(RTG_PATH + "/"):
                point_hits.append(tuple(hit.hit_position))
                parts[_structure_part(path)] += 1
        rays.clear()
    if not point_hits:
        raise ValueError("未命中主 RTG。请确认设备已加载，或增大量程后重试。")
    return point_hits, {"view_count": len(views), "parts": dict(parts)}


def _ship_points(hits, paths, segments):
    from large_vessel_layout import SHIP

    points, labels = [], []
    faces = Counter(top=0, side=0)
    returns = {}
    for name, segment in segments.items():
        count = 0
        for hit, path in zip(hits[segment], paths[segment]):
            if not path.startswith(SHIP + "/"):
                continue
            box = path.startswith(SHIP + "/StructureSlots/") or "container" in path[len(SHIP):].lower()
            points.append(tuple(hit.hit_position))
            labels.append(int(box))
            count += 1
            if box:
                if hit.normal[2] >= .9:
                    faces["top"] += 1
                elif abs(hit.normal[2]) <= .15:
                    faces["side"] += 1
        returns[name] = count
    if not points:
        raise ValueError("本次未扫到船体。请将 STS_01 小车移到船箱区上方，或选用更大量程。")
    return points, labels, {"returns": returns, "ship_faces": dict(faces)}


async def capture(stage, project_root, range_m, equipment="rtg"):
    import numpy as np
    import omni.kit.app
    import omni.kit.raycast.query as query
    from omni.kit.viewport.utility import get_active_viewport
    from pxr import UsdGeom

    viewport = get_active_viewport()
    app = omni.kit.app.get_app()
    time_code = viewport.time if viewport else None
    metres_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
    raycast = query.acquire_raycast_query_interface()
    rays, segments = _mounted_rays(query, stage, time_code, metres_per_unit, range_m, equipment)
    hits = await _raycast(query, raycast, app, rays)
    paths = [hit.get_target_usd_path() if hit.valid else "" for hit in hits]
    if equipment == "sts01":
        points, labels, stats = _ship_points(hits, paths, segments)
    else:
        stats = _yard_stats(hits, paths, segments)
        points = [tuple(hit.hit_position) for hit in hits if hit.valid]
        labels = [
            2 if path.startswith(RTG_PATH + "/") else int("container" in path.lower())
            for hit, path in zip(hits, paths) if hit.valid
        ]
        structure_points, structure_stats = await _scan_structure(
            query, raycast, app, stage, time_code, metres_per_unit, range_m,
        )
        points.extend(structure_points)
        labels.extend([2] * len(structure_points))
        stats.update(structure_stats)
    points = np.asarray(points, dtype=np.float32) * metres_per_unit
    labels = np.asarray(labels, dtype=np.uint8)
    # Prefer the RTG or box classification if neighbouring surfaces share a voxel.
    priority = np.argsort(-labels.astype(np.int8), kind="stable")
    _, keep = np.unique(np.floor(points[priority] / 0.035).astype(np.int32), axis=0, return_index=True)
    keep = priority[keep]
    points, labels = points[keep], labels[keep]
    surroundings, containers, rtg = (int(count) for count in np.bincount(labels, minlength=3))
    capture_id = uuid4().hex
    result = {
        "point_count": len(points),
        "point_counts": {"surroundings": surroundings, "containers": containers, "rtg": rtg},
        "equipment": equipment,
        "mode": "ship" if equipment == "sts01" else "combined",
        "frame": "world",
        "range_m": range_m,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scanner_count": len(segments),
        **stats,
        "preview": {"url": f"/api/sim/pointcloud/{capture_id}/preview", "colors": POINT_COLORS},
        "files": {
            extension: f"/api/sim/pointcloud/{capture_id}/{extension}"
            for extension in FORMATS
        },
    }
    await asyncio.to_thread(_export, project_root, capture_id, points, labels, result)
    return result


def _directory(project_root):
    return Path(project_root) / "outputs" / "rtg_pointcloud" / "sim"


def _export(project_root, capture_id, points, labels, result):
    import numpy as np
    from rtg_yard_lidar import write_npy, write_pcd

    directory = _directory(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory) as temporary:
        bundle = Path(temporary) / capture_id
        bundle.mkdir()
        stem = bundle / f"{result['equipment']}_{capture_id}"
        colors = np.asarray(POINT_COLORS, dtype=np.uint8)[labels]
        with stem.with_suffix(".ply").open("w", encoding="ascii", newline="\n") as stream:
            stream.write(
                f"ply\nformat ascii 1.0\nelement vertex {len(points)}\n"
                "property float x\nproperty float y\nproperty float z\n"
                "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
            )
            np.savetxt(stream, np.column_stack((points, colors)), fmt="%.6f %.6f %.6f %d %d %d")
        write_pcd(stem.with_suffix(".pcd"), points)
        write_npy(stem.with_suffix(".npy"), points)
        with stem.with_suffix(".preview").open("wb") as stream:
            stream.write(points.astype("<f4", copy=False).tobytes())
            stream.write(labels.tobytes())
        bundle.rename(directory / capture_id)
        latest = Path(temporary) / LATEST_FILES[result["equipment"]]
        latest.write_text(json.dumps(result), encoding="utf-8")
        latest.replace(directory / latest.name)


def latest_result(project_root, equipment="rtg"):
    try:
        return json.loads((_directory(project_root) / LATEST_FILES[equipment]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def download(project_root, capture_id, extension):
    if not re.fullmatch(r"[0-9a-f]{32}", capture_id) or extension not in (*FORMATS, "preview"):
        return None
    directory = _directory(project_root).resolve()
    for equipment in LATEST_FILES:
        path = (directory / capture_id / f"{equipment}_{capture_id}.{extension}").resolve()
        if path.is_relative_to(directory) and path.is_file():
            return path
    return None
