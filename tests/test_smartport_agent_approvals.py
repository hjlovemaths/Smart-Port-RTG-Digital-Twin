"""Exercise the panel's real approval lifecycle without a running Kit UI."""
import ast
import json
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / "omniverse/extensions/smartport.agent_panel"
          / "smartport/agent_panel/extension.py")


class Widget:
    stack = []
    in_ui_event = False

    def __init__(self, text="", clicked_fn=None, **kwargs):
        self.text, self.clicked_fn, self.children = text, clicked_fn, []
        self.visible, self.height = kwargs.get("visible", True), kwargs.get("height", 0)
        if self.stack:
            self.stack[-1].children.append(self)

    def __enter__(self):
        self.stack.append(self)
        return self

    def __exit__(self, *args):
        self.stack.pop()

    def clear(self):
        assert not self.in_ui_event, "Container::clear is forbidden during a UI click"
        self.children.clear()

    def click(self):
        Widget.in_ui_event = True
        try:
            self.clicked_fn()
        finally:
            Widget.in_ui_event = False

    def buttons(self):
        return ([self] if self.clicked_fn else []) + [item for child in self.children for item in child.buttons()]


@pytest.fixture
def panel():
    # Importing omni.ui requires Kit. Compile the production class unchanged,
    # replacing only its extension base and external UI/runtime dependencies.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    cls.bases = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    clock = SimpleNamespace(monotonic=time.monotonic)
    scope = {"threading": threading, "time": clock, "json": json, "queue": queue,
             "ui": SimpleNamespace(VStack=Widget, HStack=Widget, Label=Widget, Button=Widget, Pixel=lambda value: value),
             "_APPROVAL_TIMEOUT_SECONDS": .04}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), scope)
    instance = scope[cls.name]()
    instance._approval_frame = Widget(visible=False)
    instance._pending_approval = None
    instance._approval_ui_dirty = False
    instance._chat_lines = []
    instance._history = []
    instance._chat_log = instance._chat_scroll = None
    instance._status_label = Widget()
    instance._ui_events, instance._kit_commands = queue.Queue(), queue.Queue()
    instance._window = SimpleNamespace(visible=True)
    instance._overlay = SimpleNamespace(visible=True, update=lambda: None)
    instance._shutting_down = instance._busy = False
    instance._runtime = None
    instance.clock = clock
    return instance


def request(panel, duration=30):
    return {"kind": "approval", "name": "set_rtg_axis_positions", "arguments": {"hoist_m": 10},
            "decision": threading.Event(), "approved": False, "lock": threading.Lock(),
            "expires_at": panel.clock.monotonic() + duration}


@pytest.mark.parametrize("button,approved", [("Confirm", True), ("Cancel", False)])
def test_click_removes_both_buttons_and_cannot_decide_twice(panel, button, approved):
    approval = request(panel)
    panel._handle_ui_event(approval)
    panel._on_update(None)
    controls = panel._approval_frame.buttons()
    assert len(controls) == 2
    next(item for item in controls if item.text == button).click()
    assert approval["decision"].is_set() and approval["approved"] is approved
    assert panel._pending_approval is None
    assert not panel._approval_frame.visible and panel._approval_frame.height == 0
    # Hide/disable now, destroy only after the callback has returned.
    assert not panel._approval_frame.enabled
    assert len(panel._approval_frame.buttons()) == 2
    panel._on_update(None)
    assert not panel._approval_frame.buttons()
    # A queued double click on a now-destroyed control cannot reverse consent.
    next(item for item in controls if item.text != button).click()
    assert approval["approved"] is approved
    assert sum(speaker == "Approval" for speaker, _ in panel._chat_lines) == 1


def test_only_one_request_is_displayed_and_old_callbacks_do_not_clear_new_request(panel):
    first, second = request(panel), request(panel)
    panel._handle_ui_event(first)
    panel._on_update(None)
    old_confirm = panel._approval_frame.buttons()[0].clicked_fn
    panel._handle_ui_event(first)
    assert len(panel._approval_frame.buttons()) == 2
    panel._handle_ui_event(second)
    panel._on_update(None)
    assert first["decision"].is_set() and not first["approved"]
    assert panel._pending_approval is second
    assert len(panel._approval_frame.buttons()) == 2
    old_confirm()
    panel._handle_ui_event({"kind": "approval_finished", "approval": first})
    assert panel._pending_approval is second and not second["decision"].is_set()
    assert len(panel._approval_frame.buttons()) == 2


def test_expired_approval_cannot_be_confirmed_even_before_worker_wakes(panel):
    approval = request(panel)
    panel._handle_ui_event(approval)
    panel._on_update(None)
    callback = panel._approval_frame.buttons()[0].clicked_fn
    panel.clock.monotonic = lambda: approval["expires_at"] + .1
    callback()
    assert approval["decision"].is_set() and not approval["approved"]
    assert approval["reason"] == "timeout"
    assert not panel._approval_frame.visible


def test_worker_timeout_finishes_request_and_delayed_ui_event_never_adds_buttons(panel):
    result = []
    worker = threading.Thread(target=lambda: result.append(panel._request_operator_confirmation("move", {})))
    worker.start()
    approval = panel._ui_events.get(timeout=1)
    worker.join(1)
    assert not worker.is_alive() and result == [False]
    panel._handle_ui_event(approval)
    panel._on_update(None)
    assert panel._ui_events.empty()
    assert not panel._approval_frame.buttons() and panel._pending_approval is None


@pytest.mark.parametrize("finish", ["timeout", "hidden", "failed", "complete", "references_cleared"])
def test_pending_controls_are_cleaned_for_each_request_exit(panel, finish):
    approval = request(panel)
    panel._handle_ui_event(approval)
    panel._on_update(None)
    if finish == "timeout":
        panel.clock.monotonic = lambda: approval["expires_at"] + 1
        panel._on_update(None)
    elif finish == "hidden":
        panel._overlay.visible = False
        panel._on_update(None)
    elif finish == "references_cleared":
        panel._clear_ui_references()
    else:
        panel._handle_ui_event({"kind": finish})
    assert approval["decision"].is_set() and not approval["approved"]
    assert panel._pending_approval is None
    if panel._approval_frame is not None:
        assert not panel._approval_frame.visible
        panel._on_update(None)
        assert not panel._approval_frame.buttons()


def test_replacement_before_deferred_cleanup_does_not_clear_the_new_request(panel):
    first, second = request(panel), request(panel)
    panel._handle_ui_event(first)
    panel._on_update(None)
    panel._approval_frame.buttons()[1].click()
    # The next approval can arrive before the scheduled clear. One update
    # must build only the new request, without reviving the old controls.
    panel._handle_ui_event(second)
    panel._on_update(None)
    assert panel._pending_approval is second
    assert len(panel._approval_frame.buttons()) == 2
    panel._approval_frame.buttons()[0].click()
    assert second["approved"] and not first["approved"]
