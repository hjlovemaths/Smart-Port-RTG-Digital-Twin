"""Read device poses in the shared demonstration map coordinates."""

import math
import re

from .viewport_navigation import STATIC_RTG_NAMES

RTG_ROOT = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
STS_ROOT = "/World/STSControls"
PARK = "/World/ParkedYardTrucks"
EXTERNAL = "/World/IECS/ExternalTrucks"
TRUCK_MARKERS = "/World/IECS/TruckMarkers/"
RTG_STATIC = "/World/PortAndRTG/SMART_PORT_STATIC_RTG_YARD_"
TRUCK_REPLACEMENTS = (
    "/World/TruckDriverDemo/Truck",
    "/World/ShipToYardTransfer/Truck",
    "/World/YardToShipTransfer/Truck",
)
TASK_TRUCK = "/World/LiveContainers/LiveTaskTruck"

# WGS 84 local east/north scale at the demonstration origin (0°, 0°).
# A tangent-plane approximation is sufficient for this sub-kilometre scene.
_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257223563
_EAST_METRES_PER_DEGREE = math.radians(1) * _WGS84_A
_NORTH_METRES_PER_DEGREE = _EAST_METRES_PER_DEGREE * (1 - _WGS84_F * (2 - _WGS84_F))


def map_coordinates(scene_x_m, scene_y_m):
    # The quay is toward -X; the control center is toward +X and +Y.
    return {"longitude": scene_y_m / _EAST_METRES_PER_DEGREE, "latitude": -scene_x_m / _NORTH_METRES_PER_DEGREE}


def _region(name, x_min, y_min, x_max, y_max, kind):
    return {"name": name, "kind": kind, "sw": map_coordinates(x_max, y_min), "ne": map_coordinates(x_min, y_max)}


def _map_layout():
    from build_ground_traffic_network import CAUSEWAYS, GATE_Y
    from yard_coordinate_mapping import YARD_BLOCKS, YARD_LAYOUT_START_Y_M

    regions = [
        _region("岸桥码头", -256, -10, -201.5, 505, "land"),
        _region("堆场", -101.5, -12, 154, 503, "land"),
        _region("停车区", 129, 63, 148, 212, "parking"),
        _region("闸口", 125, GATE_Y - 5, 151, GATE_Y + 5, "bridge"),
    ]
    regions.extend(_region("", -202, y - 3.7, -101, y + 3.7, "bridge") for y in CAUSEWAYS)
    regions.extend(
        _region(block.name, -73, YARD_LAYOUT_START_Y_M + block.start_m,
                88, YARD_LAYOUT_START_Y_M + block.end_m, "yard")
        for block in YARD_BLOCKS
    )
    return {
        "name": "演示坐标 · 场景 +Y 向东，−X 向北",
        "origin": map_coordinates(0, 0),
        "bounds": _region("", -276, -26, 166, 522, "water"),
        "regions": regions,
        # Center of the existing control center in build_port_service_areas.py.
        "control_center": map_coordinates(138, 470),
    }


def _attr(prim, name, default=None):
    attribute = prim.GetAttribute(name) if prim else None
    value = attribute.Get() if attribute else None
    return default if value is None else value


def _truck_name(number):
    return f"{number:02d} 号集卡"


def _park_name(name):
    match = re.fullmatch(r"Group([12])_Bay(0[1-9]|10)", name)
    if match:
        return _truck_name((int(match[1]) - 1) * 10 + int(match[2]))
    return None


def _device_root(hit_path):
    suffix = hit_path.split("/")
    if hit_path == RTG_ROOT or hit_path.startswith(RTG_ROOT + "/"):
        return RTG_ROOT, "rtg", "主 RTG"
    if hit_path.startswith(RTG_STATIC):
        root = "/".join(suffix[:4])
        suffix_code = root[len(RTG_STATIC):]
        if suffix_code in STATIC_RTG_NAMES:
            return root, "rtg", suffix_code + " RTG"
    if hit_path.startswith(STS_ROOT + "/") and re.fullmatch(r"STS_\d{2}", suffix[3]):
        return "/".join(suffix[:4]), "sts", suffix[3]
    if hit_path.startswith(PARK + "/") and (label := _park_name(suffix[3])):
        return "/".join(suffix[:4]), "truck", label
    if hit_path.startswith(EXTERNAL + "/") and re.fullmatch(r"EXT_\d+", suffix[4]):
        return "/".join(suffix[:5]), "truck", suffix[4].replace("_", "-")
    for path in (*TRUCK_REPLACEMENTS, TASK_TRUCK, "/World/STSUnloadTask/Truck"):
        if hit_path == path or hit_path.startswith(path + "/"):
            label = _truck_name(1) if path in TRUCK_REPLACEMENTS else "作业集卡"
            return path, "truck", label
    return None


def _local_position(stage, path, time):
    from pxr import UsdGeom

    prim = stage.GetPrimAtPath(path)
    if prim:
        return UsdGeom.Xformable(prim).GetLocalTransformation(time).ExtractTranslation()
    return None


def _rtg_axes(stage, time):
    from pxr import UsdGeom, UsdPhysics
    import rtg_live_control as rtg

    gantry = stage.GetPrimAtPath(rtg.GANTRY_PATH)
    if gantry and any(op.GetOpName() in ("xformOp:transform:shipTransfer", "xformOp:transform:stsYardTask",
                                        "xformOp:transform:iecsRtg")
                      for op in UsdGeom.Xformable(gantry).GetOrderedXformOps()):
        g, t, h = (_local_position(stage, path, time) for path in (rtg.GANTRY_PATH, rtg.TROLLEY_PATH, rtg.HOIST_PATH))
        if g is not None and t is not None and h is not None:
            return {"gantry": rtg.gantry_usd_to_actual(g[1]), "trolley": t[0], "hoist": h[2]}, "大车为工程坐标，小车与吊具为场景轴位移"

    values = {}
    for axis, path, converter in (
        ("gantry", rtg.GANTRY_JOINT_PATH, rtg.gantry_usd_to_actual),
        ("trolley", rtg.TROLLEY_JOINT_PATH, rtg.trolley_usd_to_actual),
        ("hoist", rtg.HOIST_JOINT_PATH, rtg.hoist_usd_to_actual),
    ):
        joint = stage.GetPrimAtPath(path)
        if joint:
            value = UsdPhysics.DriveAPI.Get(joint, UsdPhysics.Tokens.linear).GetTargetPositionAttr().Get()
            if value is not None and math.isfinite(value):
                values[axis] = converter(value)
    return values, "RTG 工程轴位置"


def _sts_axes(stage, path, prim):
    from sts_control import INITIAL

    # The STS controller authors these actual motion deltas for manual and task moves.
    axes = dict(INITIAL)
    axes["hoist"] += _attr(prim, "sts:raisedHomeOffset", 0)
    for axis, part, index, sign in (
        ("gantry", "/Gantry", 0, -1),
        ("trolley", "/Gantry/Trolley", 1, -1),
        ("hoist", "/Gantry/Trolley/Hoist", 2, 1),
    ):
        delta = _attr(stage.GetPrimAtPath(path + part), "xformOp:transform:stsControl")
        if delta is not None:
            axes[axis] += sign * delta.ExtractTranslation()[index]
    return {axis: axes[axis] for axis in ("gantry", "trolley", "hoist")}


def _device(stage, description, transforms):
    from pxr import UsdGeom

    path, kind, name = description
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsActive() or UsdGeom.Imageable(prim).ComputeVisibility(transforms.GetTime()) == UsdGeom.Tokens.invisible:
        return None
    axes, basis = {}, ""
    anchor = prim
    if kind == "sts":
        anchor = stage.GetPrimAtPath(path + "/Gantry") or prim
        axes, basis = _sts_axes(stage, path, prim), "STS 本机轴位置"
    elif path == RTG_ROOT:
        from rtg_live_control import GANTRY_PATH

        anchor = stage.GetPrimAtPath(GANTRY_PATH) or prim
        axes, basis = _rtg_axes(stage, transforms.GetTime())
    elif kind == "rtg":
        basis = "静态模型，无独立轴读数"

    position = transforms.GetLocalToWorldTransform(anchor).ExtractTranslation()
    unit = UsdGeom.GetStageMetersPerUnit(stage)
    scene_x, scene_y = position[0] * unit, position[1] * unit
    if kind == "rtg" and path != RTG_ROOT:
        scene_x = _attr(prim, "rtg:yardColumnCenterX", scene_x)
        scene_y = _attr(prim, "rtg:parkingCenterY", scene_y)
    return {
        "id": path, "kind": kind, "name": name, "position": map_coordinates(scene_x, scene_y),
        "axes": axes, "axis_basis": basis,
    }


def device_for_hit(stage, path, time):
    from pxr import UsdGeom

    if path.startswith(TRUCK_MARKERS):
        marker = path[len(TRUCK_MARKERS):].split("/", 1)[0]
        if marker.startswith("EXT_"):
            path = f"{EXTERNAL}/{marker}"
        elif (match := re.fullmatch(r"ITT_(00[1-9]|01\d|020)", marker)):
            group, slot = divmod(int(match[1]) - 1, 10)
            path = f"{PARK}/Group{group + 1}_Bay{slot + 1:02d}"
    description = _device_root(path)
    if path.startswith("/World/ParkedTruckNumbers/Truck"):
        match = re.fullmatch(r"Truck(0[1-9]|1\d|20)", path.split("/")[3])
        if match:
            number = int(match[1])
            group, slot = divmod(number - 1, 10)
            description = (f"{PARK}/Group{group + 1}_Bay{slot + 1:02d}", "truck", _truck_name(number))
    return _device(stage, description, UsdGeom.XformCache(time)) if description else None


def device_map_state(stage, time):
    from pxr import UsdGeom

    transforms = UsdGeom.XformCache(time)
    paths = [RTG_ROOT, TASK_TRUCK, "/World/STSUnloadTask/Truck", *TRUCK_REPLACEMENTS]
    for parent in ("/World/PortAndRTG", STS_ROOT, PARK, EXTERNAL):
        root = stage.GetPrimAtPath(parent)
        if root:
            paths.extend(str(child.GetPath()) for child in root.GetChildren()
                         if parent != "/World/PortAndRTG" or str(child.GetPath()).startswith(RTG_STATIC))
    devices = []
    for path in dict.fromkeys(paths):
        description = _device_root(path)
        if description and (device := _device(stage, description, transforms)):
            devices.append(device)
    return {"map": _map_layout(), "devices": devices}
