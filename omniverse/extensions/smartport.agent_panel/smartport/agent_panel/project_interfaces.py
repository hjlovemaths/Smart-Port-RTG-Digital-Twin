"""Explicit adapters for existing project runtime APIs; no arbitrary execution."""

from __future__ import annotations

import builtins
import importlib
from pathlib import Path

from .scene_runtime import current_sts_panel, current_tasks, existing_module, iecs_runtime, simulation_status, sts_controllers


HANDLED_TOOLS = {
    "set_rtg_spreader_size", "resume_sts_crane", "control_timeline", "control_sts_task", "submit_rtg_task",
    "control_rtg_simulation", "control_iecs", "control_live_twin", "control_sea_motion",
    "control_camera_display", "control_info_panel", "show_alarm", "control_gamepad", "rtg_slot_operation",
    "control_rtg_physics", "control_lidar", "capture_lidar", "reload_yard_inventory", "control_livestream",
}


def load(name):
    return existing_module(name) or importlib.import_module(name)


def require_idle(owner):
    if owner.motion:
        raise ValueError("An Agent RTG movement is running. Wait for arrival or stop it before starting a task.")
    owner._guard_manual("rtg")
    moving = [name for name, controller in sts_controllers(owner.stage).items()
              if any(abs(controller.targets[axis] - controller.state[axis]) > 0.001 for axis in controller.state)]
    if moving:
        raise ValueError("Wait for or stop the moving STS cranes first: " + ", ".join(moving))


def _sts_task(owner, args):
    action = args["action"]
    if action == "status":
        return current_tasks(owner.stage)["sts"]
    panel = current_sts_panel(owner.stage)
    if action.startswith("start_"):
        require_idle(owner)
        if panel is None or panel._sub is None:
            panel = load("sts_control").show_sts_controls()
        if panel.unload_task:
            raise ValueError("Reset the current STS task explicitly before starting another")
        if action == "start_unload": panel.start_unload()
        else: panel.start_double20_roundtrip()
        if panel.unload_task is None:
            raise RuntimeError(panel.task_status.text)
    elif panel is None or panel.unload_task is None:
        raise ValueError("No STS task is active")
    elif action == "pause":
        panel.unload_task.paused = True
    elif action == "resume":
        # A task's target must survive a pause. The panel normally skips ticks;
        # an equipment STOP, in contrast, deliberately cancels its axis target.
        if panel.unload_task.c.stopped or getattr(panel.unload_task, "_agent_stop_cancelled_targets", False):
            raise ValueError("The crane is stopped. Reset the task before restarting its cancelled motion.")
        panel.unload_task.paused = False
    elif action == "reset": panel.reset_unload()
    elif action == "focus": panel.focus_task()
    if action == "focus": owner.follow = None
    return current_tasks(owner.stage)["sts"]


def _simulation(owner, args):
    action = args["action"]
    module = existing_module("rtg_simulation_driver")
    if action == "status": return simulation_status(module)
    if action == "stop":
        if module: module.stop_simulation()
        return {"running": False}
    if action == "clear_task":
        if module: module.clear_simulated_task()
        return {"task_cleared": True}
    if action == "start":
        if not simulation_status(module)["running"]: require_idle(owner)
        load("rtg_simulation_driver").start_simulation()
        status = simulation_status(load("rtg_simulation_driver"))
        if not status["running"]: raise RuntimeError("The RTG simulator could not start. See the Kit log for its initialization error.")
        return status
    scenarios = load("rtg_simulation_scenarios")
    if action == "scenario_status": return scenarios.get_deterministic_scenario_status()
    if action == "stop_scenarios":
        scenarios.stop_deterministic_scenarios()
        return scenarios.get_deterministic_scenario_status()
    kwargs = {key: args[key] for key in ("task_count", "seed", "block", "bay_min", "bay_max") if key in args}
    if action == "preview_scenarios": return scenarios.preview_deterministic_scenarios(**kwargs)
    if action == "start_scenarios":
        require_idle(owner)
        if "inventory_seed" in args: kwargs["inventory_seed"] = args["inventory_seed"]
        return scenarios.start_deterministic_scenarios(reset_inventory=args.get("reset_inventory", False), **kwargs)
    if action == "continue_scenarios": return scenarios.continue_deterministic_scenarios(**kwargs)
    raise ValueError("Unsupported simulation action")


def _iecs(owner, args):
    action = args["action"]
    runtime = iecs_runtime(owner.stage)
    if action == "status": return current_tasks(owner.stage)["iecs"]
    if action == "start":
        if runtime is None:
            require_idle(owner)
            runtime = load("iecs_launch").launch(stage=owner.stage)
    elif runtime is None:
        raise ValueError("IECS is not running. Start it with control_iecs(action='start').")
    elif action == "stop":
        runtime.close()
        return {"running": False}
    elif action == "focus":
        owner.follow = None
        runtime.focus()
    else:
        if action in {"submit", "mixed"} and owner.motion:
            raise ValueError("Wait for or stop the Agent RTG movement before scheduling IECS work")
        command = {"action": action}
        if "flow" in args: command["flow"] = args["flow"]
        if action == "speed": command["value"] = args["speed"]
        runtime.process(command)
        runtime.publish()
        if action == "check_scene": return runtime.scene_report
        if action == "inventory_preview": return runtime.inventory_preview()
    return current_tasks(owner.stage)["iecs"]


def _sea(owner, args):
    action = args["action"]
    motion = getattr(builtins, "_smart_port_sea_motion", None)
    if motion is not None and (motion.closed or motion.stage != owner.stage): motion = None
    if action == "stop":
        if motion: load("ship_sea_motion").stop_sea_motion()
        return {"running": False}
    if action == "start":
        if motion is None: motion = load("ship_sea_motion").start_sea_motion(owner.stage)
    elif action != "status" and motion is None:
        raise ValueError("Sea motion is not running; start it first")
    if motion:
        if action == "pause": motion.enabled = False
        elif action == "resume": motion.enabled = True
        elif action == "strength": motion.strength = args["strength"]
        elif action == "quality": motion.set_quality(args["smooth"])
        elif action in {"hold", "release"}:
            if args["ship_path"] not in motion.ships: raise ValueError("Not a registered moored ship")
            getattr(motion, action)(args["ship_path"])
    return {"running": motion is not None, "enabled": motion.enabled if motion else False,
            "strength": motion.strength if motion else None, "ships": list(motion.ships) if motion else [],
            "held_ships": sorted(motion.held) if motion else []}


def _camera_display(owner, args):
    display, action = args["display"], args["action"]
    if display == "virtual_cameras":
        module = load("rtg_virtual_cameras")
        if action in {"start", "refresh"}: module.create_rtg_virtual_cameras(owner.stage, align_labels=False, start_camera_popup=False)
        if action == "stop":
            # Restore a normal camera before deleting the selected virtual one.
            viewport = owner._viewport()
            if str(viewport.camera_path).startswith(module.VIRTUAL_CAMERA_ROOT_PATH):
                owner.set_view({"target": "rtg", "view": "angled"})
            module.cleanup_rtg_virtual_cameras(owner.stage)
        return {"display": display, "cameras": [{"number": item.seq, "camera_path": item.camera_path} for item in module._existing_virtual_camera_records(owner.stage)]}
    configurations = {
        "rtsp_popup": ("rtg_camera_popup", "start_rtg_camera_popup", "stop_rtg_camera_popup", "_service"),
        "spreader_rtsp_grid": ("rtg_camera_popup", "start_rtg_spreader_camera_grid", "stop_rtg_spreader_camera_grid", "_spreader_grid_service"),
        "model_grid": ("rtg_model_camera_grid", "start_rtg_model_camera_grid", "stop_rtg_model_camera_grid", "_service"),
    }
    name, start, stop, attr = configurations[display]
    module = existing_module(name)
    if action in {"start", "refresh"}:
        module = load(name)
        if display == "model_grid" and action == "refresh": module.refresh_rtg_model_camera_grid(owner.stage)
        elif display == "model_grid": getattr(module, start)(owner.stage)
        else: getattr(module, start)()
    elif action == "stop" and module: getattr(module, stop)()
    return {"display": display, "running": bool(module and getattr(module, attr, None))}


def _panels(args):
    panel, action = args["panel"], args["action"]
    name = {"device": "rtg_device_info_popup", "container": "rtg_container_info_popup", "alarm": "rtg_alarm_popup"}[panel]
    module = existing_module(name)
    if action == "start": getattr(load(name), "start_" + name)()
    elif action == "stop" and module: getattr(module, "stop_" + name)()
    service = getattr(builtins, {"device": "_rtg_device_info_popup_service", "container": "_rtg_container_info_popup_service", "alarm": "_rtg_alarm_popup_service"}[panel], None)
    return {"panel": panel, "running": service is not None}


def _slot(owner, args):
    action = args["action"]
    module = load("rtg_pick_place_controller")
    if action == "calibration_status": return module.calibration_status()
    if action == "reset_calibration":
        module.reset_calibration()
        return module.calibration_status()
    if action == "stop":
        sequence = module.stop_pick_sequence(owner.stage)
        physics = existing_module("rtg_isaac_physics")
        animation = physics.stop_rtg_animation(owner.stage) if physics else None
        return {"status": "stopped", "sequence": sequence, "animation": animation}
    if action == "stop_follow":
        module.stop_container_follow(owner.stage)
        return {"status": "container follow stopped"}
    if action == "detach":
        module.detach_container(owner.stage)
        return {"status": "detached"}
    if action == "scene_rows": return module.scene_rows_from_right(args["bay_id"], owner.stage, reverse_world_x=args.get("reverse_world_x", False))
    if action not in {"target", "alignment", "scene_target"}:
        if owner.motion: raise ValueError("Wait for or stop the current Agent RTG motion first")
        owner._guard_manual("rtg")
    from .yard_queries import read_inventory_snapshot
    _inventory, source = read_inventory_snapshot(owner.stage, owner.project_root)
    if source["kind"] == "active_driver_inventory":
        raise ValueError("Stop the active TwinState/simulation driver before using file-backed slot control")
    kwargs = {key: args[key] for key in ("bay_id", "row", "tier") if key in args}
    kwargs["inventory_path"] = Path(source["inventory_file"])
    if action in {"scene_target", "move_scene_row"}:
        scene_kwargs = {"bay_id": args["bay_id"], "visual_row_from_right": args["visual_row_from_right"],
                        "stage": owner.stage, "inventory_path": kwargs["inventory_path"], "reverse_world_x": args.get("reverse_world_x", False)}
        if action == "scene_target":
            return module.scene_slot_target_from_right(**scene_kwargs)
        return module.move_to_scene_row_from_right(**scene_kwargs, high=args.get("high", True), duration_s=args.get("duration_s", 3.0))
    if action == "target":
        from dataclasses import asdict
        return asdict(module.slot_target(**kwargs))
    kwargs["stage"] = owner.stage
    if action == "alignment": return module.debug_slot_alignment(**kwargs)
    if action == "calibrate": return module.calibrate_current_slot(**kwargs, apply_z=args.get("apply_z", False))
    if action == "attach":
        result = module.attach_slot_container(**kwargs)
        return {"status": "attached", "result": result}
    if action == "pick_demo":
        result = module.pick_slot_demo(**kwargs, travel_duration_s=args.get("duration_s", 4.0))
        return {"status": "started", "result": result}
    functions = {"move": module.move_to_slot, "lower": module.lower_to_slot, "lift": module.lift_from_slot}
    if action == "move": kwargs["high"] = args.get("high", True)
    return functions[action](**kwargs, duration_s=args.get("duration_s", 3.0))


def _physics(owner, args):
    module = load("rtg_isaac_physics")
    action = args["action"]
    if action == "status": return {"status": module.rtg_status(owner.stage), "targets": module.get_rtg_physics_targets(owner.stage)}
    if action == "diagnose": return module.diagnose_rtg_physics(owner.stage)
    if action == "validate": return module.validate_rtg_physics_stage(owner.stage)
    if action in {"activate_visual", "activate_physics", "set_targets", "freeze"}: owner._guard_manual("rtg")
    if action == "set_targets":
        import rtg_live_control as live
        limits = {"gantry_m": live.GANTRY_SOFT_LIMITS, "trolley_m": live.TROLLEY_SOFT_LIMITS, "hoist_m": live.HOIST_SOFT_LIMITS}
        targets = {key: value for key, value in args.items() if key in limits}
        if not targets: raise ValueError("Specify at least one RTG target axis")
        for key, value in targets.items():
            if not limits[key][0] <= value <= limits[key][1]: raise ValueError(f"{key} is outside the RTG soft limits")
        current = dict(zip(("gantry_m", "trolley_m", "hoist_m"), live.RTGController(owner.stage).get_positions()))
        current.update(targets)
        return module.set_rtg_physics_targets(stage=owner.stage, **current)
    functions = {"activate_visual": module.activate_rtg_visual_control, "activate_physics": module.activate_rtg_physics,
                 "deactivate": module.deactivate_rtg_physics, "start_sync": module.start_rtg_physics_visual_sync,
                 "stop_sync": module.stop_rtg_physics_visual_sync, "freeze": module.freeze_rtg_animations}
    result = functions[action](stage=owner.stage)
    return result if isinstance(result, dict) else {"action": action, "completed": True}


def _lidar(owner, args):
    action, mode = args["action"], args.get("mode", "yard")
    if action in {"markers_on", "markers_off"}:
        module = load("rtg_lidar_position_markers")
        if action == "markers_on": module.show_rtg_lidar_position_markers(owner.stage)
        else: module.remove_rtg_lidar_position_markers(owner.stage)
        return {"markers_visible": action == "markers_on"}
    module = load("rtg_yard_lidar")
    fleet = load("rtg_lidar_fleet")
    if action == "status": return {"yard": module.get_rtg_yard_lidar_status(), "fleet": fleet.get_rtg_lidar_fleet_status()}
    if action == "diagnose": return module.diagnose_rtg_yard_lidar()
    if action == "stop":
        if mode in {"yard", "all"}: module.stop_rtg_yard_lidar()
        if mode != "yard": fleet.stop_rtg_lidar_fleet()
        return {"status": "stopped", "mode": mode}
    if action == "start":
        kwargs = {key: args[key] for key in ("config", "far_range_m") if key in args}
        kwargs["output_dir"] = owner.project_root / "outputs/rtg_pointcloud"
        if mode == "yard": return module.start_rtg_yard_lidar(owner.stage, **kwargs)
        if "variant" in args: kwargs["variant"] = args["variant"]
        functions = {"representative": fleet.start_rtg_lidar_representative_fleet, "trolley_bottom": fleet.start_rtg_lidar_trolley_bottom_fleet,
                     "spreader": fleet.start_rtg_lidar_spreader_fleet, "all": fleet.start_rtg_lidar_all_fleet}
        return functions[mode](owner.stage, **kwargs)
    if action in {"probe_on", "probe_off"}:
        if action == "probe_on": module.create_rtg_lidar_probe()
        else: module.remove_rtg_lidar_probe()
        return {"probe_visible": action == "probe_on"}
    if action in {"save_map", "save_scan"}:
        return getattr(module, "save_rtg_yard_map" if action == "save_map" else "save_rtg_yard_sensor_scan")(output_stem=args.get("output_stem", "agent_lidar"))
    kwargs = {"display_range_m": args.get("display_range_m", 20.0)}
    if action == "scan_volumes": return fleet.show_rtg_lidar_scan_volumes(**kwargs)
    if action == "debug_draw": return fleet.show_rtg_lidar_position_scan_debug_draw(stage=owner.stage, **kwargs)
    if action == "wireframes": return fleet.show_rtg_lidar_position_scan_wireframes(stage=owner.stage, **kwargs)
    raise ValueError("Unsupported LiDAR action")


def execute_project_interface(owner, name, args):
    if name not in HANDLED_TOOLS: raise ValueError(f"Unsupported runtime interface: {name}")
    stage, action = owner.stage, args.get("action")
    if name == "set_rtg_spreader_size":
        owner._guard_manual("rtg")
        from rtg_live_control import RTGController
        result = RTGController(stage).set_spreader_size(args["size_ft"], smooth=False)
        if result is None: raise ValueError("RTG telescopic spreader controls are unavailable in this stage")
        return {"requested_size_ft": args["size_ft"], "actual_size_ft": result}
    if name == "resume_sts_crane":
        controller = sts_controllers(stage).get(args["crane_id"])
        if controller is None: raise ValueError("This crane has no active controller")
        controller.resume()
        return {"crane_id": args["crane_id"], "stopped": controller.stopped, "message": "Stop released; issue a new target to move."}
    if name == "control_timeline":
        import omni.timeline
        timeline = omni.timeline.get_timeline_interface()
        if action != "status": getattr(timeline, action)()
        return {"playing": timeline.is_playing(), "time_seconds": timeline.get_current_time()}
    if name == "control_sts_task": return _sts_task(owner, args)
    if name == "control_rtg_simulation": return _simulation(owner, args)
    if name == "submit_rtg_task":
        module = existing_module("rtg_simulation_driver")
        if not simulation_status(module)["running"]:
            require_idle(owner)
        elif owner.motion:
            raise ValueError("Stop the Agent RTG movement before submitting simulator work")
        result = load("rtg_simulation_driver").publish_simulated_task(**args)
        status = simulation_status(load("rtg_simulation_driver"))
        if not status["running"]: raise RuntimeError("The RTG simulator did not start; the task is not executing. See the Kit log.")
        return {"submitted": True, "result": result, "simulation": status}
    if name == "control_iecs": return _iecs(owner, args)
    if name == "control_live_twin":
        driver = getattr(builtins, "_smart_port_rtg_panel_facing_driver", None)
        if action == "start":
            require_idle(owner)
            load("rtg_twin_state_receiver").start_live_driver()
        elif action == "stop":
            stop = getattr(builtins, "_smart_port_rtg_twin_stop_live_driver", None)
            if callable(stop): stop()
        driver = getattr(builtins, "_smart_port_rtg_panel_facing_driver", None)
        return {"running": bool(driver and driver._subscription is not None), "tasks": current_tasks(stage)["rtg"]}
    if name == "control_sea_motion": return _sea(owner, args)
    if name == "control_camera_display": return _camera_display(owner, args)
    if name == "control_info_panel": return _panels(args)
    if name == "show_alarm":
        module = load("rtg_alarm_popup")
        if action == "show": module.show_rtg_alarm(args["message"], severity=args.get("severity", "warning"), duration_seconds=args.get("duration_seconds", 0))
        elif action == "clear": module.clear_rtg_alarm()
        payload_key = module.ALARM_MANUAL_PAYLOAD_KEY
        return {"manual_alarm": getattr(builtins, payload_key, None), "source": "local simulated alarm"}
    if name == "control_gamepad":
        module = load("rtg_gamepad_manual_control")
        if action == "start":
            if owner.motion: raise ValueError("Stop Agent motion before starting gamepad mode")
            module.start_gamepad_manual_mode(args.get("controller_index", 0))
        elif action == "stop": module.stop_gamepad_manual_mode()
        elif action == "position_for_pick": return module.position_gamepad_manual_for_pick(**{key: args[key] for key in ("block", "bay", "row", "tier")})
        return module.get_gamepad_manual_status()
    if name == "rtg_slot_operation": return _slot(owner, args)
    if name == "control_rtg_physics": return _physics(owner, args)
    if name == "control_lidar": return _lidar(owner, args)
    if name == "capture_lidar":
        mode = args["mode"]
        kwargs = {key: args[key] for key in ("output_stem", "output_frame", "timeout_frames", "warmup_frames", "min_scans") if key in args}
        kwargs.setdefault("output_stem", "agent_lidar_capture")
        if mode == "yard": coroutine = load("rtg_yard_lidar").capture_rtg_yard_pointcloud(**kwargs)
        elif mode == "fleet": coroutine = load("rtg_lidar_fleet").capture_rtg_lidar_fleet_pointcloud(**kwargs, serial=args.get("serial", True))
        else:
            kwargs.pop("output_frame", None)
            kwargs["output_dir"] = owner.project_root / "outputs/rtg_pointcloud"
            fleet = load("rtg_lidar_fleet")
            if mode == "positions":
                if owner.motion: raise ValueError("Wait for or stop Agent RTG motion before a multi-position capture")
                owner._guard_manual("rtg")
                coroutine = fleet.capture_rtg_lidar_fleet_at_trolley_positions(stage=stage, primary_bay_id=args.get("bay_id"), **kwargs)
            else: coroutine = fleet.capture_rtg_lidar_fleet_in_batches(stage=stage, **kwargs)
        return owner.job("lidar_" + mode, coroutine)
    if name == "reload_yard_inventory":
        module = existing_module("rtg_twin_state_receiver")
        if module is None or getattr(module, "_driver", None) is None:
            raise ValueError("Start the live receiver or RTG simulation before reloading its selected inventory")
        return module.reload_yard_inventory()
    if name == "control_livestream":
        module = existing_module("rtg_viewport_only_livestream")
        if action == "status":
            import carb
            return {"module_loaded": module is not None, "hide_ui": carb.settings.get_settings().get("/app/window/hideUi")}
        module = load("rtg_viewport_only_livestream")
        if action == "restore_ui": module.restore_kit_ui()
        elif action == "enable_interaction": module.enable_viewport_interaction()
        else:
            function = module.start_viewport_only_livestream_2k if args.get("resolution") == "2k" else module.start_viewport_only_livestream_1080p
            return {"viewer_url": function(hide_kit_ui=False, target_fps=args.get("target_fps", 30)),
                    "actual_resolution": "1920x1080", "note": "The project's legacy 2k alias currently uses its 1080p preset."}
        return {"action": action, "completed": True}
    raise ValueError(f"Unimplemented runtime interface: {name}")
