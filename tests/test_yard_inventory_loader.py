from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "omniverse" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))


def load_module(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


loader = load_module("yard_inventory_loader")
pick = load_module("rtg_pick_place_controller")


def test_live_inventory_summary_matches_fixture_stats():
    data = loader.load_inventory()
    summary = loader.inventory_summary(data)
    assert summary["stack_count"] == data["stats"]["occupiedStackCount"]
    assert summary["container_count"] == data["stats"]["containerCount"]
    assert summary["anchor_count"] == data["stats"]["anchorCount"]


def test_find_slot_returns_top_container_when_tier_is_omitted():
    data = loader.load_inventory()
    slot = loader.find_slot(data, bay_id="3C/002", row=2)
    assert slot.bay_id == "3C/002"
    assert slot.row == 2
    assert slot.tier == 4
    assert slot.container_size_ft == 40
    assert slot.length_y == pytest.approx(loader.CONTAINER_LENGTH_40FT_Y_M)
    assert slot.container.container_no.startswith("SP")


def test_slot_target_converts_inventory_slot_to_rtg_axes():
    target = pick.slot_target("3C/002", row=2)
    assert target.slot.tier == 4
    assert target.gantry_m > 0.0
    assert target.hoist_clear_m > target.hoist_pick_m
    assert target.trolley_m == pytest.approx(
        pick.trolley_for_x(target.slot.center_x)
    )


def test_pick_controller_exports_visual_attach_helpers():
    target = pick.slot_target("3C/002", row=2)
    assert pick.container_prim_path(target.slot) == (
        "/World/LiveContainers/YardInventory/Bay_3C_002/"
        "R02_T04_Container"
    )
    assert pick.container_center_z_for_hoist(
        pick.hoist_for_container_center_z(target.slot.center_z)
    ) == pytest.approx(target.slot.center_z)
    for name in (
        "attach_slot_container",
        "detach_container",
        "pick_slot_demo",
        "stop_pick_sequence",
    ):
        assert name in pick.__all__
        assert hasattr(pick, name)
