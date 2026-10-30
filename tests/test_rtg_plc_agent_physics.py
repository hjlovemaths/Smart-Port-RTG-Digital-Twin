"""Agent physics uses PLC validation and never generates an axis velocity."""
import builtins
from types import SimpleNamespace

import pytest

from test_rtg_plc_protocol import Controller, message
import rtg_plc_scene_driver as adapter
from rtg_plc_protocol import PlcEngine


class Interaction:
    enabled = False
    carried = None
    fail = False
    backend = SimpleNamespace(check=lambda: None)

    def set_enabled(self, enabled):
        if self.fail:
            raise RuntimeError("PhysX unavailable")
        if not enabled and self.carried:
            raise ValueError("land and unlock first")
        self.enabled = enabled


@pytest.fixture
def driver(monkeypatch):
    instance = adapter.PlcSceneDriver()
    instance.interaction = Interaction()
    instance.link.connected = True
    instance.link.epoch = instance.epoch = 1
    instance.engine = PlcEngine(Controller(), ((0, 500), (0, 18.06), (.5, 18)),
                                on_arm=instance._arm, interaction=instance.interaction)
    monkeypatch.setattr(builtins, adapter.DRIVER_KEY, instance, raising=False)
    monkeypatch.setattr(builtins, adapter.OWNER_FLAG, False, raising=False)
    return instance


def test_enable_arms_local_control_without_axis_motion_and_disable_preserves_existing_control(driver):
    before = driver.engine.controller.get_positions()
    result = adapter.set_rtg_plc_physics(True)
    assert result["armed"] and result["physics_enabled"]
    assert driver.engine.requested == [0, 0, 0]
    assert driver.engine.controller.get_positions() == before
    result = adapter.set_rtg_plc_physics(False)
    assert result["armed"] and not result["physics_enabled"]
    assert driver.engine.controller.get_positions() == before


def test_missing_dependency_reverts_new_control_claim(driver):
    driver.interaction.fail = True
    with pytest.raises(RuntimeError, match="PhysX unavailable"):
        adapter.set_rtg_plc_physics(True)
    assert not driver.engine.armed and not driver.interaction.enabled
    assert not getattr(builtins, adapter.OWNER_FLAG)


def test_control_conflict_cannot_enable_physics(driver):
    def occupied():
        raise ValueError("another driver owns RTG")
    driver.engine.guard = occupied
    with pytest.raises(ValueError, match="owns RTG"):
        adapter.set_rtg_plc_physics(True)
    assert not driver.engine.armed and not driver.interaction.enabled


def test_moving_axes_and_suspended_cargo_still_enforce_plc_interlocks(driver):
    driver.engine.apply(message("ctr_sw_set", 1))
    driver.engine.apply(message("vel", [0, .2]))
    with pytest.raises(ValueError, match="stop all axes"):
        adapter.set_rtg_plc_physics(True)
    assert not driver.interaction.enabled
    driver.engine.apply(message("vel", [0, 0]))
    adapter.set_rtg_plc_physics(True)
    driver.interaction.carried = "/World/LiveContainers/Load"
    with pytest.raises(ValueError, match="land and unlock"):
        adapter.set_rtg_plc_physics(False)
    assert driver.interaction.enabled and driver.engine.armed


def test_unready_scene_rejects_without_arming(driver):
    driver.link.connected = False
    with pytest.raises(RuntimeError, match="scene_ready"):
        adapter.set_rtg_plc_physics(True)
    assert not driver.engine.armed
