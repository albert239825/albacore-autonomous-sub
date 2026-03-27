"""Target-follow controller from bbox geometry to CMD outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from comms.protocol import CmdMsg
from config import (
    TARGET_DESIRED_AREA_RATIO,
    TARGET_FOLLOW_RUDDER_KP,
    TARGET_FOLLOW_THRUSTER_KP,
    TARGET_HOLD_THRUSTER,
    TARGET_LOST_FRAMES_HOLD,
    TARGET_LOST_FRAMES_STOP,
)
from vision.detector import Detection


def _clamp(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(v))))


@dataclass(slots=True)
class TargetFollowController:
    frame_width: int
    frame_height: int
    desired_area_ratio: float = TARGET_DESIRED_AREA_RATIO
    lost_frames: int = 0
    last_rudder: int = 0

    def update(self, detection: Optional[Detection]) -> CmdMsg:
        if detection is None:
            self.lost_frames += 1
            if self.lost_frames <= TARGET_LOST_FRAMES_HOLD:
                return CmdMsg(TARGET_HOLD_THRUSTER, self.last_rudder, 0, 0)
            if self.lost_frames <= TARGET_LOST_FRAMES_STOP:
                return CmdMsg(10, self.last_rudder, 0, 0)
            return CmdMsg(0, 0, 0, 0)

        self.lost_frames = 0
        x, y, w, h = detection.xywh
        _ = y
        x_error = (x - (self.frame_width / 2.0)) / max(1.0, self.frame_width)
        area = max(1.0, w * h)
        desired_area = self.desired_area_ratio * (self.frame_width * self.frame_height)
        size_error = (desired_area - area) / max(1.0, desired_area)

        rudder = _clamp(TARGET_FOLLOW_RUDDER_KP * x_error, -45, 45)
        thruster = _clamp(TARGET_FOLLOW_THRUSTER_KP * size_error, -100, 100)
        self.last_rudder = rudder
        return CmdMsg(thruster, rudder, 0, 0)


if __name__ == "__main__":
    controller = TargetFollowController(frame_width=640, frame_height=480)
    dets = [
        Detection("boat", 0.9, (100, 220, 40, 40)),
        Detection("boat", 0.9, (220, 220, 50, 50)),
        Detection("boat", 0.9, (320, 240, 65, 65)),
        Detection("boat", 0.9, (480, 240, 80, 80)),
        None,
        None,
        None,
        None,
        None,
    ]
    for idx, det in enumerate(dets):
        cmd = controller.update(det)
        print(f"frame={idx:02d} detection={det is not None} cmd={cmd}")
