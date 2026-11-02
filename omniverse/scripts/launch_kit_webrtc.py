"""Launch the installed Smart Port Composer as a windowless WebRTC server."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_command(kit_root: Path, width: int, height: int, fps: int,
                  http_port: int, signal_port: int, stream_port: int) -> list[str]:
    release = kit_root / "_build/windows-x86_64/release"
    bootstrap = PROJECT_ROOT / "omniverse/scripts/start_headless_webrtc.py"
    return [
        str(release / "kit/kit.exe"),
        str(release / "apps/smartport.rtg_composer.kit"),
        "--no-window",
        "--ext-folder", str(PROJECT_ROOT / "omniverse/extensions"),
        "--exec", f'"{bootstrap.as_posix()}"',
        "--/app/content/emptyStageOnStart=true",
        "--/app/window/enabled=false",
        "--/app/window/scaleToMonitor=false",
        "--/app/window/dpiScaleOverride=1.0",
        f"--/app/window/width={width}",
        f"--/app/window/height={height}",
        f"--/app/renderer/resolution/width={width}",
        f"--/app/renderer/resolution/height={height}",
        "--/app/renderer/skipWhileMinimized=false",
        "--/app/viewport/defaults/tickRate=0",
        "--/persistent/app/viewport/defaults/tickRate=0",
        "--/app/viewport/outline/enabled=false",
        "--/app/viewport/createCameraModelRep=false",
        "--/app/viewport/defaults/hud/visible=false",
        "--/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=false",
        f"--/exts/omni.kit.livestream.app/primaryStream/width={width}",
        f"--/exts/omni.kit.livestream.app/primaryStream/height={height}",
        f"--/exts/omni.kit.livestream.app/spectatorStream/0/width={width}",
        f"--/exts/omni.kit.livestream.app/spectatorStream/0/height={height}",
        f"--/exts/omni.kit.livestream.app/primaryStream/signalPort={signal_port}",
        f"--/exts/omni.kit.livestream.app/primaryStream/streamPort={stream_port}",
        f"--/exts/omni.kit.livestream.app/primaryStream/targetFps={fps}",
        *[f"--/exts/omni.kit.livestream.app/spectatorStream/{i}/{key}={value}"
          for i in range(3) for key,value in {'streamType':'webrtc','signalPort':49200+i*10,'streamPort':48000+i*2,'targetFps':30}.items()],
        "--/smartport/webrtc/browserAgentOnly=true",
        f"--/exts/omni.services.transport.server.http/port={http_port}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit-root", type=Path,
                        default=Path(os.environ.get("SMARTPORT_KIT_ROOT", "D:/Software/kit-app-template")))
    parser.add_argument("--stage", type=Path, default=PROJECT_ROOT / "omniverse/scenes/smart_port.usda")
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--http-port", type=int, default=8011)
    parser.add_argument("--signal-port", type=int, default=49100)
    parser.add_argument("--stream-port", type=int, default=47998)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if min(args.width, args.height, args.fps) <= 0 or args.width % 2 or args.height % 2:
        parser.error("Width and height must be positive even numbers; FPS must be positive")
    if any(not 1 <= port <= 65535 for port in (args.http_port, args.signal_port, args.stream_port)):
        parser.error("Ports must be between 1 and 65535")
    command = build_command(args.kit_root.resolve(), args.width, args.height, args.fps,
                            args.http_port, args.signal_port, args.stream_port)
    for path in (Path(command[0]), Path(command[1]), args.stage):
        if not path.is_file():
            parser.error(f"Required file does not exist: {path}")
    if args.dry_run:
        print(json.dumps({"command": command, "stage": str(args.stage.resolve()),
                          "viewer_url": f"http://localhost:{args.http_port}"}, indent=2))
        return 0
    # Refuse to steal the existing stream or discard an open USD session.
    for port in (args.http_port, args.signal_port):
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(f"Port {port} is already in use. Save and close the running Kit app first.")
    run_dir = PROJECT_ROOT / ".cache/webrtc-headless" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True)
    status_path = run_dir / "status.json"
    config = {"stage": str(args.stage.resolve()), "width": args.width, "height": args.height,
              "target_fps": args.fps, "http_port": args.http_port,
              "signal_port": args.signal_port, "stream_port": args.stream_port,
              "status_path": str(status_path)}
    environment = dict(os.environ, SMARTPORT_HEADLESS_CONFIG=json.dumps(config))
    with (run_dir / "kit.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=Path(command[0]).parents[1], env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result = {"pid": process.pid, "viewer_url": f"http://localhost:{args.http_port}",
              "log": str(run_dir / "kit.log"), "status": str(status_path)}
    (PROJECT_ROOT / ".cache/webrtc-headless/latest.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
