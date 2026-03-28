"""Camera + detector hub: threaded capture, MJPEG browser stream."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np

from config import (
    CAMERA_INDEX,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    MJPEG_JPEG_QUALITY,
    MJPEG_PORT,
    VISION_CONF_THRESHOLD,
    VISION_IOU_THRESHOLD,
    VISION_TARGET_CLASSES,
    YOLO_MODEL_NAME,
)
from vision.detector import Detection, Detector


class VisionStream:
    def __init__(
        self,
        camera_index: int,
        detector: Detector,
        frame_width: int = FRAME_WIDTH,
        frame_height: int = FRAME_HEIGHT,
        jpeg_quality: int = MJPEG_JPEG_QUALITY,
        mjpeg_port: int = MJPEG_PORT,
        target_classes: list[str] | None = None,
    ) -> None:
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
        self.detector = detector
        self._lock = threading.Lock()
        self._latest_detections: list[Detection] = []
        self._latest_jpeg: bytes | None = None
        self._latest_frame: np.ndarray | None = None
        self._fps: float = 0.0
        self._running = False
        self._started = False
        self._jpeg_quality = jpeg_quality
        self._mjpeg_port = mjpeg_port
        self._capture_thread: threading.Thread | None = None
        self._mjpeg_thread: threading.Thread | None = None
        self._http_server: HTTPServer | None = None
        self._ema_alpha = 2.0 / (30 + 1)  # EWMA ~30 frames
        self._prev_frame_time: float | None = None
        self._target_classes = set(target_classes) if target_classes is not None else set(VISION_TARGET_CLASSES)

    def start(self) -> None:
        """Start capture+detection thread and MJPEG server thread (idempotent)."""
        if self._started:
            return
        self._started = True
        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        self._mjpeg_thread = threading.Thread(target=self._run_mjpeg_server, daemon=True)
        self._mjpeg_thread.start()

    def _capture_loop(self) -> None:
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            detections = self.detector.detect(frame)
            annotated = self.detector.draw(frame, detections, target_classes=self._target_classes)
            now = time.perf_counter()
            if self._prev_frame_time is not None:
                dt = max(now - self._prev_frame_time, 1e-9)
                instant_fps = 1.0 / dt
                if self._fps <= 0.0:
                    fps = instant_fps
                else:
                    fps = self._ema_alpha * instant_fps + (1.0 - self._ema_alpha) * self._fps
            else:
                fps = 0.0
            self._prev_frame_time = now
            cv2.putText(
                annotated,
                f"FPS: {fps:.1f}",
                (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 255),
                2,
            )
            ok_enc, buf = cv2.imencode(
                ".jpg",
                annotated,
                [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality],
            )
            jpeg_bytes = buf.tobytes() if ok_enc else None
            with self._lock:
                self._latest_detections = detections
                self._latest_jpeg = jpeg_bytes
                self._latest_frame = frame.copy()
                self._fps = fps

    def get_detections(self) -> list[Detection]:
        with self._lock:
            return list(self._latest_detections)

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def get_fps(self) -> float:
        with self._lock:
            return self._fps

    def _run_mjpeg_server(self) -> None:
        server = HTTPServer(("0.0.0.0", self._mjpeg_port), MJPEGHandler)
        server.vision_stream = self  # type: ignore[attr-defined]
        self._http_server = server
        server.serve_forever()

    def stop(self) -> None:
        """Stop capture loop and release the camera."""
        self._running = False
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=3.0)
        if self._http_server is not None:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
            self._http_server = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class MJPEGHandler(BaseHTTPRequestHandler):
    """Serves annotated camera feed as MJPEG; uses ``server.vision_stream``."""

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        stream: VisionStream = self.server.vision_stream  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            jpeg = stream.get_jpeg()
            if jpeg is None:
                time.sleep(0.05)
                continue
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
            except BrokenPipeError:
                break
            time.sleep(0.033)


def start_mjpeg_server(vision_stream: VisionStream, port: int = MJPEG_PORT) -> None:
    """Create HTTPServer, attach vision_stream, block with serve_forever (use in a thread)."""
    server = HTTPServer(("0.0.0.0", port), MJPEGHandler)
    server.vision_stream = vision_stream  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    detector = Detector(
        model_name=YOLO_MODEL_NAME,
        conf_threshold=VISION_CONF_THRESHOLD,
        iou_threshold=VISION_IOU_THRESHOLD,
    )
    vision = VisionStream(CAMERA_INDEX, detector, FRAME_WIDTH, FRAME_HEIGHT)
    vision.start()
    print(f"MJPEG stream at http://0.0.0.0:{MJPEG_PORT}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        vision.stop()
