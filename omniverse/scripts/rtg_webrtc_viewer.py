"""Add a video-only default mode to Kit's installed WebRTC web client.

The stock client and SDK stay in place. ``?controls=1`` retains the original
connection controls, while the default page automatically connects at QHD.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "<!-- smartport-video-only-viewer-v1 -->"
VIEWER_HEAD = """
<!-- smartport-video-only-viewer-v1 -->
<script>
  if (new URLSearchParams(location.search).get('controls') !== '1') {
    document.documentElement.classList.add('smartport-video-only');
  }
</script>
<style>
  html.smartport-video-only,
  .smartport-video-only body {
    width: 100%; height: 100%; min-height: 0; margin: 0; padding: 0;
    overflow: hidden; background: #080d12; color: #e5edf5;
  }
  .smartport-video-only .container {
    position: fixed; inset: 0; width: 100%; height: 100%; min-height: 0;
    margin: 0; padding: 0;
  }
  .smartport-video-only h1,
  .smartport-video-only .control-panel { display: none; }
  .smartport-video-only .stream-content {
    position: absolute; inset: 0; width: 100%; height: 100%; min-height: 0;
    margin: 0; padding: 0; background: #080d12;
  }
  .smartport-video-only #remote-video {
    display: block;
    width: min(100vw, calc(100vh * var(--stream-ratio, 1.777777778)));
    height: min(100vh, calc(100vw / var(--stream-ratio, 1.777777778)));
    max-width: none; max-height: none;
    margin: 0; padding: 0; border: 0; object-fit: contain;
  }
  .smartport-video-only #loading-info {
    color: #d7e3ef; background: #101820dd; border-radius: 8px;
    pointer-events: auto; cursor: pointer;
  }
</style>
<script>
  if (new URLSearchParams(location.search).get('spectator') === '1') {
    document.documentElement.classList.add('smartport-spectator');
    const toggleSpectatorFullscreen = () => {
      const operation = document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
      operation.catch(error => console.warn('Fullscreen:', error));
    };
    document.addEventListener('DOMContentLoaded', () => {
      const button = document.createElement('button');
      button.id = 'spectator-fullscreen'; button.type = 'button'; button.textContent = '全屏';
      button.setAttribute('aria-label', '切换观众画面全屏');
      button.style.cssText = 'position:fixed;right:16px;top:16px;z-index:10000;padding:8px 14px;border:1px solid #8395a6;border-radius:6px;background:#18242de6;color:white;cursor:pointer;';
      button.addEventListener('click', toggleSpectatorFullscreen);
      document.addEventListener('fullscreenchange', () => {button.textContent = document.fullscreenElement ? '退出全屏' : '全屏';});
      document.body.appendChild(button);
    });
    for (const event of ['pointerdown','pointerup','pointermove','mousedown','mouseup','mousemove','wheel','dblclick','contextmenu','keydown','keyup']) {
      window.addEventListener(event, e => {
        if (e.target.closest?.('.control-panel,input,select,button') || !document.getElementById('remote-video')) return;
        if (event.startsWith('key') && (e.key === 'F11' || e.key === 'Escape')) { e.stopImmediatePropagation(); return; }
        if (event === 'dblclick') { e.preventDefault(); e.stopImmediatePropagation(); toggleSpectatorFullscreen(); return; }
        if (event.startsWith('key') && (e.ctrlKey || e.metaKey || e.altKey)) { e.stopImmediatePropagation(); return; }
        e.preventDefault(); e.stopImmediatePropagation();
      }, {capture:true, passive:false});
    }
  }
</script>
<link rel="stylesheet" href="/static/smartport-agent.css?v=5">
<script type="module" src="/static/smartport-session.js?v=1"></script>
<script type="module" src="/static/smartport-agent.js?v=7"></script>
<script type="module" src="/static/smartport-navigation.js?v=4"></script>
"""


def build_viewer_page(stock_html: str) -> str:
    """Keep the vendor's input and streaming implementation unchanged."""
    replacements = {
        "fetch('/api/stream-config')": "fetch('/api/stream-config' + (new URLSearchParams(location.search).get('spectator') === '1' ? '?spectator=1' : ''))",
        "</head>": VIEWER_HEAD + "\n</head>",
        "<title>Omniverse Kit WebRTC Streaming</title>": "<title>Smart Port · 港区视图</title>",
        "resolutionSelect.value = '1920x1080';": """const viewOnly = document.documentElement.classList.contains('smartport-video-only');
                const requestedResolution = new URLSearchParams(location.search).get('resolution');
                const validResolution = Object.hasOwn(StreamResolution, requestedResolution);
                resolutionSelect.value = validResolution ? requestedResolution : (viewOnly ? '2560x1440' : '1920x1080');
                if (viewOnly) {
                    remoteVideo.removeAttribute('poster');
                    // The SDK maps input using the element's bounds. Keep
                    // those bounds equal to the visible video, without a
                    // letterbox inside the element that would offset clicks.
                    const fitStream = () => {
                        if (remoteVideo.videoWidth && remoteVideo.videoHeight) {
                            document.documentElement.style.setProperty('--stream-ratio',
                                remoteVideo.videoWidth / remoteVideo.videoHeight);
                        }
                    };
                    remoteVideo.addEventListener('loadedmetadata', fitStream);
                    remoteVideo.addEventListener('resize', fitStream);
                    loadingInfo.textContent = '正在连接港区画面…';
                    loadingInfo.addEventListener('click', () => {
                        if (!isConnected && !connectButton.disabled) connect();
                    });
                }""",
        "console.log('Stream configurations loaded');": """console.log('Stream configurations loaded');
                    const spectator = new URLSearchParams(location.search).get('spectator') === '1';
                    if (spectator) {
                        const index = streamConfigurations.streams.findIndex(s => s.type === 'spectator');
                        if (index < 0) { loadingInfo.textContent = '观众流尚未就绪'; return; }
                        streamSelect.value = String(index);
                        resolutionSelect.disabled = true;
                    }
                    if (viewOnly && new URLSearchParams(location.search).get('autoconnect') !== '0') connect();""",
        """window.addEventListener('beforeunload', () => {
                    if (isConnected) {
                        AppStreamer.terminate();
                    }
                });""": """// Also release a connecting/paused client when its page closes.
                // Otherwise a refresh can leave a pending streaming session.
                let streamReleased = false;
                const releaseStream = () => {
                    if (streamReleased || !(currentController || isConnected || isPaused)) return;
                    streamReleased = true;
                    try {
                        Promise.resolve(AppStreamer.terminate()).catch(error => console.warn('Stream cleanup:', error));
                    } catch (error) { console.warn('Stream cleanup:', error); }
                };
                window.addEventListener('beforeunload', releaseStream);
                window.addEventListener('pagehide', releaseStream);""",
    }
    for anchor, replacement in replacements.items():
        if stock_html.count(anchor) != 1:
            raise ValueError(f"Unsupported WebRTC client template: expected one {anchor!r}")
        stock_html = stock_html.replace(anchor, replacement, 1)
    return stock_html


def install_viewer_page(web_root: Path) -> Path:
    index = web_root / "index.html"
    backup = web_root / "index.smartport-original.html"
    current = index.read_text(encoding="utf-8")
    if MARKER in current:
        stock = backup.read_text(encoding="utf-8")
    else:
        stock = current
    # Validate before touching the installed client or its backup.
    result = build_viewer_page(stock)
    if MARKER not in current:
        backup.write_text(stock, encoding="utf-8")
    if current != result:
        temporary = index.with_suffix(".smartport-tmp")
        temporary.write_text(result, encoding="utf-8")
        temporary.replace(index)
    assets = Path(__file__).resolve().parents[1] / "web"
    for name in ("smartport-agent.js", "smartport-agent.css", "smartport-navigation.js", "smartport-session.js"):
        (web_root / name).write_bytes((assets / name).read_bytes())
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", required=True, type=Path)
    args = parser.parse_args()
    print("Installed viewport-only web client:", install_viewer_page(args.web_root))
