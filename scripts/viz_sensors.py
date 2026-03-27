"""Live terminal sensor dashboard via UDP telemetry stream."""

from __future__ import annotations

import argparse
import socket
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize relayed sensor telemetry over UDP.")
    parser.add_argument("--listen-port", type=int, default=5006)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.listen_port))
    sock.setblocking(False)
    lines: list[str] = []
    print(f"Listening on UDP :{args.listen_port}")
    while True:
        for _ in range(32):
            try:
                raw, _addr = sock.recvfrom(4096)
            except BlockingIOError:
                break
            lines.append(raw.decode("ascii", errors="ignore").strip())
            lines = lines[-20:]

        print("\x1b[2J\x1b[H", end="")
        print("ALBACORE SENSOR DASHBOARD")
        print("-" * 64)
        for line in lines[-12:]:
            print(line)
        print("-" * 64)
        print("Ctrl+C to exit.")
        sys.stdout.flush()
        time.sleep(0.05)


if __name__ == "__main__":
    main()
