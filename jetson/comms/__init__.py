"""Jetson comms package: Teensy serial protocol, real + mock links.

Exports message types and helpers used by ``main.py``, scripts, and tests.
``SerialComms`` talks to hardware; ``MockComms`` mimics the same call pattern
for laptop-only development (``--mock`` on the orchestrator).
"""

from .mock_comms import MockComms
from .protocol import (
    AudMsg,
    BatMsg,
    CmdMsg,
    DepMsg,
    EStopMsg,
    ImuMsg,
    ModeMsg,
    StateMsg,
    UssMsg,
    clamp_cmd,
    parse_line,
    serialize,
)
from .serial_comms import SerialComms

__all__ = [
    "AudMsg",
    "BatMsg",
    "CmdMsg",
    "DepMsg",
    "EStopMsg",
    "ImuMsg",
    "ModeMsg",
    "StateMsg",
    "UssMsg",
    "MockComms",
    "SerialComms",
    "parse_line",
    "serialize",
    "clamp_cmd",
]
