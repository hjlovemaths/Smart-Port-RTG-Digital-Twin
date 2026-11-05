"""Public runtime tool catalog. Each action maps to a fixed project API."""

from __future__ import annotations


def enum(*values):
    return {"type": "string", "enum": list(values)}


def number(lo=None, hi=None, integer=False):
    result = {"type": "integer" if integer else "number"}
    if lo is not None:
        result["minimum"] = lo
    if hi is not None:
        result["maximum"] = hi
    return result


TEXT = {"type": "string", "maxLength": 256}
BOOL = {"type": "boolean"}
BAY = {"type": "string", "description": "For example 4C41 or 4C/041"}
CRANE = {"type": "string", "description": "STS_01 through STS_07; specify the requested crane"}


def tool(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": list(required), "additionalProperties": False},
    }}


TOOLS = (
    tool("preview_yard_to_ship_transfer", "Read-only plan for a reversible RTG -> truck -> STS loading demo. Resolve actual SOURCE box size, never guess it from yard bay parity; returns size_ft and spreader_adjustment (current size, target, required). Specify source yard bay/row and optional tier (defaults to highest visible box). Single-box loading requires an exact six-digit ship target_location, e.g. 011090. For ONE truck carrying TWO boxes, set plan_type=cumulative_double20 and provide second_source_bay/second_source_row (optional second_source_tier); destination arguments depend on delivery_mode. Planner orders sources upstream first along the work lane (-Y), preserving each source/destination pairing. Truck drives forward to each source bay: load FRONT slot first, then drive to the second bay and load REAR slot. RTG gantry must stay fixed while gripping a box; only trolley and hoist move during each pickup/placement. Empty RTG may travel between bays after unlocking/raising. No reversing in yard, including same-bay slot alignment; parking-bay reverse entry after return remains allowed. Both boxes and spreader clearance are required before leaving for STS. Preview returns loading_order and both yard_loading_stops_m. Same yard block, two 20ft sources only; the second may be below the first in the same stack. For delivery_mode=quay, require crane_id and omit BOTH ship target locations: the truck arrives with both boxes and waits for handoff, without STS motion or empty return. delivery_mode=ship (default) requires both ship target locations and STS unloads each to its user-specified ship target. Never split this request into two single-box trips or invent a source/target. Rejects buried source/target, absent 6.6 deck cells, unsupported landing, wrong size, uneven 40ft supports and unreachable STS. 20ft uses odd ship bays; 40ft uses an even bay spanning two returned odd bays. Target structural placeholders are replaced only in the demo layer, not verified vacant actual cargo slots. Returns plan_id before start.",
      {"source_bay":BAY,"source_row":number(1,6,True),"source_tier":number(1,6,True),"target_location":{"type":"string","pattern":"^[0-9]{6}$"},"crane_id":CRANE,"plan_type":enum("single","cumulative_double20"),"delivery_mode":enum("ship","quay"),"second_source_bay":BAY,"second_source_row":number(1,6,True),"second_source_tier":number(1,6,True),"second_target_location":{"type":"string","pattern":"^[0-9]{6}$"}},("source_bay","source_row")),
    tool("start_yard_to_ship_transfer", "Start a previewed yard-to-vessel loading demo by plan_id after scene-operation approval. FIRST read current RTG spreader and continuously telescope to the source box size; no descent or locking until actual geometry confirms ready. For a cumulative_double20 preview, visit the planned source bays forward, filling BOTH truck slots before leaving for STS; honor delivery_mode=quay by stopping loaded at the specified STS for handoff. Otherwise continue both individual ship deliveries. Then parked truck drives to yard; RTG locks/lifts source onto trailer; truck follows roads and causeway to selected STS; STS locks/lifts/transfers/unlocks at specified ship location. 20ft boxes load near the cab; 40ft stay centred. After unloading the empty truck returns and reverses into its original parking bay. Cinematic viewing is enabled by default unless cinematic=false. Full original box parts move continuously without rescaling. No inventory-file or PLC writes. Returns immediately; status includes rtg_spreader_size_ft, required_size_ft and spreader_ready. Poll control_yard_to_ship_transfer once and report started if still running.",
      {"plan_id":TEXT,"cinematic":BOOL},("plan_id",)),
    tool("control_yard_to_ship_transfer", "Status, pause/resume or reset the full reverse RTG/truck/STS loading demo. Reset restores original yard box parts, target ship placeholders, truck and equipment poses. Finished tasks reserve resources until reset. Resume/reset require approval. Mutually exclusive with ship-to-yard and other equipment tasks.",
      {"action":enum("status","pause","resume","reset")},("action",)),
    tool("preview_ship_to_yard_transfer", "Read-only plan for a reversible ship-to-yard kinematic DEMO: select the TOP returned deck slot in a ship bay and side row (port=left facing bow; row_from_side=1 is the outermost port/starboard row, not rowNo01), then place on top of a rendered yard stack. Resolves 40ft support covering odd bays, checks stack height and neighbors, chooses a reachable STS and a parked truck, returns plan_id. These vessel structure slots are NOT verified actual cargo. For twin-20 set double20=true and provide BOTH user-specified target_bay/target_row and second_target_bay/second_target_row. Two adjacent odd ship bays must have same-row, same-height accessible 20ft boxes. STS grips both, RTG uses 20ft to unload each to its specified supported stack; never invent either destination. Use this before starting the complete STS/truck/RTG demonstration; do not chain the old fixed-box demos.",
      {"source_bay":number(1,999,True),"source_side":enum("port","starboard"),"row_from_side":number(1,15,True),"target_bay":BAY,"target_row":number(1,6,True),"double20":BOOL,"second_target_bay":BAY,"second_target_row":number(1,6,True)},("source_bay","source_side","row_from_side","target_bay","target_row")),
    tool("start_ship_to_yard_transfer", "Start a previously previewed ship-to-yard visual demonstration using plan_id. Parked truck drives to STS, STS lowers/locks/lifts/transfers onto trailer, truck follows roads/causeway to yard, primary RTG locks/lifts/stacks/unlocks. 20ft boxes load near the cab; 40ft stay centred. After delivery the truck returns and reverses into its original parking bay. No teleport during carrying, PLC writes or inventory-file changes. Returns immediately; poll control_ship_to_yard_transfer status. Compact ship models normally need no normalization; permit task-only scale normalization only when explicitly authorized. Starting requires scene-operation approval.",
      {"plan_id":TEXT,"allow_display_normalization":BOOL,"cinematic":BOOL},("plan_id",)),
    tool("control_transfer_camera", "Control event-driven cinematic viewing for either ship-to-yard or yard-to-ship transfers: status/start/pause/resume/stop. Both starts enable it by default unless cinematic=false. Direction-specific shots follow actual pickup, truck handoff, roads/causeway and destination landing. Each entire RTG operation uses one stable work-cell camera; twin-20 unloading keeps that camera across both boxes. Phase cues update without extra cuts. Camera controls never change task execution. Manual viewing pauses the director; resume explicitly to continue. The final landing overview stays while the empty truck returns to parking; after task completion hold 3 seconds and restore the previous camera.",
      {"action":enum("status","start","pause","resume","stop")},("action",)),
    tool("control_ship_to_yard_transfer", "Read status, pause/resume or reset the complete ship/truck/RTG visual task. Reset restores source boxes, parked truck, crane poses and source display, without editing saved assets. A completed demonstration reserves its session poses until reset; reset before starting another demo. Resume/reset require approval.",
      {"action":enum("status","pause","resume","reset")},("action",)),
    tool("get_vessel_structure", "Query ship bay/row/tier/valid slots using the current 6.6 TXT snapshot. Use for vessel structure, deck/hold row counts or a location such as 010404. H=hold, D=deck. These are structural slots, not actual onboard cargo. Missing bays/locations are not proof of empty cargo. Raw size codes C/D/1 are undefined; bay parity gives nominal 20/40ft. include_slots enables paginated locations.",
         {"bay":number(1,999,True), "zone":enum("H","D"), "location":{"type":"string","pattern":"^[0-9]{6,7}$"},
          "include_slots":BOOL,"offset":number(0,100000,True),"limit":number(1,200,True)}),
    tool("get_agent_capabilities", "List all connected runtime interfaces, allowed actions, parameters and runtime requirements."),
    tool("get_equipment_status", "Read RTG/STS positions, motion targets, limits and stop state without moving equipment. RTG work_mode reports TwinState workMode: 1 automatic, 2 assisted, 3 remote manual, 4 local, 5 maintenance. Check is_current/state/source; stopped/stale snapshots are not the current real device mode. Local gamepad/control ownership is separate.",
         {"equipment": enum("all", "rtg", "sts"), "crane_id": CRANE}),
    tool("get_current_tasks", "Read current RTG, STS and IECS tasks, phases, IDs, sources, destinations, queues and snapshot time. Includes RTG work_mode even without a current task; check its freshness and source before reporting the current mode. Never starts a task."),
    tool("list_viewpoints", "List view presets and existing USD cameras, including camera paths for switch_viewport_camera."),
    tool("set_viewport_view", "Look at a port overview, RTG, trolley, spreader, bay, STS crane or current task. For looking straight down at the spreader and container tops from BELOW the trolley platform, use target=trolley and view=under_trolley: a wide 110-degree camera that follows the trolley by default. This differs from the external top view above the RTG. Increase field_of_view_deg to widen the view without moving the camera. Other views support custom angles, distance and following a moving target. Changes only the viewport.",
         {"target": enum("port", "rtg", "trolley", "spreader", "bay", "sts", "task"),
          "view": enum("angled", "front", "back", "left", "right", "top", "under_trolley"),
          "bay_id": BAY, "crane_id": CRANE, "distance_m": number(1, 3000),
          "azimuth_deg": number(-360, 360), "elevation_deg": number(-85, 90),
          "field_of_view_deg": number(30, 140), "follow": BOOL}, ("target",)),
    tool("switch_viewport_camera", "Switch the active viewport to an existing USD camera path, or RTG virtual camera C01-C33 by number. Stops Agent camera following.",
         {"camera_path": TEXT, "camera_number": number(1, 33, True)}),
    tool("set_sts_axis_positions", "Move a specified STS shore crane continuously using the existing speed limits and boom interlock. Query get_equipment_status for per-crane limits. Coordinates are local scene control metres; boom is degrees.",
         {"crane_id": CRANE, "gantry_m": number(), "trolley_m": number(), "hoist_m": number(), "boom_deg": number(0, 80), "park_for_luffing": BOOL}, ("crane_id",)),
    tool("set_rtg_spreader_size", "Set the primary RTG telescopic spreader to 20 or 40 ft using the existing controller.",
         {"size_ft": {"type": "integer", "enum": [20, 40]}}, ("size_ft",)),
    tool("stop_equipment", "Stop Agent RTG movement or active STS controllers. Also stops shared STS tasks to prevent their next tick restarting motion. Does not send real equipment commands.",
         {"equipment": enum("rtg", "sts", "all"), "crane_id": CRANE}, ("equipment",)),
    tool("resume_sts_crane", "Release a specified STS crane's local stop latch. Its cancelled target is not restarted; issue a new movement command afterwards.", {"crane_id": CRANE}, ("crane_id",)),
    tool("control_timeline", "Read, play, pause or stop the Kit animation timeline.", {"action": enum("status", "play", "pause", "stop")}, ("action",)),
    tool("control_sts_task", "Start the legacy single-container unload or fixed double-20 round-trip demonstration; inspect, pause, resume, reset or focus its STS task. On the current 6.6 ship structure stage use preview_ship_to_yard_transfer(double20=true), start_ship_to_yard_transfer and control_ship_to_yard_transfer for the full twin-20 truck/RTG demonstration instead.",
         {"action": enum("status", "start_unload", "start_double20", "pause", "resume", "reset", "focus")}, ("action",)),
    tool("submit_rtg_task", "Submit a LOAD, UNLOAD or SWITCH task to the existing PLC-free RTG simulator. Only simulation inventory is used. Position format is e.g. 3C55062 (bay 55, row 6, tier 2). Does not dispatch production tasks.",
         {"work_type": enum("LOAD", "UNLOAD", "SWITCH"), "start_pos": TEXT, "end_pos": TEXT,
          "bay_map": {"type": "string", "pattern": "^[0-6]{6}$"}, "container_size": {"type": "integer", "enum": [20, 40]},
          "container_height": number(2.2, 3.2), "container_type": TEXT, "task_id": TEXT, "container_no": TEXT,
          "vision_required": BOOL, "wait_for_previous": BOOL}, ("work_type",)),
    tool("control_rtg_simulation", "Control the RTG simulator and deterministic scenario queue. Preview/status do not start it. Scenario reset_inventory defaults to false and affects simulation stock only.",
         {"action": enum("status", "start", "stop", "clear_task", "preview_scenarios", "start_scenarios", "continue_scenarios", "stop_scenarios", "scenario_status"),
          "task_count": number(1, 30, True), "seed": number(0, 2147483647, True),
          "block": enum("1C", "2C", "3C", "4C"), "reset_inventory": BOOL,
          "bay_min": number(1, 91, True), "bay_max": number(1, 91, True),
          "inventory_seed": number(0, 2147483647, True)}, ("action",)),
    tool("control_iecs", "Use the existing integrated equipment scheduling simulator: unload/load/gate-in/gate-out/yard transfer, mixed workload, pause/resume/reset, speed, scene validation, inventory preview and task focus. Uses reversible demo layers.",
         {"action": enum("status", "start", "submit", "mixed", "pause", "resume", "reset", "speed", "check_scene", "inventory_preview", "focus", "stop"),
          "flow": enum("unload", "load", "gate_in", "gate_out", "transfer"), "speed": {"type": "integer", "enum": [1, 5, 10, 20]}}, ("action",)),
    tool("control_live_twin", "Read status or start/stop the existing local TwinState receiver. Receives PLC/RCMS data to visualize the twin; never writes PLC commands. Stop simulation before starting a live receiver.",
         {"action": enum("status", "start", "stop")}, ("action",)),
    tool("control_plc_zmq", "Manage the local RTG ZMQ PLC simulator. start reuses a compatible existing service or launches the fixed service (command SUB 5515, state PUB 5516), then connects Kit. connect attaches only to an existing service. After start/connect completes, physics_on enables ZMQ cargo collision, landing/twistlock interlocks, gravity and stacking through sim_physics=1; it explicitly claims local control without velocity commands. Keep the timeline paused and all axes stopped. physics_off requires landing/unlocking cargo. Use physics_on for ZMQ picking/collision requests, not control_rtg_physics.activate_physics. status reports service, scene_ready, armed, physics_enabled and carried_container. stop/disconnect clear the adapter; stop only terminates an Agent-owned service. start/connect alone never arm control or stop competing drivers. Async actions return operation_id; poll completion. Never sends real PLC commands.",
         {"action": enum("status", "start", "stop", "connect", "disconnect", "physics_on", "physics_off")}, ("action",)),
    tool("control_sea_motion", "Read/start/stop/pause/resume sea and ship movement; set wave strength or quality; hold/release an existing moored ship.",
         {"action": enum("status", "start", "stop", "pause", "resume", "strength", "quality", "hold", "release"),
          "strength": number(0, 3), "smooth": BOOL, "ship_path": TEXT}, ("action",)),
    tool("control_camera_display", "Start/stop camera displays. work_views shows three simultaneous docked views: auto-zoom under-trolley top-down, side-follow spreader/container, and an angled whole-RTG overview that follows gantry travel. Use this for multiple RTG views in one panel. Also supports RTSP popup/grid, four-corner model grid, and virtual cameras. RTSP uses existing project configuration.",
         {"display": enum("rtsp_popup", "spreader_rtsp_grid", "model_grid", "virtual_cameras", "work_views"), "action": enum("status", "start", "stop", "refresh")}, ("display", "action")),
    tool("control_info_panel", "Start/stop the existing device, container or alarm information overlay services.",
         {"panel": enum("device", "container", "alarm"), "action": enum("status", "start", "stop")}, ("panel", "action")),
    tool("show_alarm", "Read, display or clear a local simulated RTG alarm. Does not acknowledge real equipment alarms.",
         {"action": enum("status", "show", "clear"), "message": TEXT, "severity": enum("info", "warning", "error"), "duration_seconds": number(0, 3600)}, ("action",)),
    tool("control_gamepad", "Read/start/stop existing local gamepad manual mode or position it for a named pickup slot. Does not operate real equipment.",
         {"action": enum("status", "start", "stop", "position_for_pick"), "controller_index": number(0, 3, True),
          "block": enum("1C", "2C", "3C", "4C"), "bay": number(1, 91, True), "row": number(1, 6, True), "tier": number(1, 6, True)}, ("action",)),
    tool("rtg_slot_operation", "Use existing inventory-backed RTG slot picking APIs: read target/alignment/calibration, move high, lower, lift, attach, detach, pick demo, calibrate, reset calibration or stop. Query bay inventory first to resolve the logical bay. Stage-only visualization.",
         {"action": enum("target", "alignment", "calibration_status", "move", "lower", "lift", "attach", "detach", "pick_demo", "calibrate", "reset_calibration", "stop", "scene_rows", "scene_target", "move_scene_row", "stop_follow"),
          "bay_id": BAY, "row": number(1, 6, True), "tier": number(1, 6, True), "duration_s": number(0.2, 120), "high": BOOL, "apply_z": BOOL,
          "visual_row_from_right": number(1, 6, True), "reverse_world_x": BOOL}, ("action",)),
    tool("control_rtg_physics", "Inspect/validate or enable/disable the existing RTG physics/visual controller and visual sync. Physics requires Isaac/PhysX. Target commands enforce the RTG soft limits.",
         {"action": enum("status", "diagnose", "validate", "activate_visual", "activate_physics", "deactivate", "set_targets", "start_sync", "stop_sync", "freeze"),
          "gantry_m": number(), "trolley_m": number(), "hoist_m": number()}, ("action",)),
    tool("control_lidar", "Use existing RTX LiDAR services: start/stop/status/diagnose, position markers, scan volumes/debug/wireframe, diagnostic probe or save accumulated map/scan. RTX sensing requires Isaac Sim. Output is confined to the project point-cloud directory.",
         {"action": enum("status", "start", "stop", "diagnose", "markers_on", "markers_off", "scan_volumes", "debug_draw", "wireframes", "probe_on", "probe_off", "save_map", "save_scan"),
          "mode": enum("yard", "representative", "trolley_bottom", "spreader", "all"),
          "config": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,96}$"}, "variant": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,96}$"},
          "far_range_m": number(1, 600), "display_range_m": number(1, 100), "output_stem": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}}, ("action",)),
    tool("capture_lidar", "Start an asynchronous yard/fleet/batched/multi-position point-cloud capture. Returns an operation ID; poll get_operation_status for completion and output files. Requires initialized RTX sensors in Isaac Sim.",
         {"mode": enum("yard", "fleet", "batches", "positions"), "output_stem": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
          "output_frame": enum("world", "sensor"), "timeout_frames": number(60, 3600, True),
          "warmup_frames": number(1, 300, True), "min_scans": number(1, 60, True), "serial": BOOL, "bay_id": BAY}, ("mode",)),
    tool("get_operation_status", "Read background capture jobs and Agent RTG motion progress, results or errors.", {"operation_id": TEXT}),
    tool("cancel_operation", "Cancel an Agent capture job or RTG motion by its operation ID.", {"operation_id": TEXT}, ("operation_id",)),
    tool("reload_yard_inventory", "Explicitly reload the already-selected inventory and refresh the stage. May replace current inventory visuals; does not accept arbitrary file paths."),
    tool("control_livestream", "Start the existing viewport-only WebRTC stream with Kit UI preserved, restore the normal UI, or enable viewport interaction. Returns the local viewer URL. Does not publish credentials.",
         {"action": enum("start", "restore_ui", "enable_interaction", "status"), "resolution": enum("1080p", "2k"), "target_fps": number(1, 60, True)}, ("action",)),
)

SCHEMAS = {item["function"]["name"]: item["function"]["parameters"] for item in TOOLS}


def needs_approval(name, arguments):
    if name in {"start_ship_to_yard_transfer","start_yard_to_ship_transfer"}:return True
    if name in {"control_ship_to_yard_transfer","control_yard_to_ship_transfer"}:return arguments.get("action") in {"resume","reset"}
    if name in {"move_rtg_to_bay", "set_rtg_axis_positions", "set_sts_axis_positions", "set_rtg_spreader_size", "submit_rtg_task", "reload_yard_inventory"}:
        return True
    action = arguments.get("action")
    return (
        name == "control_sts_task" and action in {"start_unload", "start_double20", "reset", "resume"}
        or name == "control_iecs" and action in {"submit", "mixed", "reset", "resume"}
        or name == "control_rtg_simulation" and action in {"start", "clear_task", "start_scenarios", "continue_scenarios"}
        or name == "control_live_twin" and action == "start"
        or name == "control_gamepad" and action in {"start", "position_for_pick"}
        or name == "rtg_slot_operation" and action in {"move", "lower", "lift", "attach", "detach", "pick_demo", "calibrate", "reset_calibration", "move_scene_row"}
        or name == "control_rtg_physics" and action in {"activate_visual", "activate_physics", "deactivate", "set_targets", "freeze"}
        or name == "control_plc_zmq" and action in {"physics_on", "physics_off"}
        or name == "capture_lidar" and arguments.get("mode") == "positions"
    )
