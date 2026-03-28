"""Target association and smoothing for AUTO_TRACK.

`Tracker` turns per-frame detector outputs into a stable single-target `TrackState`
for navigation. It supports:
- acquisition gating (must be seen for N consecutive frames),
- EMA smoothing on `(x, y, w, h)`,
- missed-detection coasting, and
- automatic reset after a configurable lost-frame timeout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from config import (
    TRACKER_ACQUIRE_FRAMES,
    TRACKER_LOST_HOLD_FRAMES,
    TRACKER_LOST_STOP_FRAMES,
    TRACKER_SMOOTHING_ALPHA,
    VISION_TARGET_CLASSES,
)
from .detector import Detection


@dataclass(slots=True)
class TrackState:
    """Current single-target estimate consumed by navigation."""

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    confidence: float = 0.0
    class_name: str = ""
    frames_visible: int = 0
    frames_lost: int = 0
    is_tracking: bool = False


class Tracker:
    """Track one primary target across frames.

    Selection policy:
    - While tracking: pick nearest detection to current center and accept it if it
      falls inside a distance gate (`max(w, h) * 1.5`).
    - While not tracking: start from highest-confidence detection.
    """

    _MATCH_RADIUS_FACTOR = 1.5

    def __init__(
        self,
        smoothing_alpha: float = TRACKER_SMOOTHING_ALPHA,
        acquire_frames: int = TRACKER_ACQUIRE_FRAMES,
        lost_hold_frames: int = TRACKER_LOST_HOLD_FRAMES,
        lost_stop_frames: int = TRACKER_LOST_STOP_FRAMES,
        target_classes: list[str] | None = None,
    ) -> None:
        """Configure smoothing and acquire/lost thresholds.

        Args:
            smoothing_alpha: EMA blend factor (higher = more responsive, lower = smoother).
            acquire_frames: Consecutive visible frames required before `is_tracking=True`.
            lost_hold_frames: Informational threshold used by downstream control logic.
            lost_stop_frames: Reset tracker after this many consecutive lost frames.
            target_classes: classes eligible for tracking. If omitted, uses
                config.VISION_TARGET_CLASSES. Pass [] to track all classes.
        """
        self.smoothing_alpha = smoothing_alpha
        self.acquire_frames = acquire_frames
        self.lost_hold_frames = lost_hold_frames
        self.lost_stop_frames = lost_stop_frames
        self._target_classes = set(target_classes) if target_classes is not None else set(VISION_TARGET_CLASSES)
        self._s = TrackState()

    @property
    def state(self) -> TrackState:
        """Return a copy of current state (safe from external mutation)."""
        return replace(self._s)

    def reset(self) -> None:
        """Clear state and drop any active/tentative track."""
        self._s = TrackState()

    def _ema(self, prev: float, new: float) -> float:
        a = self.smoothing_alpha
        return a * new + (1.0 - a) * prev

    def _apply_match(self, det: Detection) -> None:
        """Incorporate a matched detection into state with EMA smoothing."""
        s = self._s
        if not s.is_tracking and s.frames_visible == 0:
            s.x, s.y, s.w, s.h = det.x, det.y, det.w, det.h
        else:
            s.x = self._ema(s.x, det.x)
            s.y = self._ema(s.y, det.y)
            s.w = self._ema(s.w, det.w)
            s.h = self._ema(s.h, det.h)
        s.class_name = det.class_name
        s.confidence = det.confidence
        s.frames_lost = 0
        s.frames_visible += 1
        if not s.is_tracking and s.frames_visible >= self.acquire_frames:
            s.is_tracking = True

    def _maybe_reset_lost(self) -> None:
        """Reset once target has been lost beyond `lost_stop_frames`."""
        if self._s.frames_lost > self.lost_stop_frames:
            self.reset()

    def _filtered(self, detections: list[Detection]) -> list[Detection]:
        """Apply class filter before association logic."""
        if not self._target_classes:
            return detections
        return [d for d in detections if d.class_name in self._target_classes]

    def update(self, detections: list[Detection]) -> TrackState:
        """Advance tracker by one frame.

        Args:
            detections: Current frame detections from `vision.detector.Detector`.

        Returns:
            Updated `TrackState` snapshot.
        """
        detections = self._filtered(detections)
        s = self._s

        if s.is_tracking:
            if not detections:
                s.frames_lost += 1
                self._maybe_reset_lost()
                return replace(self._s)

            best_i = min(
                range(len(detections)),
                key=lambda i: math.hypot(detections[i].x - s.x, detections[i].y - s.y),
            )
            det = detections[best_i]
            dist = math.hypot(det.x - s.x, det.y - s.y)
            gate = max(s.w, s.h) * self._MATCH_RADIUS_FACTOR
            if dist <= gate:
                self._apply_match(det)
            else:
                s.frames_lost += 1
                self._maybe_reset_lost()
            return replace(self._s)

        # Not yet tracking — acquire with consecutive frames that have detections
        if not detections:
            self.reset()
            return replace(self._s)

        best = max(detections, key=lambda d: d.confidence)
        self._apply_match(best)
        return replace(self._s)


if __name__ == "__main__":
    # Scripted story: appear → acquire → track + smooth → miss → coast → reappear → eventually full reset
    tracker = Tracker(
        smoothing_alpha=0.4,
        acquire_frames=3,
        lost_hold_frames=8,
        lost_stop_frames=20,
    )
    demo_class = VISION_TARGET_CLASSES[0] if VISION_TARGET_CLASSES else "bottle"

    def det(
        cx: float,
        cy: float,
        w: float = 80.0,
        h: float = 60.0,
        conf: float = 0.9,
        name: str = demo_class,
    ) -> list[Detection]:
        return [Detection(class_name=name, confidence=conf, x=cx, y=cy, w=w, h=h)]

    sequence: list[tuple[str, list[Detection]]] = [
        ("empty", []),
        ("acquire", det(300.0, 240.0)),
        ("acquire", det(305.0, 241.0)),
        ("acquire+lock", det(310.0, 242.0)),
        ("track", det(320.0, 243.0)),
        ("track", det(330.0, 244.0)),
        ("track", det(340.0, 245.0)),
    ]
    for label, dets in sequence:
        st = tracker.update(dets)
        phase = (
            "NOT TRACKING (no detections)"
            if not dets and not st.is_tracking
            else (
                f"ACQUIRING ({st.frames_visible}/{tracker.acquire_frames} frames)"
                if not st.is_tracking and dets
                else f"TRACKING x={st.x:.1f} y={st.y:.1f} w={st.w:.1f} h={st.h:.1f} visible={st.frames_visible} lost={st.frames_lost}"
            )
        )
        print(f"{label:16} {phase}")

    # Coast while lost (same smoothed pose, lost increments)
    print("--- target disappears ---")
    for i in range(25):
        st = tracker.update([])
        if not st.is_tracking and st.frames_lost == 0 and st.frames_visible == 0:
            print(f"Frame lost+{i}: NOT TRACKING (target gone, reset)")
            break
        note = "LOST (nav slowing)" if st.frames_lost == tracker.lost_hold_frames else ""
        print(
            f"Frame lost+{i}: TRACKING x={st.x:.1f} y={st.y:.1f} (coasting, lost={st.frames_lost}) {note}".strip()
        )
