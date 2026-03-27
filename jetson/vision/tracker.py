"""Simple target tracker with bbox smoothing and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .detector import Detection


@dataclass(slots=True)
class TrackState:
    cls_name: str = "none"
    confidence: float = 0.0
    xywh: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    age_frames: int = 0
    missing_frames: int = 0


class DetectionTracker:
    def __init__(self, alpha: float = 0.4, max_missing_frames: int = 10) -> None:
        self.alpha = alpha
        self.max_missing_frames = max_missing_frames
        self.state = TrackState()

    def _smooth_xywh(
        self, prev: tuple[float, float, float, float], cur: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        a = self.alpha
        return tuple(a * c + (1.0 - a) * p for p, c in zip(prev, cur))  # type: ignore[return-value]

    def update(self, detections: list[Detection]) -> Optional[TrackState]:
        if detections:
            best = max(detections, key=lambda d: d.confidence)
            if self.state.age_frames == 0:
                self.state.xywh = best.xywh
            else:
                self.state.xywh = self._smooth_xywh(self.state.xywh, best.xywh)
            self.state.cls_name = best.cls_name
            self.state.confidence = best.confidence
            self.state.age_frames += 1
            self.state.missing_frames = 0
            return self.state

        self.state.missing_frames += 1
        if self.state.missing_frames > self.max_missing_frames:
            self.state = TrackState()
            return None
        return self.state


if __name__ == "__main__":
    tracker = DetectionTracker(alpha=0.3, max_missing_frames=4)
    sequence: list[list[Detection]] = [
        [Detection("boat", 0.9, (100, 100, 80, 50))],
        [Detection("boat", 0.88, (108, 102, 82, 52))],
        [Detection("boat", 0.86, (120, 106, 85, 54))],
        [],
        [],
        [Detection("boat", 0.8, (140, 111, 88, 55))],
    ]
    for i, frame_dets in enumerate(sequence):
        state = tracker.update(frame_dets)
        print(f"frame={i} state={state}")
