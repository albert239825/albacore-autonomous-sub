"""Detection capture and forwarding to the laptop backend."""

from __future__ import annotations

import time

from http_sender import HttpSender
from vision.tracker import TrackState


class DetectionCapture:
    """Capture confirmed targets and POST annotated JPEG frames."""

    def __init__(
        self,
        sender: HttpSender,
        backend_url: str,
        cooldown_s: float = 10.0,
        confidence_threshold: float = 0.5,
        confirm_frames: int = 15,
    ) -> None:
        self._sender = sender
        self.backend_url = backend_url.rstrip("/")
        self.cooldown_s = cooldown_s
        self.confidence_threshold = confidence_threshold
        self.confirm_frames = max(1, confirm_frames)
        self.last_capture_time = 0.0
        self.consecutive_frames = 0
        self.capture_count = 0

    def update(self, track_state: TrackState, jpeg_bytes: bytes | None) -> bool:
        """Update capture gating and enqueue an upload when confirmed."""
        if (
            not track_state.is_tracking
            or track_state.confidence < self.confidence_threshold
            or jpeg_bytes is None
        ):
            self.consecutive_frames = 0
            return False

        self.consecutive_frames += 1
        if self.consecutive_frames < self.confirm_frames:
            return False

        now = time.time()
        if (now - self.last_capture_time) < self.cooldown_s:
            return False

        self._post_detection(
            jpeg_bytes=jpeg_bytes,
            class_name=track_state.class_name,
            confidence=track_state.confidence,
        )
        self.last_capture_time = now
        self.capture_count += 1
        return True

    def _post_detection(self, jpeg_bytes: bytes, class_name: str, confidence: float) -> None:
        self._sender.post_form(
            f"{self.backend_url}/api/ingest/detection",
            files={"file": ("capture.jpg", jpeg_bytes, "image/jpeg")},
            data={"class_name": class_name, "confidence": f"{confidence:.6f}"},
            timeout=2.0,
        )
