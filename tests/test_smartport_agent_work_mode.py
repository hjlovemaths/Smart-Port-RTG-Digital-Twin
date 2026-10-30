"""Device workMode and freshness must be exposed independently of local control."""
import ast
import builtins
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "omniverse/extensions/smartport.agent_panel"))
from smartport.agent_panel import scene_runtime


@pytest.fixture
def live(monkeypatch):
    now = 1793342400.0
    fields = {"rtg:workMode": 5, "rtg:rtgName": "RTG01", "rtg:lastAppliedUnix": now-.1,
              "rtg:lastTwinTimestampUtc": datetime.fromtimestamp(now-.1, timezone.utc).isoformat()}
    receiver = SimpleNamespace(_thread=SimpleNamespace(is_alive=lambda: True), latest_state_at=now-.1,
                               _stop_event=SimpleNamespace(is_set=lambda: False))
    modules = {"rtg_twin_state_receiver": SimpleNamespace(_receiver=receiver)}
    prim = SimpleNamespace(GetAttribute=lambda key: SimpleNamespace(Get=lambda: fields.get(key)))
    stage = SimpleNamespace(GetPrimAtPath=lambda _: prim)
    monkeypatch.setattr(scene_runtime.time, "time", lambda: now)
    monkeypatch.setattr(scene_runtime, "existing_module", modules.get)
    return SimpleNamespace(now=now, fields=fields, receiver=receiver, modules=modules, stage=stage)


@pytest.mark.parametrize("code,label", [(0,"未知"),(1,"自动模式"),(2,"辅助模式"),(3,"远程手动"),(4,"本地模式"),(5,"维修模式")])
def test_work_mode_mapping_comes_from_received_field(live, code, label):
    live.fields["rtg:workMode"] = code
    result = scene_runtime.rtg_work_mode(live.stage)
    assert result["workMode"] == code and result["label"] == label
    assert result["is_current"] is (code != 0)


def test_stopped_or_stopping_receiver_does_not_report_last_mode_as_current(live):
    live.receiver._stop_event.is_set = lambda: True
    result = scene_runtime.rtg_work_mode(live.stage)
    assert result["label"] == "维修模式" and result["state"] == "receiver_stopped"
    assert not result["is_current"]


def test_no_new_frames_marks_mode_stale(live):
    live.receiver.latest_state_at = live.now-20
    result = scene_runtime.rtg_work_mode(live.stage)
    assert result["state"] == "stale" and not result["is_current"]


def test_fresh_receipt_of_old_upstream_snapshot_is_still_stale(live):
    live.fields["rtg:lastTwinTimestampUtc"] = datetime.fromtimestamp(live.now-30, timezone.utc).isoformat()
    result = scene_runtime.rtg_work_mode(live.stage)
    assert result["state"] == "stale" and result["age_seconds"] == 30


def test_simulator_is_never_reported_as_current_real_device_mode(live):
    live.modules["rtg_simulation_driver"] = SimpleNamespace(
        _simulation=SimpleNamespace(_subscription=object()), get_simulation_status=lambda: {})
    result = scene_runtime.rtg_work_mode(live.stage)
    assert result["source"] == "simulation" and result["state"] == "simulation"
    assert not result["is_current"]


@pytest.mark.parametrize("value", [None, True, "invalid", 99])
def test_missing_and_invalid_work_modes_remain_unknown(live, value):
    live.fields["rtg:workMode"] = value
    result = scene_runtime.rtg_work_mode(live.stage)
    assert not result["known"] and not result["is_current"] and result["label"] == "未知"


def test_local_remote_manual_flag_cannot_override_plc_work_mode(live, monkeypatch):
    monkeypatch.setattr(builtins, "_SMART_PORT_RTG_REMOTE_MANUAL_ACTIVE", True, raising=False)
    assert scene_runtime.rtg_work_mode(live.stage)["label"] == "维修模式"


def test_labels_match_receiver_heartbeat_contract():
    tree = ast.parse((ROOT / "omniverse/scripts/rtg_twin_state_receiver.py").read_text(encoding="utf-8"))
    labels = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "WORK_MODE_LABELS" for target in node.targets))
    assert ast.literal_eval(labels) == scene_runtime.WORK_MODE_LABELS
