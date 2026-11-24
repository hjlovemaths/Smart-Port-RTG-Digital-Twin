"""Serve the Agent's selected RTSP cameras as same-origin JPEG previews."""

import threading
import time
from uuid import uuid4


class CameraFeed:
    def __init__(self, camera, reader_type):
        self._lock = threading.Lock()
        self._closed = False
        self._lease_until = 0.0
        self._retry_at = 0.0
        self._latest = (0.0, None)
        self._reader = reader_type(camera, self._encode, lambda status: None, active=self._active)

    def _active(self):
        return not self._closed and time.monotonic() < self._lease_until

    def _encode(self, frame):
        now = time.monotonic()
        if now - self._latest[0] < 0.1 or not self._active():
            return
        import cv2

        rgba, _, _ = frame
        bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        ok, jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self._latest = (now, jpeg.tobytes())

    def frame(self):
        with self._lock:
            if self._closed:
                return None
            now = time.monotonic()
            self._lease_until = now + 5.0
            if not self._reader.running and now >= self._retry_at:
                self._latest = (0.0, None)
                self._reader.start()
                self._retry_at = now + 3.0
            captured, jpeg = self._latest
            return jpeg if now - captured < 2.0 else None

    def close(self):
        with self._lock:
            self._closed = True
            self._reader.stop(wait=False)


class CameraPreview:
    def __init__(self, display, cameras, reader_type):
        self.display = display
        self.revision = uuid4().hex
        self.cameras = [
            {"number": number, "location": camera.location if camera else "", "configured": camera is not None}
            for number, camera in cameras.items()
        ]
        self.feeds = {number: CameraFeed(camera, reader_type) for number, camera in cameras.items() if camera}

    def state(self):
        return {"display": self.display, "revision": self.revision, "cameras": self.cameras}

    def close(self):
        for feed in self.feeds.values():
            feed.close()


def install_camera_routes(app, prefix, current_preview, execute):
    from fastapi import HTTPException
    from starlette.requests import Request
    from starlette.responses import Response

    async def live_camera_frame(request: Request, revision: str, number: int):
        session = request.scope.get("smartport_session", {})
        if session.get("role") != "admin":
            raise HTTPException(403, "仅管理员可查看实体相机")
        preview = current_preview()
        if not preview or revision != preview.revision or number not in preview.feeds:
            raise HTTPException(404, "相机预览已关闭")
        frame = preview.feeds[number].frame()
        headers = {"Cache-Control": "private, no-store"}
        if frame is None:
            return Response(status_code=204, headers=headers)
        return Response(frame, media_type="image/jpeg", headers=headers)

    async def live_camera_close(request: Request, payload: dict):
        if request.scope.get("smartport_session", {}).get("role") != "admin":
            raise HTTPException(403, "仅管理员可关闭实体相机")
        return await execute("live-camera-close", payload)

    app.add_api_route(prefix + "/live-camera/{revision}/{number}", live_camera_frame, methods=["GET"])
    app.add_api_route(prefix + "/live-camera/close", live_camera_close, methods=["POST"])
