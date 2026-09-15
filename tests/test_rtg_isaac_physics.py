from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "omniverse"
    / "scripts"
    / "rtg_isaac_physics.py"
)
SPEC = importlib.util.spec_from_file_location("rtg_isaac_physics", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    ("value", "engineering", "usd", "expected"),
    [
        (0.0, (0.0, 18.0), (-2.25, 0.0), -2.25),
        (9.0, (0.0, 18.0), (-2.25, 0.0), -1.125),
        (18.0, (0.0, 18.0), (-2.25, 0.0), 0.0),
        (99.0, (0.0, 18.0), (-2.25, 0.0), 0.0),
    ],
)
def test_engineering_to_usd_mapping_clamps_and_interpolates(
    value, engineering, usd, expected
):
    assert MODULE.engineering_to_usd(value, engineering, usd) == pytest.approx(expected)


def test_physics_paths_use_the_existing_primary_rtg_hierarchy():
    assert MODULE.ARTICULATION_ROOT_PATH == MODULE.GANTRY_PATH
    assert MODULE.TROLLEY_PATH.startswith(MODULE.GANTRY_PATH)
    assert MODULE.HOIST_PATH.startswith(MODULE.TROLLEY_PATH)
    assert [axis for _path, _body, axis in MODULE.JOINT_SPECS] == ["Y", "X", "Z"]


def test_hoist_mapping_uses_the_full_live_yard_travel():
    lower, upper = MODULE.HOIST_USD_POSITION_LIMITS
    assert MODULE.HOIST_ENGINEERING_LIMITS == (0.0, 18.0)
    assert lower == pytest.approx(-2.465)
    assert upper == pytest.approx(4.038213076923077)
    assert MODULE.engineering_to_usd(
        14.0,
        MODULE.HOIST_ENGINEERING_LIMITS,
        MODULE.HOIST_USD_POSITION_LIMITS,
    ) == pytest.approx(2.593054615384615)
    # The old generated mapping moved only 0.76 m at this command.  The
    # current live-yard calibration must retain a visibly useful full range.
    assert upper - lower > 6.0


def test_physics_activation_is_explicit_and_reversible():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "activate_rtg_physics" in source
    assert "deactivate_rtg_physics" in source
    assert '"isaac_physics"' in source
    assert '"blender_authored_animation"' in source
    assert "_move_articulation_root" in source
    assert "_freeze_authored_animation" in source
    assert "ROPE_SYSTEM_PATH" in source
    assert "update_rtg_physics_visuals" in source
    assert "start_rtg_physics_visual_sync" in source
    assert "get_rtg_physics_state" in source


def test_launcher_keeps_experimental_collision_proxies_opt_in():
    launcher = (
        SCRIPT_PATH.parent / "launch_isaac_sim_project.py"
    ).read_text(encoding="utf-8")
    assert '"--collision-proxies"' in launcher
    assert "create_collision_proxies=args.collision_proxies" in launcher
    assert "create_collision_proxies=True" not in launcher


def test_stable_visual_control_api_is_exported():
    for name in (
        "animate_to",
        "rtg_start",
        "rtg_status",
        "move_to",
        "move_gantry",
        "move_trolley",
        "move_hoist",
        "move_primary_rtg_to_bay",
        "stop_rtg_animation",
    ):
        assert name in MODULE.__all__
        assert hasattr(MODULE, name)
    assert MODULE.DEFAULT_RTG_GANTRY_M == pytest.approx(16.0)
    assert MODULE.DEFAULT_RTG_TROLLEY_M == pytest.approx(1.6)
    assert MODULE.DEFAULT_RTG_HOIST_M == pytest.approx(14.0)


def test_launcher_exposes_visual_rtg_startup_shortcut():
    launcher = (
        SCRIPT_PATH.parent / "launch_isaac_sim_project.py"
    ).read_text(encoding="utf-8")
    assert '"--activate-rtg"' in launcher
    assert '"--activate-visual-control"' in launcher
    assert "builtins.rtg = module" in launcher
    assert 'physics["rtg_start"]' in launcher
    assert "rtg.animate_to(...)" in launcher
    assert '"--load-live-inventory"' in launcher
    assert "build_yard_inventory_layer" in launcher
    assert "context.open_stage" in launcher


def test_launcher_can_place_only_the_primary_rtg_before_lidar_startup():
    launcher = (
        SCRIPT_PATH.parent / "launch_isaac_sim_project.py"
    ).read_text(encoding="utf-8")
    assert '"--rtg-bay"' in launcher
    assert "move_primary_rtg_to_bay" in launcher
    assert "if args.rtg_bay" in launcher
    assert "Primary/middle RTG moved" in launcher


def test_primary_rtg_placement_uses_a_separate_static_root_op():
    assert MODULE.PRIMARY_RTG_PLACEMENT_OP_SUFFIX == "rtgYardPlacement"
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "PRIMARY_RTG_PLACEMENT_OP_SUFFIX" in source
    assert "Only the primary RTG was moved" in source
