"""Simple GPS waypoint navigation using heading PID."""

from __future__ import annotations

import math
from dataclasses import dataclass

from comms.protocol import CmdMsg
from config import WAYPOINT_CRUISE_THRUSTER, WAYPOINT_KD, WAYPOINT_KI, WAYPOINT_KP, WAYPOINT_STOP_RADIUS_M


def _wrap_deg(a: float) -> float:
    return ((a + 180.0) % 360.0) - 180.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


@dataclass(slots=True)
class WaypointNavigator:
    kp: float = WAYPOINT_KP
    ki: float = WAYPOINT_KI
    kd: float = WAYPOINT_KD
    integral: float = 0.0
    prev_error: float = 0.0

    def compute(
        self,
        current_lat: float,
        current_lon: float,
        current_heading_deg: float,
        target_lat: float,
        target_lon: float,
        dt: float,
    ) -> CmdMsg:
        dist_m = _haversine_m(current_lat, current_lon, target_lat, target_lon)
        if dist_m <= WAYPOINT_STOP_RADIUS_M:
            return CmdMsg(0, 0, 0, 0)

        desired_heading = _bearing_deg(current_lat, current_lon, target_lat, target_lon)
        err = _wrap_deg(desired_heading - current_heading_deg)
        self.integral += err * dt
        deriv = (err - self.prev_error) / max(1e-6, dt)
        self.prev_error = err

        rudder = int(max(-45, min(45, self.kp * err + self.ki * self.integral + self.kd * deriv)))
        return CmdMsg(WAYPOINT_CRUISE_THRUSTER, rudder, 0, 0)


if __name__ == "__main__":
    nav = WaypointNavigator()
    lat, lon = 39.9500, -75.1700
    heading = 20.0
    target_lat, target_lon = 39.9510, -75.1680
    dt = 0.1
    for step in range(20):
        cmd = nav.compute(lat, lon, heading, target_lat, target_lon, dt)
        heading = (heading + cmd.rudder_deg * 0.05) % 360.0
        lat += 0.00002
        lon += 0.00003
        print(f"step={step:02d} heading={heading:6.2f} cmd={cmd}")
