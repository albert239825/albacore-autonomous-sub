"""Quick serial debug utility for the single Teensy link."""

from __future__ import annotations

import argparse
import time

from jetson.config import CONTROL_BAUD
from jetson.comms.protocol import CmdMsg
from jetson.comms.serial_comms import SerialComms


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple serial debug utility.")
    parser.add_argument("--port", default="/dev/ttyTHS1")
    parser.add_argument("--baud", type=int, default=CONTROL_BAUD)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    link = SerialComms(args.port, args.baud)
    link.connect()
    print(f"Connected to {args.port} @ {args.baud}")
    end = time.time() + args.seconds
    try:
        while time.time() < end:
            link.send_cmd(CmdMsg(0, 0, 0, 0, 0))
            msg = link.read_message()
            if msg is not None:
                print(msg)
            time.sleep(0.05)
    finally:
        link.close()
        print("Closed.")


if __name__ == "__main__":
    main()
