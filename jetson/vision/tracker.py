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

    def _det_xywh(self, d: Detection) -> tuple[float, float, float, float]:
        return (d.x, d.y, d.w, d.h)

    def update(self, detections: list[Detection]) -> Optional[TrackState]:
        if detections:
            best = max(detections, key=lambda d: d.confidence)
            cur = self._det_xywh(best)
            if self.state.age_frames == 0:
                self.state.xywh = cur
            else:
                self.state.xywh = self._smooth_xywh(self.state.xywh, cur)
            self.state.cls_name = best.class_name
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
        [Detection(class_name="boat", confidence=0.9, x=100.0, y=100.0, w=80.0, h=50.0)],
        [Detection(class_name="boat", confidence=0.88, x=108.0, y=102.0, w=82.0, h=52.0)],
        [Detection(class_name="boat", confidence=0.86, x=120.0, y=106.0, w=85.0, h=54.0)],
        [],
        [],
        [Detection(class_name="boat", confidence=0.8, x=140.0, y=111.0, w=88.0, h=55.0)],
    ]
    for i, frame_dets in enumerate(sequence):
        state = tracker.update(frame_dets)
        print(f"frame={i} state={state}")
