"""Launch the full Isaac Sim runtime and open the project stage.

Run this file with the Isaac Sim bundled Python, not the project Python::

    D:/isaacsim/python.bat omniverse/scripts/launch_isaac_sim_project.py

The script keeps the GUI application alive and loads ``smart_port.usda``
asynchronously.  It is intentionally separate from the stage-authored demo
and the RTG physics controller so it does not modify the project scene.
"""

from __future__ import annotations

import argparse
import builtins
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path

from isaacsim import SimulationApp


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE = PROJECT_ROOT / "omniverse" / "scenes" / "smart_port.usda"
PHYSICS_SCRIPT = PROJECT_ROOT / "omniverse" / "scripts" / "rtg_isaac_physics.py"
LIDAR_SCRIPT = PROJECT_ROOT / "omniverse" / "scripts" / "rtg_yard_lidar.py"
DEFAULT_INVENTORY = PROJECT_ROOT / "omniverse" / "data" / "yard_inventory.live.json"
ISAAC_ROOT = Path(sys.executable).resolve().parents[2]
BASE_EXPERIENCE = ISAAC_ROOT / "apps" / "isaacsim.exp.base.kit"
FULL_EXPERIENCE = ISAAC_ROOT / "apps" / "isaacsim.exp.full.kit"
DLSS_MODES = {
    "performance": 0,
    "balanced": 1,
    "quality": 2,
    "auto": 3,
}


def _load_script_module(path: Path, module_name: str):
    """Load the controller as a real Python module.

    ``runpy.run_path`` returns a plain globals dictionary.  That works for
    most scripts, but it breaks ``dataclasses`` when the controller is later
    hot-reloaded from Script Editor because the generated class cannot resolve
    its module in ``sys.modules``.  A real module also gives us a safe reload
    path while Isaac Sim is running.
    """

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load RTG physics module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_physics_module(path: Path):
    return _load_script_module(path, "rtg_isaac_physics_live")


def _publish_rtg_api(module) -> None:
    """Expose the RTG control module to Isaac Script Editor snippets."""

    physics = vars(module)
    for name in physics.get("__all__", ()):
        if name in physics:
            globals()[name] = physics[name]
            setattr(builtins, name, physics[name])
    builtins.rtg = module
    builtins.rtg_physics = physics


def _publish_lidar_api(module) -> None:
    """Expose the trolley LiDAR controls to Script Editor snippets."""

    for name in module.__dict__.get("__all__", ()):
        if name in module.__dict__:
            setattr(builtins, name, module.__dict__[name])
    builtins.rtg_lidar = module


def _publish_yard_inventory_api() -> None:
    """Expose live inventory helpers to Isaac Script Editor snippets."""

    from yard_container_builder import live_inventory_status, reload_live_inventory

    builtins.yard_status = live_inventory_status
    builtins.yard_reload = reload_live_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        nargs="?",
        type=Path,
        default=DEFAULT_STAGE,
        help="USD stage to open (defaults to the project smart_port.usda)",
    )
    parser.add_argument(
        "--activate-physics",
        action="store_true",
        help="activate the RTG Isaac physics controller after the stage loads",
    )
    parser.add_argument(
        "--activate-rtg",
        "--activate-visual-control",
        dest="activate_visual_control",
        action="store_true",
        help="activate the stable visual RTG controller after the stage loads",
    )
    parser.add_argument(
        "--start-playing",
        action="store_true",
        help=(
            "start the Isaac timeline after RTG activation and send the "
            "configured first-pass target"
        ),
    )
    parser.add_argument(
        "--rtg-gantry",
        type=float,
        default=16.0,
        help="initial visual RTG gantry target in metres (default: 16.0)",
    )
    parser.add_argument(
        "--rtg-trolley",
        type=float,
        default=1.6,
        help="initial visual RTG trolley target in metres (default: 1.6)",
    )
    parser.add_argument(
        "--rtg-hoist",
        type=float,
        default=14.0,
        help="initial visual RTG hoist target in metres (default: 14.0)",
    )
    parser.add_argument(
        "--collision-proxies",
        action="store_true",
        help=(
            "enable the experimental coarse collision proxies; keep this "
            "off for the stable joint-only physics pass"
        ),
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1780,
        help="viewport/render width in pixels (default: 1780)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=900,
        help="viewport/render height in pixels (default: 900)",
    )
    parser.add_argument(
        "--dlss-mode",
        choices=tuple(DLSS_MODES),
        default="balanced",
        help="DLSS quality preset (default: balanced)",
    )
    parser.add_argument(
        "--hide-fps",
        action="store_true",
        help="do not open the Statistics/FPS window automatically",
    )
    parser.add_argument(
        "--fps-limit",
        type=int,
        default=60,
        help="cap the interactive Kit update loop (default: 60 FPS)",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=12,
        help="maximum Kit worker threads; leaves CPU capacity for the UI (default: 12)",
    )
    parser.add_argument(
        "--full-ui",
        action="store_true",
        help="load the much heavier Isaac Sim Full toolset instead of the Base editor",
    )
    parser.add_argument(
        "--load-live-inventory",
        action="store_true",
        help="rebuild and attach live yard containers from yard_inventory.live.json",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="inventory JSON used with --load-live-inventory",
    )
    parser.add_argument(
        "--inventory-limit",
        type=int,
        default=None,
        help="debug limit for the number of occupied stacks loaded from inventory",
    )
    parser.add_argument(
        "--enable-rtg-lidar",
        action="store_true",
        help="attach the RTX LiDAR to the primary RTG trolley after stage load",
    )
    parser.add_argument(
        "--freeze-rtgs",
        action="store_true",
        help=(
            "freeze primary and background RTGs at their frame-1 pose for "
            "static sensor capture; does not enable PhysX"
        ),
    )
    parser.add_argument(
        "--rtg-bay",
        default=None,
        help=(
            "place only the primary/middle RTG over a yard bay before LiDAR "
            "startup, for example 3C/002"
        ),
    )
    parser.add_argument(
        "--lidar-config",
        default="Example_Rotary",
        help="Isaac Sim RTX LiDAR config name (default: Example_Rotary)",
    )
    parser.add_argument(
        "--lidar-far-range",
        type=float,
        default=600.0,
        help="LiDAR maximum range in metres (default: 600; covers the live yard)",
    )
    parser.add_argument(
        "--lidar-output-dir",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "rtg_pointcloud",
        help="directory for PLY/PCD/NPY LiDAR captures",
    )
    args = parser.parse_args()
    if args.fps_limit <= 0:
        parser.error("--fps-limit must be greater than zero")
    if args.cpu_threads <= 0:
        parser.error("--cpu-threads must be greater than zero")
    if args.activate_physics and args.activate_visual_control:
        parser.error("use either --activate-physics or --activate-rtg, not both")
    stage_path = args.stage.resolve()
    if not stage_path.is_file():
        raise FileNotFoundError(f"USD stage not found: {stage_path}")

    inventory_build_result = None

    # The managed desktop runtime may not be allowed to create the default
    # per-user Warp cache under AppData. Keep the cache inside the project,
    # where the launcher is guaranteed to have write access.
    warp_cache = PROJECT_ROOT / ".cache" / "warp"
    warp_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("WARP_CACHE_PATH", str(warp_cache))

    simulation_app = SimulationApp(
        {
            "headless": False,
            "width": args.width,
            "height": args.height,
            "window_width": args.width,
            "window_height": args.height,
            "multi_gpu": False,
            "renderer": "RaytracedLighting",
            "limit_cpu_threads": args.cpu_threads,
            "extra_args": [
                f"--/rtx/post/dlss/execMode={DLSS_MODES[args.dlss_mode]}",
                "--/app/runLoops/main/rateLimitEnabled=true",
                f"--/app/runLoops/main/rateLimitFrequency={args.fps_limit}",
                "--/app/runLoops/rendering_0/rateLimitEnabled=true",
                f"--/app/runLoops/rendering_0/rateLimitFrequency={args.fps_limit}",
                # RTX annotators read LiDAR returns from the host buffer.  A
                # user config that forces the GPU-resident path results in
                # empty GenericModelOutput data.
                "--/app/sensors/nv/lidar/outputBufferOnGPU=false",
                "--enable",
                "omni.kit.window.script_editor",
            ],
        },
        experience=str(FULL_EXPERIENCE if args.full_ui else BASE_EXPERIENCE),
    )

    try:
        # smart_port.usda already contains ./live_containers.usda as a
        # sublayer.  Build that file after SimulationApp initializes Kit (so
        # pxr is available), but before opening the stage.  Rebuilding it
        # after viewport/RTX products exist would require a stage reload and
        # can invalidate Hydra RenderProducts, including RTX LiDAR products.
        if args.load_live_inventory:
            from yard_container_builder import build_yard_inventory_layer

            inventory_build_result = build_yard_inventory_layer(
                inventory_path=args.inventory,
                limit=args.inventory_limit,
            )
            print(
                "[RTG] Live inventory USD prepared before stage open: "
                f"{inventory_build_result}",
                flush=True,
            )

        import omni.usd

        context = omni.usd.get_context()
        print(f"[RTG] Opening stage: {stage_path}", flush=True)
        context.open_stage(str(stage_path))

        # USD loading is asynchronous.  Pump the Kit event loop until the
        # stage is available, then leave the app in the normal interactive
        # loop so the user can press Play and use the Script Editor.
        for _ in range(300):
            simulation_app.update()
            if context.get_stage() is not None:
                break

        if context.get_stage() is None:
            raise RuntimeError(f"Isaac Sim could not open stage: {stage_path}")

        print("[RTG] Stage loaded. Isaac Sim is ready.", flush=True)
        _publish_yard_inventory_api()
        if not args.hide_fps:
            import omni.ui

            statistics_window = omni.ui.Workspace.get_window("Statistics")
            if statistics_window is not None:
                statistics_window.visible = True
                print("[RTG] Statistics/FPS window opened.", flush=True)
        if args.load_live_inventory:
            try:
                from yard_container_builder import live_inventory_status

                inventory_status = live_inventory_status(context.get_stage())
                if not inventory_status.get("inventory_root_valid"):
                    raise RuntimeError(
                        "live_containers.usda was prepared, but the existing "
                        "stage sublayer did not compose /World/LiveContainers"
                    )
                print(
                    "[RTG] Live inventory loaded: "
                    f"build={inventory_build_result}; status={inventory_status}",
                    flush=True,
                )
            except Exception as error:
                print(
                    "[RTG] Live inventory startup load failed; "
                    "Isaac will continue. Close/restart Isaac Sim after fixing "
                    "the inventory layer, rather than reloading the Stage from "
                    f"Script Editor. Error: {error}",
                    flush=True,
                )
                traceback.print_exc()
        physics_module = None
        if args.rtg_bay:
            physics_module = _load_physics_module(PHYSICS_SCRIPT)
            placement_result = physics_module.move_primary_rtg_to_bay(
                args.rtg_bay,
                context.get_stage(),
            )
            print(
                f"[RTG] Primary/middle RTG moved to {args.rtg_bay}: "
                f"{placement_result}",
                flush=True,
            )
        if args.freeze_rtgs:
            if physics_module is None:
                physics_module = _load_physics_module(PHYSICS_SCRIPT)
            freeze_result = physics_module.freeze_rtg_animations(context.get_stage())
            print(f"[RTG] RTG animation held for static sensor capture: {freeze_result}", flush=True)
        if physics_module is not None:
            _publish_rtg_api(physics_module)
        if args.enable_rtg_lidar:
            lidar_module = _load_script_module(LIDAR_SCRIPT, "rtg_yard_lidar_live")
            _publish_lidar_api(lidar_module)
            lidar_result = lidar_module.start_rtg_yard_lidar(
                context.get_stage(),
                config=args.lidar_config,
                output_dir=args.lidar_output_dir,
                far_range_m=args.lidar_far_range,
            )
            print(f"[RTG LiDAR] Ready: {lidar_result}", flush=True)
        if args.activate_physics or args.activate_visual_control:
            if physics_module is None:
                physics_module = _load_physics_module(PHYSICS_SCRIPT)
            _publish_rtg_api(physics_module)
            physics = vars(physics_module)
            if args.activate_visual_control:
                result = physics["rtg_start"](
                    gantry_m=args.rtg_gantry,
                    trolley_m=args.rtg_trolley,
                    hoist_m=args.rtg_hoist,
                    play=args.start_playing,
                )
                print(f"[RTG Physics] Visual RTG ready: {result}", flush=True)
                print(
                    "[RTG Physics] Script Editor shortcuts: "
                    "rtg.animate_to(...), rtg.move_to(...), "
                    "move_gantry(...), move_trolley(...), move_hoist(...)",
                    flush=True,
                )
                args.start_playing = False
            else:
                # The visual asset has no production collision mesh yet.  The
                # experimental coarse boxes overlap at the trolley/hoist joints
                # and can create self-contact that tips the spreader over during
                # the first physics step, so keep the launcher on the stable
                # joint-only pass unless the user explicitly opts in.
                result = physics["activate_rtg_physics"](
                    create_collision_proxies=args.collision_proxies
                )
                print(f"[RTG Physics] Activated: {result}", flush=True)
            if args.start_playing:
                physics["set_rtg_physics_targets"](
                    gantry_m=args.rtg_gantry,
                    trolley_m=args.rtg_trolley,
                    hoist_m=args.rtg_hoist,
                )
                import omni.timeline

                omni.timeline.get_timeline_interface().play()
                print(
                    "[RTG Physics] Timeline started with first-pass targets "
                    f"gantry={args.rtg_gantry}m "
                    f"trolley={args.rtg_trolley}m "
                    f"hoist={args.rtg_hoist}m",
                    flush=True,
                )
        # SimulationApp.update() is a manual pump and can otherwise run as
        # fast as the CPU allows.  On the full smart-port stage that consumes
        # most logical cores even while the timeline is paused, starving UI
        # input and making a powerful workstation appear sluggish.  Keep an
        # explicit wall-clock cap as well as the Kit run-loop settings above.
        frame_period = 1.0 / float(args.fps_limit)
        while simulation_app.is_running():
            frame_start = time.perf_counter()
            simulation_app.update()
            sleep_seconds = frame_period - (time.perf_counter() - frame_start)
            if sleep_seconds > 0.0:
                time.sleep(sleep_seconds)
    finally:
        simulation_app.close()


if __name__ == "__main__":
    main()
