"""Main-thread adapters for scene views, equipment motion and task queries."""

from __future__ import annotations

import asyncio
import builtins
import copy
import importlib
import math
import sys
import time
from pathlib import Path
from typing import Any

from . import agent_api, runtime_catalog


RTG_ROOT = "/World/PortAndRTG/RTG_PRIMARY_DYNAMIC"
GANTRY = RTG_ROOT + "/ANIM_CTRL_RTG_GANTRY_TRAVEL"
TROLLEY = GANTRY + "/ANIM_CTRL_RTG_TROLLEY_TRAVEL"
HOIST = TROLLEY + "/ANIM_CTRL_RTG_HOIST_VERTICAL"
CAMERA_PATH = "/World/SmartPortAgent/ViewportCamera"
# In trolley space, below the upper rope anchors/platform (Z=8.81).
UNDER_TROLLEY_EYE = (2.55, -5.0, 8.0)
UNDER_TROLLEY_FOV_DEG = 110.0
CORE_TOOLS = {"get_agent_capabilities", "get_equipment_status", "get_current_tasks", "list_viewpoints",
              "set_viewport_view", "switch_viewport_camera", "set_sts_axis_positions", "stop_equipment",
              "get_operation_status", "cancel_operation"}


def read_attr(prim, name, default=None):
    if prim:
        attr = prim.GetAttribute(name)
        if attr:
            value = attr.Get()
            if value is not None:
                return value
    return default


def existing_module(name):
    module = sys.modules.get(name)
    if module is not None:
        return module
    # Script Editor and the launcher can assign stable alternate module names.
    for value in tuple(sys.modules.values()):
        filename = getattr(value, "__file__", None)
        if filename and Path(filename).name == name + ".py":
            return value
    return None


def current_sts_panel(stage):
    panel = getattr(builtins, "_smart_port_sts_panel", None)
    return panel if panel is not None and panel.stage == stage and panel.controllers is not None else None


def iecs_runtime(stage):
    runtime = getattr(builtins, "_iecs_runtime", None)
    return runtime if runtime is not None and not runtime.closed and runtime.stage == stage else None


def simulation_status(module):
    if module is None:
        return {"running": False, "state": "IDLE"}
    result = module.get_simulation_status()
    simulation = getattr(module, "_simulation", None)
    result["running"] = bool(simulation and simulation._subscription is not None)
    return result


def sts_controllers(stage):
    pool = getattr(builtins, "_smart_port_sts_controller_pool", None)
    controllers = dict(pool["controllers"]) if pool and pool["stage"] == stage else {}
    panel = current_sts_panel(stage)
    if panel:
        controllers.update(panel.controllers)
    return {key: value for key, value in controllers.items() if not value.closed}


def auxiliary_rtg_motion(stage):
    key = id(stage)
    result = {}
    for name, prefix in (("rtg_pick_place_controller", "_PICK_SEQUENCE"), ("rtg_isaac_physics", "_RTG_ANIMATION")):
        module = existing_module(name)
        subscriptions = getattr(module, prefix + "_SUBSCRIPTIONS", {}) if module else {}
        states = getattr(module, prefix + "_STATES", {}) if module else {}
        result["slot_pick" if prefix == "_PICK_SEQUENCE" else "visual_animation"] = {
            "running": key in subscriptions, "state": copy.deepcopy(states.get(key))}
    return result


def current_tasks(stage):
    status = stage.GetPrimAtPath("/World/LiveTwinState")
    fields = ("taskId", "taskState", "taskType", "workType", "operationState", "visualPhase",
              "startPos", "endPos", "sourcePos", "targetPos", "containerNo", "containerType",
              "bayMap", "currentBayId", "inventoryMode", "inventorySyncState", "inventorySyncReason",
              "lastTwinTimestampUtc", "rtgName", "runinfo")
    rtg = {field: read_attr(status, "rtg:" + field) for field in fields}
    rtg["available"] = bool(status and rtg["taskId"])
    simulator = existing_module("rtg_simulation_driver")
    sim_status = simulation_status(simulator)
    panel = current_sts_panel(stage)
    task = panel.unload_task if panel else None
    sts = {"available": bool(task)}
    if task:
        sts.update({"task_type": type(task).__name__, "phase": task.phase,
                    "paused": bool(task.paused or task.c.stopped), "finished": bool(task.finished),
                    "elapsed_seconds": float(task.elapsed), "crane_id": task.c.path.rsplit("/", 1)[-1],
                    "delivering_to_yard": bool(task.delivering)})
        if getattr(task, "delivery", None):
            sts["delivery_phase"] = task.delivery.phase
    iecs = iecs_runtime(stage)
    iecs_status = {"running": False}
    if iecs:
        snapshot = iecs.engine.snapshot()
        iecs_status = {key: value for key, value in snapshot.items() if key in {
            "name", "simulation_time", "paused", "speed", "orders", "vehicles", "alarms", "reservations", "equipment", "mode"}}
        iecs_status.update(running=True, error=iecs.last_error, mixed_pending=list(iecs.mixed_pending))
    return {"rtg": rtg, "rtg_simulation": sim_status, "rtg_auxiliary_motion": auxiliary_rtg_motion(stage), "sts": sts, "iecs": iecs_status,
            "note": "RTG attributes are the last received snapshot; its timestamp may be old. All commands here affect the digital scene only."}


class SceneRuntime:
    def __init__(self, project_root, notify=None):
        self.project_root = Path(project_root)
        self.notify = notify or (lambda event: None)
        self.stage = None
        self.motion = None
        self.operations = {}
        self.jobs = {}
        self.sequence = 0
        self.follow = None
        self.follow_viewport = None
        self.camera_viewport = None
        self.camera_layer = None
        self.previous_camera = None
        self.active_view = None
        self.camera_lens_signature = None
        self.owned_sts = {}
        self.last_tick = time.monotonic()

    def bind(self, stage):
        if self.stage != stage:
            self.release()
            self.stage = stage

    def operation(self, kind, **values):
        self.sequence += 1
        identifier = f"agent-{self.sequence}"
        self.operations[identifier] = {"operation_id": identifier, "kind": kind, "status": "running", **values}
        while len(self.operations) > 40:
            oldest = next((key for key, item in self.operations.items() if item["status"] != "running"), None)
            if oldest is None:
                break
            self.operations.pop(oldest)
        return identifier

    def job(self, kind, coroutine):
        if any(not task.done() for task in self.jobs.values()):
            coroutine.close()
            raise ValueError("A background operation is still running; wait or cancel it first")
        identifier = self.operation(kind)

        async def run():
            try:
                result = await coroutine
                self.operations[identifier].update(status="completed", result=result)
            except asyncio.CancelledError:
                self.operations[identifier]["status"] = "cancelled"
            except Exception as exc:
                self.operations[identifier].update(status="failed", error=str(exc))
            self.notify({"kind": "tool_result", "name": kind, "result": dict(self.operations[identifier])})

        self.jobs[identifier] = asyncio.ensure_future(run())
        return dict(self.operations[identifier])

    def _guard_manual(self, equipment, crane_id=None):
        if equipment == "rtg" and any(item["status"] == "running" and item["kind"] == "lidar_positions" for item in self.operations.values()):
            raise ValueError("A multi-position LiDAR capture owns the RTG; wait or cancel it first")
        iecs = iecs_runtime(self.stage)
        if iecs and any(order["status"] != "completed" for order in iecs.engine.orders.values()):
            raise ValueError("IECS owns the equipment for an unfinished task. Inspect get_current_tasks and finish or reset that task first.")
        panel = current_sts_panel(self.stage)
        task = panel.unload_task if panel else None
        if task and not task.finished and (equipment == "rtg" or task.c.path.endswith("/" + str(crane_id))):
            raise ValueError("An STS task owns this equipment. Finish or explicitly reset it before manual movement.")
        if equipment == "rtg":
            if any(item["running"] for item in auxiliary_rtg_motion(self.stage).values()):
                raise ValueError("An RTG slot-pick or visual animation owns the axes. Wait or stop_equipment before issuing another movement.")
            if simulation_status(existing_module("rtg_simulation_driver"))["running"]:
                raise ValueError("The RTG simulator is active. Stop it before manual movement.")
            driver = getattr(builtins, "_smart_port_rtg_panel_facing_driver", None)
            if driver and driver._current_stage() == self.stage and driver._subscription is not None:
                raise ValueError("The TwinState/simulation driver is updating the RTG. Stop that driver before manual movement.")
            if getattr(builtins, "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE", False):
                raise ValueError("Gamepad manual mode owns the RTG. Stop gamepad mode before Agent movement.")

    def rtg_move(self, arguments, bay=False):
        self._guard_manual("rtg")
        import rtg_live_control as live
        from yard_coordinate_mapping import bay_id_position_m
        controller = live.RTGController(self.stage)
        start = dict(zip(("gantry_m", "trolley_m", "hoist_m"), controller.get_positions()))
        end = dict(start)
        for key in end:
            if key in arguments:
                end[key] = arguments[key]
        if bay:
            bay_id_position_m(arguments["bay_id"], arguments.get("anchor", "center"))
            end["gantry_m"] = live.gantry_usd_to_actual(live.gantry_bay_id_to_usd(arguments["bay_id"], arguments.get("anchor", "center")))
        for key, limits in (("gantry_m", live.GANTRY_SOFT_LIMITS), ("trolley_m", live.TROLLEY_SOFT_LIMITS), ("hoist_m", live.HOIST_SOFT_LIMITS)):
            if (key in arguments or bay and key == "gantry_m") and not limits[0] <= end[key] <= limits[1]:
                raise ValueError(f"{key} must be between {limits[0]:.3f} and {limits[1]:.3f}")
        self._cancel_motion()
        identifier = self.operation("rtg_motion", start=start, target=end, duration_s=arguments.get("duration_s", 3.0), progress=0.0)
        axes = {"gantry_m"} if bay else set(arguments) & end.keys()
        self.motion = {"id": identifier, "controller": controller, "start": start, "end": end,
                       "axes": axes, "elapsed": 0.0, "duration": arguments.get("duration_s", 3.0)}
        return {**self.operations[identifier], "requested_bay": arguments.get("bay_id"),
                "message": "Motion started. Poll get_operation_status to confirm arrival. USD visualization only."}

    def _cancel_motion(self):
        if self.motion:
            self.operations[self.motion["id"]]["status"] = "cancelled"
            self.motion = None

    def tick(self, stage, dt=None):
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self.last_tick if dt is None else float(dt)))
        self.last_tick = now
        self.bind(stage)
        if stage is None:
            return
        if self.motion:
            motion = self.motion
            try:
                self._guard_manual("rtg")
                motion["elapsed"] += dt
                fraction = min(1.0, motion["elapsed"] / motion["duration"])
                alpha = fraction * fraction * (3.0 - 2.0 * fraction)
                for axis in motion["axes"]:
                    value = motion["start"][axis] + (motion["end"][axis] - motion["start"][axis]) * alpha
                    getattr(motion["controller"], {"gantry_m": "set_gantry_safe", "trolley_m": "set_trolley_safe", "hoist_m": "set_hoist_safe"}[axis])(value)
                operation = self.operations[motion["id"]]
                operation["progress"] = fraction
                if fraction >= 1:
                    operation.update(status="completed", positions_m=dict(zip(("gantry_m", "trolley_m", "hoist_m"), motion["controller"].get_positions())))
                    self.motion = None
                    self.notify({"kind": "tool_result", "name": "rtg_motion_complete", "result": dict(operation)})
            except Exception as exc:
                self.operations[motion["id"]].update(status="failed", error=str(exc))
                self.motion = None
                self.notify({"kind": "tool_result", "name": "rtg_motion_failed", "result": dict(self.operations[motion["id"]])})
        panel = current_sts_panel(stage)
        for key, controller in tuple(self.owned_sts.items()):
            if controller.closed:
                self.owned_sts.pop(key)
            elif panel is None or controller not in panel.controllers.values():
                controller.tick(dt)
        if self.follow:
            try:
                self._apply_view(self.follow, self.follow_viewport)
            except Exception as exc:
                self.follow = None
                self.follow_viewport = None
                self.notify({"kind": "status", "text": f"Camera following stopped: {exc}"})

    def equipment_status(self, args):
        import rtg_live_control as live
        result = {}
        if args.get("equipment", "all") in {"all", "rtg"}:
            try:
                from pxr import UsdPhysics
                positions = {}
                for key, joint_path, converter in (("gantry_m", live.GANTRY_JOINT_PATH, live.gantry_usd_to_actual),
                                                    ("trolley_m", live.TROLLEY_JOINT_PATH, live.trolley_usd_to_actual),
                                                    ("hoist_m", live.HOIST_JOINT_PATH, live.hoist_usd_to_actual)):
                    joint = UsdPhysics.PrismaticJoint.Get(self.stage, joint_path)
                    if not joint: raise ValueError(f"Missing RTG joint: {joint_path}")
                    value = UsdPhysics.DriveAPI.Get(joint.GetPrim(), UsdPhysics.Tokens.linear).GetTargetPositionAttr().Get()
                    positions[key] = float(converter(float(value or 0)))
                front = self.stage.GetPrimAtPath(live.SPREADER_FRONT_TELESCOPE_PATH)
                rear = self.stage.GetPrimAtPath(live.SPREADER_REAR_TELESCOPE_PATH)
                size = None
                if front and rear:
                    value = read_attr(front, "xformOp:translate:" + live.SPREADER_LIVE_TELESCOPE_OP_SUFFIX, (0, 0, 0))
                    offset = max(0.0, min(live.SPREADER_TELESCOPE_TRAVEL_M, float(value[1])))
                    size = 40.0 - 20.0 * offset / live.SPREADER_TELESCOPE_TRAVEL_M
                result["rtg"] = {"positions_m": positions,
                                 "spreader_size_ft": size,
                                 "limits": {"gantry_m": live.GANTRY_SOFT_LIMITS, "trolley_m": live.TROLLEY_SOFT_LIMITS, "hoist_m": live.HOIST_SOFT_LIMITS},
                                 "motion": self.operations.get(self.motion["id"]) if self.motion else None}
            except Exception as exc:
                result["rtg"] = {"available": False, "error": str(exc)}
        if args.get("equipment", "all") in {"all", "sts"}:
            from sts_control import INITIAL, LIMITS
            root = self.stage.GetPrimAtPath("/World/STSControls")
            controllers = sts_controllers(self.stage)
            result["sts"] = []
            for prim in root.GetChildren() if root else []:
                name = prim.GetName()
                if args.get("crane_id") and name != args["crane_id"]:
                    continue
                controller = controllers.get(name)
                offset = read_attr(prim, "sts:upperOffset", (0, 0, 0))
                limits = dict(LIMITS, gantry=(read_attr(prim, "sts:gantryMin"), read_attr(prim, "sts:gantryMax")),
                              hoist=(LIMITS["hoist"][0], LIMITS["hoist"][1] + float(offset[2])))
                result["sts"].append({"crane_id": name, "positions": dict(controller.state) if controller else dict(INITIAL),
                                      "targets": dict(controller.targets) if controller else dict(INITIAL),
                                      "limits": controller.limits if controller else limits,
                                      "stopped": controller.stopped if controller else False,
                                      "park_hoist": controller.park_hoist if controller else 7.7 + float(offset[2]),
                                      "units": "local scene control metres; boom degrees"})
        return result

    def move_sts(self, args):
        from sts_control import shared_sts_controller
        crane = args["crane_id"]
        self._guard_manual("sts", crane)
        existed = crane in sts_controllers(self.stage)
        controller = shared_sts_controller(self.stage, crane)
        self.owned_sts[crane] = controller
        values = {key: args[source] for source, key in (("gantry_m", "gantry"), ("trolley_m", "trolley"), ("hoist_m", "hoist"), ("boom_deg", "boom")) if source in args}
        if args.get("park_for_luffing"):
            values = {"trolley": -8.0, "hoist": controller.park_hoist}
        try:
            controller.command(**values)
        except Exception:
            if not existed:
                controller.close()
                self.owned_sts.pop(crane, None)
            raise
        return {"crane_id": crane, "status": "moving", "positions": dict(controller.state), "targets": dict(controller.targets),
                "message": "Command accepted; existing speed limits and boom interlocks apply. Poll get_equipment_status for arrival."}

    def stop_equipment(self, args):
        target = args["equipment"]
        if target in {"rtg", "all"}:
            self._cancel_motion()
            simulation = existing_module("rtg_simulation_driver")
            if simulation:
                simulation.stop_simulation()
            stop_live = getattr(builtins, "_smart_port_rtg_twin_stop_live_driver", None)
            if callable(stop_live):
                stop_live()
            physics = existing_module("rtg_isaac_physics")
            pick = existing_module("rtg_pick_place_controller")
            if pick: pick.stop_pick_sequence(self.stage)
            if physics:
                physics.stop_rtg_animation(self.stage)
        panel = current_sts_panel(self.stage)
        if panel and panel.unload_task and (target in {"all", "rtg"} or not args.get("crane_id") or panel.unload_task.c.path.endswith("/" + args["crane_id"])):
            panel.unload_task.paused = True
            panel.unload_task.c.stop()
            panel.unload_task._agent_stop_cancelled_targets = True
        if target in {"sts", "all"}:
            for name, controller in sts_controllers(self.stage).items():
                if not args.get("crane_id") or name == args["crane_id"]:
                    controller.stop()
        iecs = iecs_runtime(self.stage)
        if iecs:
            iecs.process({"action": "pause"})
        return {"status": "stopped", "equipment": target, "crane_id": args.get("crane_id"), "usd_only": True}

    def _viewport(self):
        from omni.kit.viewport.utility import get_active_viewport
        viewport = get_active_viewport()
        if viewport is None:
            raise RuntimeError("No viewport is open. Open a Viewport window first.")
        return viewport

    def list_views(self):
        from pxr import UsdGeom
        cameras = [{"path": str(prim.GetPath()), "name": prim.GetName()} for prim in self.stage.Traverse() if prim.IsA(UsdGeom.Camera)]
        camera_path = str(self._viewport().camera_path)
        properties = runtime_catalog.SCHEMAS["set_viewport_view"]["properties"]
        return {"targets": properties["target"]["enum"], "views": properties["view"]["enum"], "cameras": cameras[:128],
                "camera_count": len(cameras), "active_camera": camera_path, "following": self.follow,
                "active_view": copy.deepcopy(self.active_view) if camera_path == CAMERA_PATH else None,
                "presets": {"under_trolley": {"target": "trolley", "view": "under_trolley", "follow": True,
                            "field_of_view_deg": UNDER_TROLLEY_FOV_DEG,
                            "description": "Below the trolley platform, vertically down onto the spreader/container tops; follows trolley travel, not hoist height."}}}

    def _target_bounds(self, args):
        from pxr import Gf, Usd, UsdGeom
        target = args["target"]
        path = {"port": "/World/PortAndRTG", "rtg": GANTRY, "trolley": TROLLEY, "spreader": HOIST}.get(target)
        if target == "sts":
            path = "/World/STSControls/" + args["crane_id"]
        if target == "task":
            panel = current_sts_panel(self.stage)
            if panel and panel.unload_task:
                path = GANTRY if panel.unload_task.delivering else panel.unload_task.c.path
            else:
                iecs = iecs_runtime(self.stage)
                if iecs and any(order["status"] != "completed" for order in iecs.engine.orders.values()):
                    orders = [order for order in iecs.engine.orders.values() if order.get("vehicle") and order["status"] == "running"]
                    path = iecs.vehicle_path(orders[0]["vehicle"]) if orders else "/World/ParkedYardTrucks"
                elif read_attr(self.stage.GetPrimAtPath("/World/LiveTwinState"), "rtg:taskId"):
                    path = GANTRY
                else:
                    raise ValueError("There is no current task to frame")
        if target == "bay":
            from .yard_queries import query_bay
            from yard_inventory_loader import row_centers_x
            result = query_bay(self.stage, self.project_root, args["bay_id"])
            paths = ["/World/LiveContainers/YardInventory/Bay_" + name.replace("/", "_") for name in result["logical_bay_ids"]]
            path = next((value for value in paths if self.stage.GetPrimAtPath(value)), None)
            if path is None:
                xs = row_centers_x()
                return Gf.Vec3d(sum(xs) / len(xs), result["configured_layout_position_m"]["scene_y_center"], 2.5), 7.0
        prim = self.stage.GetPrimAtPath(path) if path else None
        if not prim:
            raise ValueError(f"View target is not present in the stage: {path}")
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"], useExtentsHint=True).ComputeWorldBound(prim).ComputeAlignedBox()
        if bounds.IsEmpty():
            point = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
            return point, 5.0
        return bounds.GetMidpoint(), max(1.0, bounds.GetSize().GetLength() * 0.5)

    def _under_trolley_pose(self):
        from pxr import Gf, Usd, UsdGeom

        trolley = self.stage.GetPrimAtPath(TROLLEY)
        if not trolley:
            raise ValueError("The RTG trolley is not present in the current stage")
        transforms = UsdGeom.XformCache(Usd.TimeCode.Default())
        matrix = transforms.GetLocalToWorldTransform(trolley)
        eye = matrix.Transform(Gf.Vec3d(*UNDER_TROLLEY_EYE))
        hoist = self.stage.GetPrimAtPath(HOIST)
        contact_z = transforms.GetLocalToWorldTransform(hoist).Transform(Gf.Vec3d(2.55, -5.0, 2.55))[2] if hoist else eye[2] - 5.0
        distance = max(1.0, float(eye[2] - contact_z))
        center = eye - Gf.Vec3d(0, 0, distance)
        # Strip the imported RTG's non-uniform scale. Keep the long spreader
        # axis horizontal in the image to use the wide viewport efficiently.
        axis = matrix.TransformDir(Gf.Vec3d(1, 0, 0))
        up = Gf.Vec3d(float(axis[0]), float(axis[1]), 0)
        up = up.GetNormalized() if up.GetLength() > 1e-8 else Gf.Vec3d(1, 0, 0)
        return eye, center, up, distance

    @staticmethod
    def _viewport_aspect(viewport):
        resolution = getattr(viewport, "get_texture_resolution", None)
        if callable(resolution):
            try:
                width, height = resolution()
                if width > 0 and height > 0:
                    return float(width) / float(height)
            except (TypeError, ValueError, RuntimeError):
                pass
        return 16.0 / 9.0

    def _apply_view(self, args, viewport=None):
        from pxr import Gf, Sdf, Usd, UsdGeom
        viewport = viewport or self._viewport()
        under_trolley = args.get("view") == "under_trolley"
        if under_trolley:
            eye, center, up, distance = self._under_trolley_pose()
        else:
            center, radius = self._target_bounds(args)
            angles = {"angled": (135.0, 28.0), "front": (90.0, 8.0), "back": (-90.0, 8.0),
                      "left": (180.0, 8.0), "right": (0.0, 8.0), "top": (0.0, 90.0)}
            azimuth, elevation = angles[args.get("view", "angled")]
            azimuth = math.radians(args.get("azimuth_deg", azimuth))
            elevation = math.radians(args.get("elevation_deg", elevation))
            distance = args.get("distance_m", radius * 3.8)
            eye = center + Gf.Vec3d(math.cos(azimuth) * math.cos(elevation), math.sin(azimuth) * math.cos(elevation), math.sin(elevation)) * distance
            up = Gf.Vec3d(0, 1, 0) if abs(math.cos(elevation)) < 0.01 else Gf.Vec3d(0, 0, 1)
        aperture = 36.0 if under_trolley else 20.955
        fov = args.get("field_of_view_deg", UNDER_TROLLEY_FOV_DEG if under_trolley else math.degrees(2 * math.atan(aperture / 48.0)))
        focal_length = aperture / (2 * math.tan(math.radians(fov) / 2))
        vertical_aperture = aperture / self._viewport_aspect(viewport) if under_trolley or "field_of_view_deg" in args else 15.2908
        clipping_far = max(10000.0, distance * 10)
        lens_signature = (aperture, vertical_aperture, focal_length, clipping_far)
        if self.camera_layer is None:
            self.camera_layer = Sdf.Layer.CreateAnonymous("smartport_agent_camera.usda")
            self.stage.GetSessionLayer().subLayerPaths.insert(0, self.camera_layer.identifier)
            self.previous_camera = str(viewport.camera_path)
            self.camera_viewport = viewport
        with Usd.EditContext(self.stage, self.camera_layer):
            camera = UsdGeom.Camera.Define(self.stage, CAMERA_PATH)
            if lens_signature != self.camera_lens_signature:
                camera.CreateHorizontalApertureAttr(aperture)
                camera.CreateVerticalApertureAttr(vertical_aperture)
                camera.CreateFocalLengthAttr(focal_length)
                camera.CreateClippingRangeAttr(Gf.Vec2f(0.05, clipping_far))
                self.camera_lens_signature = lens_signature
            xf = UsdGeom.Xformable(camera.GetPrim())
            ops = xf.GetOrderedXformOps()
            op = ops[0] if ops else xf.AddTransformOp()
            pose = Gf.Matrix4d(1).SetLookAt(eye, center, up).GetInverse()
            if op.Get() != pose:
                op.Set(pose)
        if str(viewport.camera_path) != CAMERA_PATH:
            viewport.camera_path = CAMERA_PATH
        return {"camera_path": CAMERA_PATH, "target": args["target"], "view": args.get("view", "angled"),
                "eye_m": list(eye), "look_at_m": list(center), "distance_m": distance, "following": bool(args.get("follow")),
                "field_of_view_deg": fov, "mount_path": TROLLEY if under_trolley else None,
                "vertical_down": under_trolley}

    def set_view(self, args):
        result = self._apply_view(args)
        self.active_view = {**args, "view": result["view"], "field_of_view_deg": result["field_of_view_deg"]}
        self.follow = dict(args) if args.get("follow") else None
        self.follow_viewport = self._viewport() if self.follow else None
        return result

    def switch_camera(self, args):
        from pxr import UsdGeom
        viewport = self._viewport()
        if "camera_number" in args:
            import rtg_virtual_cameras as cameras
            records = cameras._existing_virtual_camera_records(self.stage)
            if not records:
                records = cameras.create_rtg_virtual_cameras(self.stage, align_labels=False, start_camera_popup=False)
            record = next((item for item in records if item.seq == args["camera_number"]), None)
            if record is None:
                raise ValueError("The requested RTG virtual camera is unavailable")
            path = record.camera_path
        else:
            path = args["camera_path"]
        camera = self.stage.GetPrimAtPath(path)
        if not camera or not camera.IsA(UsdGeom.Camera):
            raise ValueError(f"Not an existing USD camera: {path}")
        self.follow = None
        self.follow_viewport = None
        self.active_view = None
        viewport.camera_path = path
        return {"camera_path": path, "following": False}

    def execute(self, stage, name, arguments):
        self.bind(stage)
        args = agent_api.validate_tool_arguments(name, arguments)
        if name == "get_agent_capabilities":
            return {"interface_count": len(agent_api.TOOLS), "interfaces": [
                {"name": item["function"]["name"], "description": item["function"]["description"], "parameters": item["function"]["parameters"]}
                for item in agent_api.TOOLS], "scope": "Project runtime APIs. Asset builders/export scripts are development tools. RTX sensing requires Isaac RTX extensions; physics requires PhysX."}
        if name == "get_current_tasks":
            result = current_tasks(stage)
            result["agent_operations"] = [copy.deepcopy(item) for item in self.operations.values() if item["status"] in {"running", "cancelling"}]
            return result
        if name == "get_equipment_status": return self.equipment_status(args)
        if name == "move_rtg_to_bay": return self.rtg_move(args, bay=True)
        if name == "set_rtg_axis_positions": return self.rtg_move(args)
        if name == "set_sts_axis_positions": return self.move_sts(args)
        if name == "stop_equipment": return self.stop_equipment(args)
        if name == "list_viewpoints": return self.list_views()
        if name == "set_viewport_view": return self.set_view(args)
        if name == "switch_viewport_camera": return self.switch_camera(args)
        if name == "get_operation_status":
            if args.get("operation_id"):
                if args["operation_id"] not in self.operations: raise ValueError("Unknown operation_id")
                return copy.deepcopy(self.operations[args["operation_id"]])
            return {"operations": copy.deepcopy(list(self.operations.values()))}
        if name == "cancel_operation":
            identifier = args["operation_id"]
            if identifier not in self.operations: raise ValueError("Unknown operation_id")
            if self.motion and self.motion["id"] == identifier: self._cancel_motion()
            if identifier in self.jobs and not self.jobs[identifier].done():
                self.operations[identifier]["status"] = "cancelling"
                self.jobs[identifier].cancel()
            return dict(self.operations[identifier])
        from .project_interfaces import execute_project_interface
        return execute_project_interface(self, name, args)

    def release(self):
        self._cancel_motion()
        for task in self.jobs.values():
            if not task.done(): task.cancel()
        self.jobs.clear()
        panel = current_sts_panel(self.stage) if self.stage else None
        for controller in self.owned_sts.values():
            if panel is None or controller not in panel.controllers.values(): controller.close()
        self.owned_sts.clear()
        self.follow = None
        self.follow_viewport = None
        if self.camera_layer is not None and self.stage is not None:
            try:
                viewport = self.camera_viewport or self._viewport()
                if str(viewport.camera_path) == CAMERA_PATH and self.previous_camera:
                    viewport.camera_path = self.previous_camera
            except Exception:
                pass
            paths = self.stage.GetSessionLayer().subLayerPaths
            if self.camera_layer.identifier in paths: paths.remove(self.camera_layer.identifier)
        self.camera_layer = None
        self.previous_camera = None
        self.camera_viewport = None
        self.active_view = None
        self.camera_lens_signature = None

    def close(self):
        self.release()
        self.stage = None
