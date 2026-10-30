"""Kit --exec bootstrap for launch_kit_webrtc.py; keeps Agent HUD in the stream."""

import asyncio
import json
import os
from pathlib import Path
import sys
import traceback

import carb.settings
import omni.appwindow
import omni.kit.app
import omni.usd


async def start() -> None:
    config = json.loads(os.environ["SMARTPORT_HEADLESS_CONFIG"])
    status_path = Path(config["status_path"])
    app = omni.kit.app.get_app()
    try:
        if carb.settings.get_settings().get("/app/window/enabled"):
            raise RuntimeError("This entry point requires --no-window")
        for _ in range(12):
            await app.next_update_async()
        context = omni.usd.get_context()
        success, error = await context.open_stage_async(config["stage"])
        if not success:
            raise RuntimeError(f"Cannot open Smart Port stage: {error}")
        scripts = str(Path(__file__).resolve().parent)
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import rtg_viewport_only_livestream as stream

        stream.start_viewport_only_livestream(
            width=config["width"], height=config["height"], target_fps=config["target_fps"],
            http_port=config["http_port"], signal_port=config["signal_port"],
            stream_port=config["stream_port"], hide_kit_ui=True, allow_dynamic_resize=True,
        )
        from omni.services.livestream.webrtc import extension as web_client
        from rtg_webrtc_viewer import install_viewer_page

        install_viewer_page(Path(web_client.__file__).parent / "web")
        for _ in range(24):
            await app.next_update_async()
        window = omni.appwindow.get_default_app_window()
        actual = (window.get_width(), window.get_height())
        requested = (config["width"], config["height"])
        if actual != requested:
            raise RuntimeError(f"Framebuffer {actual} differs from requested {requested}")
        status = {"ready": True, "no_window": True, "stage": context.get_stage_url(),
                  "framebuffer": list(actual), "viewer_url": f"http://localhost:{config['http_port']}"}
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        print("[Smart Port Headless] Ready:", json.dumps(status))
    except Exception as exc:
        status_path.write_text(json.dumps({"ready": False, "error": str(exc)}), encoding="utf-8")
        traceback.print_exc()
        app.post_quit(1)


asyncio.ensure_future(start())
