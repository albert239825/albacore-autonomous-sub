"""Jetson orchestrator: UDP from laptop, single Teensy serial link, optional vision.

Runs a fixed-rate loop (~``MAIN_LOOP_HZ``) that (1) drains Teensy telemetry and
forwards it to the laptop dashboard, (2) applies laptop ``CMD`` / ``MODE`` /
``ESTOP``, (3) selects ``CMD`` from manual input (COMMS-only mode) or future
nav modules, and (4) sends the result to the Teensy. Use ``--mock`` for
``MockComms`` (no hardware).

Dashboard UDP: laptop sends to ``UDP_LISTEN_PORT``; first packet sets relay
address to ``(client_ip, UDP_LISTEN_PORT + 1)`` so telemetry and ``STATE`` lines
match ``laptop/controller.py`` binding on port 5006.

**Temporary:** COMMS-ONLY TEST MODE — audio TDOA, vision, nav, and auto modes are
commented out below. Uncomment marked blocks when ready.
"""

from __future__ import annotations

import argparse
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# import numpy as np
#
# from audio.stream import AudioStreamReader
# from audio.tdoa import estimate_bearing
from comms.mock_comms import MockComms
from comms.protocol import AudMsg, CmdMsg, EStopMsg, ModeMsg, ParsedMessage, StateMsg, clamp_cmd, serialize
from comms.serial_comms import SerialComms
from config import CONTROL_BAUD, CONTROL_SERIAL_PORT, MAIN_LOOP_DT, MAIN_LOOP_HZ, UDP_LISTEN_HOST, UDP_LISTEN_PORT

# from nav.target_follow import TargetFollowController
# from nav.waypoint import WaypointNavigator
# from vision.detector import Detection, YoloDetector
# from vision.tracker import DetectionTracker


class Mode(str, Enum):
    """Operating mode; laptop sends ``MODE,<name>`` to switch."""

    MANUAL = "MANUAL"
    AUTO_WAYPOINT = "AUTO_WAYPOINT"
    AUTO_TRACK = "AUTO_TRACK"


MAX_CTRL_MSGS_PER_LOOP = 256
MAX_AUD_QUEUE = 4096


@dataclass(slots=True)
class ControlState:
    """Shared state between the main loop and worker threads (vision updates detection)."""
    mode: Mode = Mode.MANUAL
    manual_cmd: CmdMsg = field(default_factory=lambda: CmdMsg(0, 0, 0, 0, 0))
    estop: bool = False
    # latest_detection: Optional[Detection] = None
    latest_bearing_deg: float = 0.0
    latest_det_class: str = "none"
    latest_det_conf: float = 0.0


def make_link(use_mock: bool) -> SerialComms | MockComms:
    """Return a single ``SerialComms`` or ``MockComms`` instance."""
    if use_mock:
        link: SerialComms | MockComms = MockComms()
        link.connect()
        return link
    ser = SerialComms(CONTROL_SERIAL_PORT, CONTROL_BAUD)
    ser.connect()
    return ser


def udp_reader_thread(
    sock: socket.socket, q: "queue.Queue[tuple[ParsedMessage, tuple[str, int]]]", stop: threading.Event
) -> None:
    """Parse incoming UDP lines; queue ``(message, sender_addr)`` for the main loop."""
    while not stop.is_set():
        try:
            raw, _addr = sock.recvfrom(4096)
        except BlockingIOError:
            time.sleep(0.002)
            continue
        line = raw.decode("ascii", errors="ignore").strip()
        from comms.protocol import parse_line

        msg = parse_line(line)
        if msg is not None:
            q.put((msg, _addr))


def _put_drop_oldest(q: "queue.Queue[ParsedMessage]", msg: ParsedMessage) -> None:
    """Non-blocking put; if full, drop oldest item to keep newest data flowing."""
    try:
        q.put_nowait(msg)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(msg)
    except queue.Full:
        pass


def serial_reader_thread(
    link,
    ctrl_q: "queue.Queue[ParsedMessage]",
    aud_q: "queue.Queue[ParsedMessage]",
    stop: threading.Event,
) -> None:
    """Continuously read one Teensy stream and demux control vs audio messages."""
    while not stop.is_set():
        msg = link.read_message()
        if msg is not None:
            if isinstance(msg, AudMsg):
                _put_drop_oldest(aud_q, msg)
            else:
                ctrl_q.put(msg)
        else:
            time.sleep(0.001)


# def vision_thread_fn(
#     out_q: "queue.Queue[Optional[Detection]]", stop: threading.Event, camera_index: int = 0
# ) -> None:
#     """YOLO + tracker on camera; pushes best tracked detection (or None) — slower than 20 Hz."""
#     import cv2
#
#     detector = YoloDetector()
#     tracker = DetectionTracker()
#     cap = cv2.VideoCapture(camera_index)
#     while not stop.is_set():
#         ok, frame = cap.read()
#         if not ok:
#             time.sleep(0.01)
#             continue
#         detections = detector.detect(frame)
#         state = tracker.update(detections)
#         out_q.put(None if state is None else Detection(state.cls_name, state.confidence, state.xywh))
#     cap.release()


def main(use_mock: bool) -> None:
    control_link = make_link(use_mock)
    # audio_reader = AudioStreamReader(control_link)  # type: ignore[arg-type]
    # audio_reader.start()

    # UDP: commands on LISTEN_PORT; telemetry/state sent to laptop on LISTEN_PORT+1
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setblocking(False)
    udp_sock.bind((UDP_LISTEN_HOST, UDP_LISTEN_PORT))
    dashboard_addr: Optional[tuple[str, int]] = None

    stop = threading.Event()
    udp_q: "queue.Queue[tuple[ParsedMessage, tuple[str, int]]]" = queue.Queue()
    ctrl_q: "queue.Queue[ParsedMessage]" = queue.Queue()
    aud_q: "queue.Queue[ParsedMessage]" = queue.Queue(maxsize=MAX_AUD_QUEUE)
    # vision_q: "queue.Queue[Optional[Detection]]" = queue.Queue(maxsize=2)
    state = ControlState()

    # waypoint_nav = WaypointNavigator()
    # follow_ctl = TargetFollowController(frame_width=640, frame_height=480)

    threads = [
        threading.Thread(target=udp_reader_thread, args=(udp_sock, udp_q, stop), daemon=True),
        threading.Thread(target=serial_reader_thread, args=(control_link, ctrl_q, aud_q, stop), daemon=True),
    ]
    for t in threads:
        t.start()

    # vision_stop = threading.Event()
    # vision_t = threading.Thread(target=vision_thread_fn, args=(vision_q, vision_stop), daemon=True)
    # try:
    #     vision_t.start()
    # except Exception:
    #     vision_t = None
    vision_t = None

    print(f"Jetson main loop (COMMS TEST) at {MAIN_LOOP_HZ:.1f}Hz, mock={use_mock}")
    try:
        while True:
            loop_start = time.time()

            # 1) Drain control Teensy telemetry; forward to dashboard
            for _ in range(MAX_CTRL_MSGS_PER_LOOP):
                try:
                    msg = ctrl_q.get_nowait()
                except queue.Empty:
                    break
                if dashboard_addr is not None:
                    udp_sock.sendto(serialize(msg).encode("ascii"), dashboard_addr)

            # 3) Laptop CMD / MODE / ESTOP; learn dashboard address from sender
            while True:
                try:
                    msg, src_addr = udp_q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(msg, CmdMsg):
                    state.manual_cmd = clamp_cmd(msg)
                elif isinstance(msg, ModeMsg):
                    if msg.mode in Mode.__members__:
                        state.mode = Mode[msg.mode]
                    else:
                        try:
                            state.mode = Mode(msg.mode)
                        except ValueError:
                            pass
                elif isinstance(msg, EStopMsg):
                    state.estop = True
                dashboard_addr = (src_addr[0], UDP_LISTEN_PORT + 1)

            # # Latest vision result (AUTO_TRACK)
            # while True:
            #     try:
            #         det = vision_q.get_nowait()
            #     except queue.Empty:
            #         break
            #     state.latest_detection = det
            #     if det is not None:
            #         state.latest_det_class = det.cls_name
            #         state.latest_det_conf = det.confidence

            # 4–6) Mode-dependent command (ESTOP overrides)
            # COMMS TEST: always forward laptop CMD except ESTOP (auto branches below when enabled).
            if state.estop:
                out_cmd = CmdMsg(0, 0, 0, 0, -1)
                state.mode = Mode.MANUAL
                state.estop = False
            else:
                out_cmd = state.manual_cmd
            # elif state.mode == Mode.MANUAL:
            #     out_cmd = state.manual_cmd
            # elif state.mode == Mode.AUTO_WAYPOINT:
            #     out_cmd = waypoint_nav.compute(
            #         current_lat=39.95,
            #         current_lon=-75.17,
            #         current_heading_deg=0.0,
            #         target_lat=39.951,
            #         target_lon=-75.168,
            #         dt=MAIN_LOOP_DT,
            #     )
            # else:
            #     out_cmd = follow_ctl.update(state.latest_detection)

            # 7) Forward to Control Teensy (watchdog expects periodic CMD)
            control_link.send_cmd(clamp_cmd(out_cmd))

            # 8) Single STATE line for HUD (mode, vision, bearing)
            if dashboard_addr is not None:
                state_line = serialize(
                    StateMsg(
                        mode=state.mode.value,
                        det_class=state.latest_det_class,
                        det_conf=state.latest_det_conf,
                        bearing_deg=state.latest_bearing_deg,
                    )
                )
                udp_sock.sendto(state_line.encode("ascii"), dashboard_addr)

            elapsed = time.time() - loop_start
            sleep_s = max(0.0, MAIN_LOOP_DT - elapsed)
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        stop.set()
        # if vision_t is not None:
        #     vision_stop.set()
        #     vision_t.join(timeout=1.0)
        # audio_reader.stop()
        control_link.close()
        udp_sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Albacore Jetson orchestrator.")
    parser.add_argument("--mock", action="store_true", help="Use mock comms instead of real serial ports.")
    args = parser.parse_args()
    main(args.mock)
