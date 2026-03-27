"""ASCII line protocol: parse and serialize Teensy + laptop messages.

Wire format: ``MSG_TYPE,field1,field2,...\\n`` (newline-terminated). Used for:

- Jetson ↔ Control Teensy serial (``CMD`` out; ``IMU``, ``USS``, ``BAT``, ``DEP`` in).
- Audio Teensy → Jetson (``AUD``).
- Laptop ↔ Jetson UDP (``CMD``, ``MODE``, ``ESTOP`` in; relayed telemetry + ``STATE`` out).

``clamp_cmd`` enforces actuator limits before sending ``CMD`` to firmware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(slots=True)
class CmdMsg:
    """Actuator command: thruster %, fin angles (deg), ballast (-1 ascend, 0 stop, 1 descend)."""

    thruster_pct: int
    rudder_deg: int
    elevator_deg: int
    ballast_dir: int


@dataclass(slots=True)
class ImuMsg:
    """IMU: accel (m/s²) then gyro (deg/s), axis order x, y, z."""

    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float


@dataclass(slots=True)
class UssMsg:
    """Ultrasonic ranges in cm (top, left, right, front)."""

    top_cm: int
    left_cm: int
    right_cm: int
    front_cm: int


@dataclass(slots=True)
class BatMsg:
    """Battery voltage (V) from Teensy ADC / divider."""

    voltage: float


@dataclass(slots=True)
class DepMsg:
    """Depth below surface (m); 0.0 if sensor absent or not initialized."""

    depth_m: float


@dataclass(slots=True)
class AudMsg:
    """One 12-bit ADC sample per hydrophone channel (0–4095)."""

    ch0: int
    ch1: int
    ch2: int
    ch3: int


@dataclass(slots=True)
class ModeMsg:
    """High-level Jetson mode from laptop (e.g. MANUAL, AUTO_WAYPOINT, AUTO_TRACK)."""

    mode: str


@dataclass(slots=True)
class EStopMsg:
    """Emergency stop: firmware maps to kill thrust + ballast ascend."""

    active: bool = True


@dataclass(slots=True)
class StateMsg:
    """Dashboard line: mode, last vision class/confidence, TDOA bearing (deg)."""

    mode: str
    det_class: str
    det_conf: float
    bearing_deg: float


ParsedMessage = Union[CmdMsg, ImuMsg, UssMsg, BatMsg, DepMsg, AudMsg, ModeMsg, EStopMsg, StateMsg]


def serialize(msg: ParsedMessage) -> str:
    """Return a single ASCII line including trailing newline."""
    if isinstance(msg, CmdMsg):
        return f"CMD,{msg.thruster_pct},{msg.rudder_deg},{msg.elevator_deg},{msg.ballast_dir}\n"
    if isinstance(msg, ImuMsg):
        return f"IMU,{msg.ax:.4f},{msg.ay:.4f},{msg.az:.4f},{msg.gx:.4f},{msg.gy:.4f},{msg.gz:.4f}\n"
    if isinstance(msg, UssMsg):
        return f"USS,{msg.top_cm},{msg.left_cm},{msg.right_cm},{msg.front_cm}\n"
    if isinstance(msg, BatMsg):
        return f"BAT,{msg.voltage:.3f}\n"
    if isinstance(msg, DepMsg):
        return f"DEP,{msg.depth_m:.3f}\n"
    if isinstance(msg, AudMsg):
        return f"AUD,{msg.ch0},{msg.ch1},{msg.ch2},{msg.ch3}\n"
    if isinstance(msg, ModeMsg):
        return f"MODE,{msg.mode}\n"
    if isinstance(msg, EStopMsg):
        return "ESTOP\n"
    if isinstance(msg, StateMsg):
        return f"STATE,{msg.mode},{msg.det_class},{msg.det_conf:.3f},{msg.bearing_deg:.2f}\n"
    raise TypeError(f"Unsupported message type: {type(msg)}")


def parse_line(line: str) -> Optional[ParsedMessage]:
    """Parse one stripped line; return ``None`` if empty or unrecognized."""
    line = line.strip()
    if not line:
        return None
    fields = line.split(",")
    msg_type = fields[0]
    try:
        if msg_type == "CMD" and len(fields) == 5:
            return CmdMsg(int(fields[1]), int(fields[2]), int(fields[3]), int(fields[4]))
        if msg_type == "IMU" and len(fields) == 7:
            return ImuMsg(*(float(v) for v in fields[1:7]))
        if msg_type == "USS" and len(fields) == 5:
            return UssMsg(*(int(v) for v in fields[1:5]))
        if msg_type == "BAT" and len(fields) == 2:
            return BatMsg(float(fields[1]))
        if msg_type == "DEP" and len(fields) == 2:
            return DepMsg(float(fields[1]))
        if msg_type == "AUD" and len(fields) == 5:
            return AudMsg(*(int(v) for v in fields[1:5]))
        if msg_type == "MODE" and len(fields) == 2:
            return ModeMsg(fields[1])
        if msg_type == "ESTOP":
            return EStopMsg(True)
        if msg_type == "STATE" and len(fields) == 5:
            return StateMsg(fields[1], fields[2], float(fields[3]), float(fields[4]))
    except ValueError:
        return None
    return None


def clamp_cmd(msg: CmdMsg) -> CmdMsg:
    """Clamp ``CMD`` fields to firmware-allowed ranges before TX."""
    return CmdMsg(
        thruster_pct=max(-100, min(100, msg.thruster_pct)),
        rudder_deg=max(-45, min(45, msg.rudder_deg)),
        elevator_deg=max(-45, min(45, msg.elevator_deg)),
        ballast_dir=max(-1, min(1, msg.ballast_dir)),
    )


if __name__ == "__main__":
    raw_lines = [
        "CMD,35,-10,0,1\n",
        "IMU,0.1,-0.2,9.8,0.4,0.2,-0.1\n",
        "USS,120,80,75,200\n",
        "BAT,12.34\n",
        "DEP,1.25\n",
        "AUD,100,120,130,140\n",
        "MODE,MANUAL\n",
        "ESTOP\n",
        "STATE,MANUAL,boat,0.89,23.4\n",
    ]
    for raw in raw_lines:
        parsed = parse_line(raw)
        print(f"IN:  {raw.strip()}")
        print(f"OUT: {parsed}")
        if parsed is not None:
            print(f"SER: {serialize(parsed).strip()}")
        print("-" * 40)
