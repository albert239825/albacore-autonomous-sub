#!/usr/bin/env python3
"""USB serial smoke test: Teensy CMD in + telemetry out (AUD filtered by default).

Run from repo root with venv active. Close PlatformIO Serial Monitor first.

Examples::

    export TEENSY_SERIAL_PORT=/dev/cu.usbmodem192773701   # macOS
    python scripts/teensy_serial_smoke.py --seconds 5

    python scripts/teensy_serial_smoke.py --port /dev/cu.usbmodem192773701 --watchdog-test

Defaults for port/baud come from ``jetson.config`` unless overridden or ``TEENSY_SERIAL_PORT`` is set.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import serial
except ImportError as e:
    raise SystemExit("Install pyserial: pip install pyserial") from e


def _resolve_port(cli_port: Optional[str]) -> str:
    if cli_port:
        return cli_port
    env = os.environ.get("TEENSY_SERIAL_PORT")
    if env:
        return env
    from jetson.config import CONTROL_SERIAL_PORT

    return CONTROL_SERIAL_PORT


def _resolve_baud(cli_baud: Optional[int]) -> int:
    if cli_baud is not None:
        return cli_baud
    from jetson.config import CONTROL_BAUD

    return CONTROL_BAUD


def _should_print_line(raw: bytes, *, show_aud: bool, aud_every: int, aud_counter: list[int]) -> bool:
    if not raw:
        return False
    try:
        s = raw.decode("ascii", errors="replace").strip()
    except Exception:
        return False
    if not s:
        return False
    if s.startswith("AUD,"):
        if not show_aud:
            return False
        aud_counter[0] += 1
        return aud_counter[0] % aud_every == 0
    return True


def run_heartbeat(
    ser: serial.Serial,
    *,
    cmd_line: bytes,
    hz: float,
    seconds: float,
    show_aud: bool,
    aud_every: int,
) -> None:
    period = 1.0 / max(hz, 1e-6)
    t_end = time.time() + seconds
    next_send = time.time()
    aud_counter = [0]
    imu_count = 0
    while time.time() < t_end:
        now = time.time()
        if now >= next_send:
            ser.write(cmd_line)
            ser.flush()
            next_send += period
        raw = ser.readline()
        if _should_print_line(raw, show_aud=show_aud, aud_every=aud_every, aud_counter=aud_counter):
            line = raw.decode("ascii", errors="replace").strip()
            print(line)
            if line.startswith("IMU,"):
                imu_count += 1
        else:
            time.sleep(0.001)
    print(f"[smoke] Done. IMU lines seen: {imu_count}", file=sys.stderr)


def run_watchdog_test(ser: serial.Serial, *, cmd_line: bytes, silence_s: float, read_after_s: float) -> None:
    print("[smoke] Sending one CMD, then silence to allow watchdog...", file=sys.stderr)
    ser.write(cmd_line)
    ser.flush()
    time.sleep(silence_s)
    t_end = time.time() + read_after_s
    imu_count = 0
    dbg_wd = 0
    aud_counter = [0]
    while time.time() < t_end:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("ascii", errors="replace").strip()
        if line.startswith("IMU,"):
            imu_count += 1
        if line.startswith("DBG,WD"):
            dbg_wd += 1
        if line.startswith("AUD,"):
            aud_counter[0] += 1
            continue
        print(line)
    print(
        f"[smoke] After silence: IMU lines={imu_count}, DBG,WD={dbg_wd} (flash firmware with SERIAL_DEBUG=1 to see WD)",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Teensy USB serial smoke test.")
    parser.add_argument("--port", default=None, help="Serial device (default: TEENSY_SERIAL_PORT or jetson config).")
    parser.add_argument("--baud", type=int, default=None, help="Baud (default: CONTROL_BAUD from jetson config).")
    parser.add_argument(
        "--cmd",
        default="CMD,10,0,0,0,0",
        help="Command line to send (must end with newline internally).",
    )
    parser.add_argument("--hz", type=float, default=20.0, help="CMD heartbeat rate while in heartbeat mode.")
    parser.add_argument("--seconds", type=float, default=5.0, help="How long to run heartbeat mode.")
    parser.add_argument("--show-aud", action="store_true", help="Print AUD lines (very noisy).")
    parser.add_argument(
        "--aud-every",
        type=int,
        default=5000,
        help="If --show-aud, print one AUD line every N AUD samples.",
    )
    parser.add_argument(
        "--watchdog-test",
        action="store_true",
        help="Send one CMD then stay silent; read telemetry (watchdog should not hang the Teensy).",
    )
    parser.add_argument("--silence-s", type=float, default=0.8, help="Silence duration before read (watchdog test).")
    parser.add_argument("--read-after-s", type=float, default=2.0, help="Read duration after silence.")
    args = parser.parse_args()

    port = _resolve_port(args.port)
    baud = _resolve_baud(args.baud)
    cmd_line = args.cmd.strip().encode("ascii", errors="ignore")
    if not cmd_line.endswith(b"\n"):
        cmd_line += b"\n"

    print(f"[smoke] Opening {port} @ {baud}", file=sys.stderr)
    ser = serial.Serial(port, baud, timeout=0.05)
    time.sleep(0.3)
    try:
        if args.watchdog_test:
            run_watchdog_test(ser, cmd_line=cmd_line, silence_s=args.silence_s, read_after_s=args.read_after_s)
        else:
            run_heartbeat(
                ser,
                cmd_line=cmd_line,
                hz=args.hz,
                seconds=args.seconds,
                show_aud=args.show_aud,
                aud_every=max(1, args.aud_every),
            )
    finally:
        ser.close()
        print("[smoke] Closed.", file=sys.stderr)


if __name__ == "__main__":
    main()
