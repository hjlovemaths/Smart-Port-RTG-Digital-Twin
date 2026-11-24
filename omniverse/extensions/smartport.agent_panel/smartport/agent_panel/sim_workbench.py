"""SIM axis and point-cloud controls for the main RTG and STS_01."""

from . import sim_pointcloud
from sts_lidar_mounts import CRANE_ID

AXES = {"gantry_m": "大车", "trolley_m": "小车", "hoist_m": "吊具"}
JOG_STEP_M = 0.5
DEVICES = {"rtg": "主 RTG", "sts01": CRANE_ID}


def _status(runtime, equipment):
    if equipment == "sts01":
        status = runtime.equipment_status({"equipment": "sts", "crane_id": CRANE_ID})["sts"]
        if not status:
            raise ValueError("STS_01 轴状态不可用。")
        sts = status[0]
        positions = {axis: sts["positions"][axis[:-2]] for axis in AXES}
        limits = {axis: sts["limits"][axis[:-2]] for axis in AXES}
        moving = not sts["stopped"] and sts["positions"] != sts["targets"]
        return positions, limits, moving
    rtg = runtime.equipment_status({"equipment": "rtg"}, simulation=True)["rtg"]
    if "positions_m" not in rtg:
        raise ValueError("主 RTG 轴状态不可用：" + rtg["error"])
    # Drive targets are float32. Strip readback noise before the panel and
    # jog command compare the next 0.5 m step with the soft limits.
    positions = rtg["positions_m"]
    for axis in ("gantry_m", "hoist_m"):
        positions[axis] = round(positions[axis], 4)
    return positions, rtg["limits"], runtime.motion is not None


def state(runtime, equipment="rtg"):
    if equipment not in DEVICES:
        raise ValueError("请选择主 RTG 或 STS_01。")
    positions, limits, moving = _status(runtime, equipment)
    active_capture = runtime.operations.get(runtime.sim_capture_id)
    capture = active_capture if active_capture and active_capture.get("equipment") == equipment else None
    operation_id = runtime.sim_sts_motion_id if equipment == "sts01" else runtime.sim_motion_id
    return {
        "equipment": equipment,
        "positions_m": positions,
        "limits": limits,
        "busy": moving,
        "operation": runtime.operations.get(operation_id),
        "pointcloud": {
            "mounts": sim_pointcloud.mount_settings(equipment),
            "busy": bool(active_capture and active_capture["status"] == "running"),
            "status": capture["status"] if capture else "idle",
            "error": capture.get("error") if capture else None,
            "result": capture["result"] if capture and capture["status"] == "completed"
            else sim_pointcloud.latest_result(runtime.project_root, equipment),
        },
    }


def _sts_motion(runtime):
    operation = runtime.operations.get(runtime.sim_sts_motion_id)
    if not operation or operation["status"] != "running":
        return None, None
    controller = runtime.owned_sts.get(CRANE_ID)
    if not controller or controller.closed or controller.stopped or controller.targets != operation["controller_targets"]:
        operation["status"] = "cancelled"
        return None, None
    return operation, controller


def stop_sts_motion(runtime):
    operation, controller = _sts_motion(runtime)
    if operation is not None:
        controller.hold()
        operation["status"] = "cancelled"


def update_sts_motion(runtime):
    operation, controller = _sts_motion(runtime)
    if operation is None:
        return
    try:
        runtime._guard_manual("sts", CRANE_ID)
    except ValueError as error:
        controller.hold()
        operation.update(status="failed", error=str(error))
        return
    axis = operation["axis"]
    start, target = operation["start"][axis], operation["target"][axis]
    position = controller.state[axis[:-2]]
    if abs(position - target) <= 1e-6:
        operation.update(
            status="completed", progress=1.0,
            positions_m={key: controller.state[key[:-2]] for key in AXES},
        )
    elif target != start:
        operation["progress"] = max(0.0, min(1.0, (position - start) / (target - start)))


def command(runtime, payload):
    equipment = payload.get("equipment", "rtg")
    if not isinstance(equipment, str) or equipment not in DEVICES:
        raise ValueError("请选择主 RTG 或 STS_01。")
    sts = equipment == "sts01"
    label = DEVICES[equipment]
    action = payload.get("action")
    if action == "focus":
        view = {"target": "sts", "crane_id": CRANE_ID} if sts else {"target": "rtg"}
        runtime.set_view(dict(view, view="angled", follow=True))
        return state(runtime, equipment)
    if action == "stop":
        if sts:
            stop_sts_motion(runtime)
        elif runtime.motion and runtime.motion["id"] == runtime.sim_motion_id:
            runtime._cancel_motion()
        return state(runtime, equipment)
    if action == "scan_view":
        view = ({"target": "sts", "crane_id": CRANE_ID, "view": "under_sts_spreader"}
                if sts else {"target": "trolley", "view": "under_trolley"})
        runtime.set_view(dict(view, follow=True))
        return state(runtime, equipment)
    if action == "capture_pointcloud":
        range_m = payload.get("range_m")
        if type(range_m) is not int or range_m not in sim_pointcloud.RANGES_M:
            raise ValueError("采集范围请选择 20、40 或 60 米。")
        if _status(runtime, equipment)[2]:
            raise ValueError(f"请等待 {label} 停稳后再采集点云。")
        runtime._guard_manual("sts" if sts else "rtg", CRANE_ID if sts else None)
        job = runtime.job("sim_pointcloud", sim_pointcloud.capture(runtime.stage, runtime.project_root, range_m, equipment))
        runtime.sim_capture_id = job["operation_id"]
        runtime.operations[runtime.sim_capture_id]["equipment"] = equipment
        return state(runtime, equipment)

    axis = payload.get("axis")
    if action not in ("move", "jog") or not isinstance(axis, str) or axis not in AXES:
        raise ValueError(f"请选择有效的 {label} 轴和调试动作。")
    positions, limits, moving = _status(runtime, equipment)
    if moving:
        raise ValueError(f"{label} 已在运动，请等待结束或停止当前调试动作。")
    if action == "jog":
        direction = payload.get("direction")
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("请选择正向或负向点动。")
        target = positions[axis] + direction * JOG_STEP_M
    else:
        target = payload.get("target_m")
    lower, upper = limits[axis]
    if type(target) not in (int, float) or not lower <= target <= upper:
        raise ValueError(f"{AXES[axis]}目标须在 {lower:.3f} 到 {upper:.3f} m 的限位内。")
    if sts:
        report = runtime.move_sts({"crane_id": CRANE_ID, axis: target})
        runtime.sim_sts_motion_id = runtime.operation(
            "sim_sts_motion", axis=axis, start=positions, target={**positions, axis: target},
            progress=0.0, controller_targets=report["targets"],
        )
    else:
        result = runtime.rtg_move({axis: target}, simulation=True)
        runtime.sim_motion_id = result["operation_id"]
        runtime.operations[runtime.sim_motion_id]["axis"] = axis
    return state(runtime, equipment)
