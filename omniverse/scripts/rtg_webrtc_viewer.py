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
<link rel="stylesheet" href="/static/smartport-agent.css?v=4">
<script type="module" src="/static/smartport-agent.js?v=5"></script>
<script type="module" src="/static/smartport-navigation.js?v=1"></script>
"""


def build_viewer_page(stock_html: str) -> str:
    """Keep the vendor's input and streaming implementation unchanged."""
    replacements = {
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
    for name in ("smartport-agent.js", "smartport-agent.css", "smartport-navigation.js"):
        (web_root / name).write_bytes((assets / name).read_bytes())
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", required=True, type=Path)
    args = parser.parse_args()
    print("Installed viewport-only web client:", install_viewer_page(args.web_root))
