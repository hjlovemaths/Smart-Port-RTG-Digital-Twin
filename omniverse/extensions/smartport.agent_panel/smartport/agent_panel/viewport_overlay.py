"""Viewport-local chat controls, independent of the docked Kit workspace."""

from __future__ import annotations

from typing import Any
import time


def panel_layout(width: float, height: float) -> dict[str, tuple[float, float, float, float]]:
    """Return input/output rectangles in viewport UI units, with no overlap."""
    width, height = max(1.0, width), max(1.0, height)
    margin = min(14.0, width / 20.0, height / 20.0)
    available = width - 2 * margin
    input_width = min(560.0, max(280.0, width * 0.30), available)
    input_height = min(100.0, height - 2 * margin)
    output_width = min(320.0, max(240.0, width * 0.18), available)
    output_height = min(340.0, max(190.0, height * 0.40), height - 2 * margin)
    input_x = (width - input_width) / 2
    input_y = height - margin - input_height
    output_x = width - margin - output_width
    output_y = height - margin - output_height
    # Small viewports put the response above the input instead of covering it.
    if input_x + input_width + margin > output_x:
        output_height = max(0.0, min(output_height, input_y - 2 * margin))
        output_y = max(margin, input_y - margin - output_height)
    return {
        "input": (input_x, input_y, input_width, input_height),
        "output": (output_x, output_y, output_width, output_height),
    }


class ViewportChatOverlay:
    def __init__(self, panel: Any) -> None:
        self._panel = panel
        self._viewport = None
        self._frame = None
        self._input_placer = self._output_placer = None
        self._input_card = self._output_card = None
        self._size = None
        self._input_fits = True
        self.visible = True

    def _apply_input_visibility(self) -> None:
        web_input_active = time.monotonic() < getattr(self._panel, "_web_input_lease_until", 0)
        if self._input_placer is not None:
            self._input_placer.visible = not web_input_active
        if self._input_card is not None:
            self._input_card.visible = self._input_fits and not web_input_active

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if self._frame is not None:
            self._frame.visible = visible
        if not visible:
            self._panel._cancel_pending_approval("panel_closed")

    def update(self) -> None:
        import omni.ui as ui
        from omni.kit.viewport.utility import get_active_viewport_window
        from omni.kit.viewport.window import get_viewport_window_instances

        # Stay on the main viewport when a work-camera window receives focus.
        windows = list(get_viewport_window_instances())
        if self._viewport not in windows:
            self.destroy()
            viewport = get_active_viewport_window()
            if viewport is None:
                return
            self._viewport = viewport
            self._frame = viewport.get_frame("smartport.agent_panel.chat_overlay")
            self._build()
            self._frame.visible = self.visible
        if not self.visible:
            return
        # The browser-native IME card replaces only the streamed input card.
        # Apply this before the size fast path so it also works without resize.
        self._apply_input_visibility()
        size = (self._frame.computed_width, self._frame.computed_height)
        if min(size) <= 0 or size == self._size:
            return
        self._size = size
        layout = panel_layout(*size)
        for name, placer, card in (
            ("input", self._input_placer, self._input_card),
            ("output", self._output_placer, self._output_card),
        ):
            x, y, width, height = layout[name]
            placer.offset_x, placer.offset_y = ui.Pixel(x), ui.Pixel(y)
            placer.width, placer.height = ui.Pixel(width), ui.Pixel(height)
            card.width, card.height = ui.Pixel(width), ui.Pixel(height)
            if name == "input":
                self._input_fits = height >= 70
                self._apply_input_visibility()
            else:
                card.visible = height >= 90

    def _build(self) -> None:
        import omni.ui as ui
        from .extension import _conversation_text_style

        panel = self._panel
        style = {
            "Rectangle::agent_card": {
                "background_color": 0xE52B2520,
                "border_color": 0x806E6357,
                "border_width": 1,
                "border_radius": 8,
            },
            "Label": {"color": 0xFFF0EAE4, "font_size": 14},
            "Button": {"background_color": 0xFF51463A, "border_radius": 4},
            "Button:hovered": {"background_color": 0xFF70604A},
            "StringField": {"background_color": 0xF51C1915, "border_radius": 4},
        }
        with self._frame:
            # Only the two cards contain hit-testable widgets. Empty viewport
            # space keeps its usual orbit, pan, zoom and selection behavior.
            with ui.ZStack(style=style):
                with ui.Placer(stable_size=True, width=560, height=100) as self._input_placer:
                    with ui.ZStack(width=560, height=100) as self._input_card:
                        ui.Rectangle(name="agent_card")
                        with ui.VStack(
                            name="agent_input_content", spacing=5,
                            style={"VStack::agent_input_content": {"margin": 10}},
                        ):
                            with ui.HStack(height=18):
                                ui.Label("SMART PORT / AGENT", height=18)
                                ui.Button("Settings", width=68, clicked_fn=panel._show_settings)
                            with ui.HStack(height=34, spacing=8):
                                panel._input_field = ui.StringField(
                                    model=panel._input_model, multiline=False,
                                    style=_conversation_text_style(),
                                )
                                ui.Button("Send", width=60, clicked_fn=panel._send_message)
                            panel._status_label = ui.Label(
                                panel._last_status, height=18, elided_text=True,
                                tooltip=panel._last_status,
                            )
                with ui.Placer(stable_size=True, width=320, height=300) as self._output_placer:
                    with ui.ZStack(width=320, height=300) as self._output_card:
                        ui.Rectangle(name="agent_card")
                        with ui.VStack(
                            name="agent_output_content", spacing=7,
                            style={"VStack::agent_output_content": {"margin": 12}},
                        ):
                            with ui.HStack(height=22, spacing=6):
                                ui.Label("AGENT RESPONSE", height=22)
                                ui.Button("Clear", width=48, clicked_fn=panel._clear_conversation)
                            ui.Separator(height=1)
                            with ui.ScrollingFrame(
                                height=ui.Fraction(1),
                                horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                                style={"background_color": 0x00000000},
                            ) as panel._chat_scroll:
                                with ui.VStack(spacing=4, height=0) as panel._chat_content:
                                    panel._chat_log = ui.Label(
                                        panel._format_chat(), word_wrap=True,
                                        alignment=ui.Alignment.LEFT_TOP,
                                        style=_conversation_text_style(),
                                    )
                            panel._approval_frame = ui.Frame(height=0, visible=False)
        panel._render_chat()

    def destroy(self) -> None:
        if self._frame is not None:
            self._panel._cancel_pending_approval("viewport_closed")
            self._frame.visible = False
            self._frame.clear()
            for name in (
                "_input_field", "_status_label", "_chat_scroll", "_chat_content",
                "_chat_log", "_approval_frame",
            ):
                setattr(self._panel, name, None)
            self._panel._approval_ui_dirty = False
        self._frame = self._viewport = None
        self._input_placer = self._output_placer = None
        self._input_card = self._output_card = None
        self._size = None
