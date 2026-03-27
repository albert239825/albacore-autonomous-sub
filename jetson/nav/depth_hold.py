"""Placeholder depth-hold controller using ballast direction commands."""

from __future__ import annotations

from dataclasses import dataclass

from config import DEPTH_DEADBAND_M, DEPTH_KD, DEPTH_KI, DEPTH_KP


@dataclass(slots=True)
class DepthHoldController:
    kp: float = DEPTH_KP
    ki: float = DEPTH_KI
    kd: float = DEPTH_KD
    deadband_m: float = DEPTH_DEADBAND_M
    integral: float = 0.0
    prev_error: float = 0.0

    def compute(self, current_depth_m: float, target_depth_m: float, dt: float) -> int:
        error = target_depth_m - current_depth_m
        if abs(error) <= self.deadband_m:
            return 0
        self.integral += error * dt
        derivative = (error - self.prev_error) / max(1e-6, dt)
        self.prev_error = error
        u = self.kp * error + self.ki * self.integral + self.kd * derivative
        if u > 0.15:
            return 1
        if u < -0.15:
            return -1
        return 0


if __name__ == "__main__":
    controller = DepthHoldController()
    depth = 0.4
    target = 1.5
    for i in range(20):
        cmd = controller.compute(depth, target, dt=0.1)
        depth += 0.05 * cmd
        print(f"step={i:02d} depth={depth:.2f}m ballast_dir={cmd}")
