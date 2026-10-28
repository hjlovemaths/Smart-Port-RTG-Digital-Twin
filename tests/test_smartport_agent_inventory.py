from __future__ import annotations

import builtins
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse" / "scripts"))
sys.path.insert(0, str(ROOT / "omniverse" / "extensions" / "smartport.agent_panel"))

from yard_inventory import YardInventory
from smartport.agent_panel import yard_queries


def stack(bay_id="4C/040", heights=(1, 0, 1, 1, 1, 0)):
    block, number = bay_id.split("/")
    size = 20 if int(number) % 2 else 40
    return {
        "bayId": bay_id, "block": block, "bayNumber": int(number),
        "containerSizeFt": size, "source": "test-snapshot", "revision": 1,
        "rows": [
            [{"containerNo": f"BOX-{row}-{tier}", "sizeFt": size, "type": f"{size}GP"}
             for tier in range(1, height + 1)]
            for row, height in enumerate(heights, start=1)
        ],
    }


def inventory(*stacks):
    return YardInventory(stacks={item["bayId"]: item for item in stacks})


def test_4c41_resolves_covering_40ft_bay_and_preserves_row_tier_details():
    data = inventory(stack(heights=(1, 3, 2, 0, 0, 2)))
    before = copy.deepcopy(data.stacks)
    result = yard_queries.describe_bay(data, "4c41", {"coverage_complete": False})
    assert result["requested_bay_id"] == "4C/041"
    assert result["logical_bay_ids"] == ["4C/040"]
    assert result["status"] == "occupied"
    assert result["coverage_complete_for_requested_bay"]
    assert result["container_count"] == 8
    bay = result["stacks"][0]
    assert bay["physical_bays"] == ["4C/039", "4C/041"]
    assert bay["bay_map"] == "132002"
    assert bay["row_heights"] == [1, 3, 2, 0, 0, 2]
    assert any(item["row"] == 2 and item["tier"] == 3 and item["container_no"] == "BOX-2-3" for item in bay["containers"])
    assert "T3:  .  X  .  .  .  ." in bay["layout_ascii"]
    assert data.stacks == before


def test_40ft_query_includes_two_adjacent_20ft_stacks_without_double_counting():
    data = inventory(stack("4C/039", (1, 0, 0, 0, 0, 0)), stack("4C/041", (0, 2, 0, 0, 0, 0)))
    result = yard_queries.describe_bay(data, "4C40", {"coverage_complete": False})
    assert result["logical_bay_ids"] == ["4C/039", "4C/041"]
    assert result["container_count"] == 3
    assert result["coverage_complete_for_requested_bay"]


def test_absent_live_bay_is_unknown_but_explicit_empty_or_complete_snapshot_is_empty():
    assert yard_queries.describe_bay(inventory(), "4C41", {"coverage_complete": False})["status"] == "unknown"
    assert yard_queries.describe_bay(inventory(), "4C41", {"coverage_complete": True})["status"] == "empty"
    empty_bay = inventory(stack("4C/041", (0, 0, 0, 0, 0, 0)))
    assert yard_queries.describe_bay(empty_bay, "4C41", {"coverage_complete": False})["status"] == "empty"


def test_block_summary_filters_and_counts_40ft_containers_once():
    data = inventory(stack(), stack("3C/001", (2, 0, 0, 0, 0, 0)))
    result = yard_queries.describe_yard(data, {}, "4c")
    assert result["container_count"] == 4
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["teu"] == 8
    assert result["blocks"][0]["occupied_logical_bay_count"] == 1
    assert yard_queries.describe_yard(data, {})["container_count"] == 6


@pytest.mark.parametrize("bay_id", ["4C42", "1C92", "4C00", "../secrets"])
def test_invalid_bay_does_not_return_a_fabricated_empty_map(bay_id):
    with pytest.raises(ValueError):
        yard_queries.describe_bay(inventory(), bay_id, {})


class FakePrim:
    def __init__(self, attrs):
        self.attrs = attrs

    def GetAttribute(self, name):
        return SimpleNamespace(Get=lambda: self.attrs[name]) if name in self.attrs else None


class FakeStage:
    def __init__(self, **attrs):
        self.root = FakePrim(attrs)

    def GetPrimAtPath(self, path):
        return self.root if path.endswith("/YardInventory") else None

    def GetLayerStack(self):
        return []


def test_selected_live_file_is_fresh_on_each_query_and_never_replaced_with_simulation(tmp_path):
    live_path = tmp_path / "yard_inventory.live.json"
    sim_path = tmp_path / "yard_inventory.sim.json"
    inventory(stack()).save(live_path)
    inventory(stack(heights=(6, 6, 6, 6, 6, 6))).save(sim_path)
    stage = FakeStage(**{"inventory:path": str(live_path), "inventory:mode": "live"})
    result = yard_queries.query_bay(stage, tmp_path, "4C41")
    assert result["container_count"] == 4
    assert result["source"]["mode"] == "live"
    assert not result["source"]["is_live_runtime"]
    inventory(stack(heights=(2, 0, 0, 0, 0, 0))).save(live_path)
    assert yard_queries.query_bay(stage, tmp_path, "4C41")["container_count"] == 2
    missing = FakeStage(**{"inventory:path": str(tmp_path / "missing.live.json")})
    with pytest.raises(FileNotFoundError, match="unavailable"):
        yard_queries.query_bay(missing, tmp_path, "4C41")


def test_stage_can_select_simulation_without_claiming_live_stock(tmp_path):
    path = tmp_path / "yard_inventory.sim.json"
    inventory(stack()).save(path)
    stage = FakeStage(**{"inventory:path": path.name, "inventory:mode": "simulation"})
    result = yard_queries.query_bay(stage, tmp_path, "4C41")
    assert result["source"]["mode"] == "simulation"
    assert not result["source"]["is_live_runtime"]


def test_matching_active_driver_uses_unsaved_memory_but_other_stage_does_not(monkeypatch, tmp_path):
    path = tmp_path / "yard_inventory.live.json"
    inventory(stack()).save(path)
    stage = FakeStage(**{"inventory:path": str(path)})
    driver = SimpleNamespace(
        _inventory_stage_id=id(stage), _inventory_load_error=None,
        _inventory=inventory(stack(heights=(2, 2, 0, 0, 0, 1))),
        _inventory_mode="live", _inventory_path=path,
        _inventory_full_sync_ready=False, _inventory_sync_reason="partial task snapshot",
    )
    monkeypatch.setattr(builtins, "_smart_port_rtg_panel_facing_driver", driver, raising=False)
    result = yard_queries.query_bay(stage, tmp_path, "4C41")
    assert result["container_count"] == 5
    assert result["source"]["is_live_runtime"]
    other_stage = FakeStage(**{"inventory:path": str(path)})
    assert yard_queries.query_bay(other_stage, tmp_path, "4C41")["container_count"] == 4
    assert sum(len(row) for row in driver._inventory.stacks["4C/040"]["rows"]) == 5


def test_simulator_inventory_is_read_from_its_actual_unsaved_driver(monkeypatch, tmp_path):
    path = tmp_path / "yard_inventory.sim.json"
    inventory(stack()).save(path)
    stage = FakeStage(**{"inventory:path": str(path)})
    driver = SimpleNamespace(
        _inventory_stage_id=id(stage), _inventory_load_error=None,
        _inventory=inventory(stack(heights=(3, 3, 0, 0, 0, 0))),
        _inventory_mode="simulation", _inventory_path=path,
        _inventory_full_sync_ready=True, _inventory_sync_reason=None,
    )
    simulation = SimpleNamespace(_driver=driver, _subscription=object())
    monkeypatch.setattr(builtins, "_smart_port_rtg_panel_facing_driver", None, raising=False)
    monkeypatch.setitem(sys.modules, "rtg_simulation_driver", SimpleNamespace(_simulation=simulation))
    result = yard_queries.query_bay(stage, tmp_path, "4C41")
    assert result["container_count"] == 6
    assert result["source"]["mode"] == "simulation"
    assert result["source"]["kind"] == "active_driver_inventory"
