"""Stateless target-follow control law (`TrackState` -> `CmdMsg`).

This module contains only control logic; it does not read sensors, run vision, or
manage state across frames. Call `compute()` once per control tick with the latest
tracker output.

For live camera integration testing of this control law (without sending hardware
commands), use `python -m vision.integration_test --with-target-follow`.
"""

from __future__ import annotations

from comms.protocol import CmdMsg
from config import (
    TARGET_FOLLOW_BOW_KP,
    TARGET_FOLLOW_BOW_SPEED_THRESHOLD,
    TARGET_FOLLOW_DESIRED_AREA_RATIO,
    TARGET_FOLLOW_HOLD_THRUSTER,
    TARGET_FOLLOW_LOST_HOLD_FRAMES,
    TARGET_FOLLOW_LOST_STOP_FRAMES,
    TARGET_FOLLOW_RUDDER_KP,
    TARGET_FOLLOW_THRUSTER_KP,
    TARGET_FOLLOW_THRUSTER_MAX_ABS,
)
from vision.tracker import TrackState


def _clamp_i(v: int, lo: int, hi: int) -> int:
    """Clamp integer command values to actuator-safe limits."""
    return max(lo, min(hi, v))


def compute(track: TrackState, frame_w: int, frame_h: int) -> CmdMsg:
    """Convert a tracking estimate into propulsion and steering commands.

    Behavior summary:
    - No track: return full stop.
    - Recently lost: coast forward at hold thrust, straight heading.
    - Tracked target: use bbox area for range (thruster) and horizontal pixel error
      for yaw (bow thruster at low speed, rudder at higher speed).

    TODO: add ballast direction command for vertical control
    """
    if not track.is_tracking:
        return CmdMsg(0, 0, 0, 0, 0)

    thr_max = max(0, min(100, TARGET_FOLLOW_THRUSTER_MAX_ABS))
    hold_thr = max(0, min(thr_max, TARGET_FOLLOW_HOLD_THRUSTER))

    if track.frames_lost > 0:
        if track.frames_lost <= TARGET_FOLLOW_LOST_HOLD_FRAMES:
            return CmdMsg(hold_thr, 0, 0, 0, 0)
        thr = hold_thr - (track.frames_lost - TARGET_FOLLOW_LOST_HOLD_FRAMES) * 3
        return CmdMsg(_clamp_i(max(0, thr), 0, thr_max), 0, 0, 0, 0)

    error_x = (track.x - frame_w / 2.0) / frame_w
    frame_area = float(frame_w * frame_h)
    target_area = max(1e-9, track.w * track.h)
    area_ratio = target_area / frame_area
    size_error = area_ratio - TARGET_FOLLOW_DESIRED_AREA_RATIO
    thruster_pct = _clamp_i(int(-size_error * TARGET_FOLLOW_THRUSTER_KP), -thr_max, thr_max)

    if abs(thruster_pct) <= TARGET_FOLLOW_BOW_SPEED_THRESHOLD:
        bow_pct = _clamp_i(int(error_x * TARGET_FOLLOW_BOW_KP), -100, 100)
        rudder_deg = 0
    else:
        rudder_deg = _clamp_i(int(error_x * TARGET_FOLLOW_RUDDER_KP), -45, 45)
        bow_pct = 0

    return CmdMsg(thruster_pct, bow_pct, rudder_deg, 0, 0)


if __name__ == "__main__":
    fw, fh = 640, 480
    fa = float(fw * fh)

    def area_to_wh(ratio: float) -> tuple[float, float]:
        """Square box with given area / frame_area."""
        side = (ratio * fa) ** 0.5
        return side, side

    def tr(
        x: float,
        y: float,
        area_ratio: float,
        *,
        frames_lost: int = 0,
        is_tracking: bool = True,
    ) -> TrackState:
        w, h = area_to_wh(area_ratio)
        return TrackState(
            x=x,
            y=y,
            w=w,
            h=h,
            confidence=0.9,
            class_name="bottle",
            frames_visible=5,
            frames_lost=frames_lost,
            is_tracking=is_tracking,
        )

    scenarios: list[tuple[str, TrackState]] = [
        ("Target centered (320, 240), area 5%", tr(320.0, 240.0, 0.05)),
        ("Target left (100, 240), area 5%", tr(100.0, 240.0, 0.05)),
        ("Target right (540, 240), area 5%", tr(540.0, 240.0, 0.05)),
        ("Target close (320, 240), area 15%", tr(320.0, 240.0, 0.15)),
        ("Target far (320, 240), area 1%", tr(320.0, 240.0, 0.01)),
        ("Target lost (5 frames)", tr(320.0, 240.0, (80.0 * 60.0) / fa, frames_lost=5)),
        ("Target lost (15 frames)", tr(320.0, 240.0, (80.0 * 60.0) / fa, frames_lost=15)),
        ("Target lost (25 frames)", tr(320.0, 240.0, (80.0 * 60.0) / fa, frames_lost=25)),
        ("No target", TrackState()),
    ]

    for title, tr in scenarios:
        cmd = compute(tr, fw, fh)
        print(f"{title:34} → CMD: thr={cmd.thruster_pct}, bow={cmd.bow_pct}, rud={cmd.rudder_deg}")
