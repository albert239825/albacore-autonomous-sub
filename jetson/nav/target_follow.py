"""Target-follow controller from bbox geometry to CMD outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from comms.protocol import CmdMsg
from config import (
    TARGET_DESIRED_AREA_RATIO,
    TARGET_FOLLOW_BOW_KP,
    TARGET_FOLLOW_LOW_THRUSTER_THRESHOLD,
    TARGET_FOLLOW_RUDDER_KP,
    TARGET_FOLLOW_THRUSTER_KP,
    TARGET_HOLD_THRUSTER,
    TARGET_LOST_FRAMES_HOLD,
    TARGET_LOST_FRAMES_STOP,
)


class BboxObservation(Protocol):
    """Duck type: matches ``vision.detector.Detection`` without importing OpenCV stack."""

    class_name: str
    confidence: float
    x: float
    y: float
    w: float
    h: float


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(v))))


@dataclass(slots=True)
class TargetFollowController:
    frame_width: int
    frame_height: int
    desired_area_ratio: float = TARGET_DESIRED_AREA_RATIO
    lost_frames: int = 0
    last_rudder: int = 0
    last_bow: int = 0
    last_thruster: int = 0

    def update(self, detection: Optional[BboxObservation]) -> CmdMsg:
        if detection is None:
            self.lost_frames += 1
            if self.lost_frames <= TARGET_LOST_FRAMES_HOLD:
                if self.last_thruster < TARGET_FOLLOW_LOW_THRUSTER_THRESHOLD:
                    return CmdMsg(TARGET_HOLD_THRUSTER, self.last_bow, 0, 0, 0)
                return CmdMsg(TARGET_HOLD_THRUSTER, 0, self.last_rudder, 0, 0)
            if self.lost_frames <= TARGET_LOST_FRAMES_STOP:
                if self.last_thruster < TARGET_FOLLOW_LOW_THRUSTER_THRESHOLD:
                    return CmdMsg(10, self.last_bow, 0, 0, 0)
                return CmdMsg(10, 0, self.last_rudder, 0, 0)
            return CmdMsg(0, 0, 0, 0, 0)

        self.lost_frames = 0
        x, y, w, h = detection.x, detection.y, detection.w, detection.h
        _ = y
        x_error = (x - (self.frame_width / 2.0)) / max(1.0, self.frame_width)
        area = max(1.0, w * h)
        desired_area = self.desired_area_ratio * (self.frame_width * self.frame_height)
        size_error = (desired_area - area) / max(1.0, desired_area)

        thruster = _clamp(TARGET_FOLLOW_THRUSTER_KP * size_error, -100, 100)
        self.last_thruster = thruster

        if thruster < TARGET_FOLLOW_LOW_THRUSTER_THRESHOLD:
            bow = _clamp(TARGET_FOLLOW_BOW_KP * x_error, -100, 100)
            rudder = 0
            self.last_bow = bow
            self.last_rudder = 0
        else:
            bow = 0
            rudder = _clamp(TARGET_FOLLOW_RUDDER_KP * x_error, -45, 45)
            self.last_bow = 0
            self.last_rudder = rudder

        return CmdMsg(thruster, bow, rudder, 0, 0)


@dataclass(slots=True)
class _SimDet:
    """For ``__main__`` demo only (same fields as ``vision.detector.Detection``)."""

    class_name: str
    confidence: float
    x: float
    y: float
    w: float
    h: float


if __name__ == "__main__":
    controller = TargetFollowController(frame_width=640, frame_height=480)
    dets: list[Optional[_SimDet]] = [
        _SimDet("boat", 0.9, 100, 220, 40, 40),
        _SimDet("boat", 0.9, 220, 220, 50, 50),
        _SimDet("boat", 0.9, 320, 240, 65, 65),
        _SimDet("boat", 0.9, 480, 240, 80, 80),
        None,
        None,
        None,
        None,
        None,
    ]
    for idx, det in enumerate(dets):
        cmd = controller.update(det)
        print(f"frame={idx:02d} detection={det is not None} cmd={cmd}")
