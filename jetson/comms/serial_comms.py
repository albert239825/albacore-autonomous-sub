"""Blocking line I/O over USB serial to the Teensy.

Uses pyserial with a short read timeout so ``readline`` / ``read_message`` do not
block indefinitely—call from a dedicated reader thread and drain often. One
``SerialComms`` instance for the single Teensy link (CMD out; IMU/USS/BAT/DEP/AUD in).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from .protocol import CmdMsg, ParsedMessage, parse_line, serialize


@dataclass(slots=True)
class SerialStats:
    """Lightweight counters for debugging link health."""

    rx_lines: int = 0
    tx_lines: int = 0
    parse_failures: int = 0
    last_rx_time: float = 0.0
    last_tx_time: float = 0.0


class SerialComms:
    """Open a serial port, send ``CMD`` lines, read parsed telemetry/audio lines."""

    def __init__(self, port: str, baud: int, timeout_s: float = 0.01) -> None:
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self.ser: Any = None
        self.stats = SerialStats()

    def connect(self) -> None:
        """Open the port and flush OS buffers."""
        import serial as serial_mod

        self.ser = serial_mod.Serial(self.port, self.baud, timeout=self.timeout_s)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def close(self) -> None:
        if self.ser is not None and self.ser.is_open:
            self.ser.close()

    def send_line(self, line: str) -> None:
        """Send a UTF-8 line (``serialize()`` appends the newline)."""
        if not self.is_connected():
            return
        assert self.ser is not None
        self.ser.write(line.encode("ascii", errors="ignore"))
        self.stats.tx_lines += 1
        self.stats.last_tx_time = time.time()

    def send_cmd(self, cmd: CmdMsg) -> None:
        """Serialize and send a ``CMD`` message."""
        self.send_line(serialize(cmd))

    def read_raw_line(self) -> Optional[str]:
        """Read one newline-terminated line, or ``None`` if none available."""
        if not self.is_connected():
            return None
        assert self.ser is not None
        raw = self.ser.readline()
        if not raw:
            return None
        try:
            line = raw.decode("ascii", errors="ignore").strip()
        except UnicodeDecodeError:
            return None
        if not line:
            return None
        self.stats.rx_lines += 1
        self.stats.last_rx_time = time.time()
        return line

    def read_message(self) -> Optional[ParsedMessage]:
        """Parse a line into a typed message; malformed lines increment ``parse_failures``."""
        line = self.read_raw_line()
        if line is None:
            return None
        msg = parse_line(line)
        if msg is None:
            self.stats.parse_failures += 1
        return msg


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Simple serial comms smoke test.")
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=1_000_000)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    link = SerialComms(args.port, args.baud, timeout_s=0.01)
    try:
        link.connect()
        print(f"Connected: {args.port} @ {args.baud}")
        end = time.time() + args.seconds
        while time.time() < end:
            link.send_cmd(CmdMsg(0, 0, 0, 0, 0))
            msg = link.read_message()
            if msg is not None:
                print(msg)
            time.sleep(0.05)
        print("Stats:", link.stats)
    finally:
        link.close()
        print("Closed.")
