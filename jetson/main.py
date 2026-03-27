"""Jetson orchestrator: UDP from laptop, two USB serial links, optional vision.

Runs a fixed-rate loop (~``MAIN_LOOP_HZ``) that (1) drains control Teensy
telemetry and forwards it to the laptop dashboard, (2) pulls audio chunks for
TDOA bearing, (3) applies laptop ``CMD`` / ``MODE`` / ``ESTOP``, (4) selects
``CMD`` from manual input, waypoint nav, or target-follow, and (5) sends the
result to the control Teensy. Use ``--mock`` to swap real serial for
``MockComms`` (no hardware).

Dashboard UDP: laptop sends to ``UDP_LISTEN_PORT``; first packet sets relay
address to ``(client_ip, UDP_LISTEN_PORT + 1)`` so telemetry and ``STATE`` lines
match ``laptop/controller.py`` binding on port 5006.
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

import numpy as np

from audio.stream import AudioStreamReader
from audio.tdoa import estimate_bearing
from comms.mock_comms import MockComms
from comms.protocol import CmdMsg, EStopMsg, ModeMsg, ParsedMessage, StateMsg, clamp_cmd, serialize
from comms.serial_comms import SerialComms
from config import (
    AUDIO_BAUD,
    AUDIO_SERIAL_PORT,
    CONTROL_BAUD,
    CONTROL_SERIAL_PORT,
    MAIN_LOOP_DT,
    MAIN_LOOP_HZ,
    UDP_LISTEN_HOST,
    UDP_LISTEN_PORT,
)
from nav.target_follow import TargetFollowController
from nav.waypoint import WaypointNavigator
from vision.detector import Detection, YoloDetector
from vision.tracker import DetectionTracker


class Mode(str, Enum):
    """Operating mode; laptop sends ``MODE,<name>`` to switch."""

    MANUAL = "MANUAL"
    AUTO_WAYPOINT = "AUTO_WAYPOINT"
    AUTO_TRACK = "AUTO_TRACK"


@dataclass(slots=True)
class ControlState:
    """Shared state between the main loop and worker threads (vision updates detection)."""
    mode: Mode = Mode.MANUAL
    manual_cmd: CmdMsg = field(default_factory=lambda: CmdMsg(0, 0, 0, 0))
    estop: bool = False
    latest_detection: Optional[Detection] = None
    latest_bearing_deg: float = 0.0
    latest_det_class: str = "none"
    latest_det_conf: float = 0.0


def make_links(use_mock: bool):
    """Return ``(control_link, audio_link)`` — either ``MockComms`` or ``SerialComms``."""
    if use_mock:
        control = MockComms("control")
        audio = MockComms("audio")
        control.connect()
        audio.connect()
        return control, audio
    control = SerialComms(CONTROL_SERIAL_PORT, CONTROL_BAUD)
    audio = SerialComms(AUDIO_SERIAL_PORT, AUDIO_BAUD)
    control.connect()
    audio.connect()
    return control, audio


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


def serial_reader_thread(link, q: "queue.Queue[ParsedMessage]", stop: threading.Event) -> None:
    """Continuously read from one Teensy link and push parsed messages to a queue."""
    while not stop.is_set():
        msg = link.read_message()
        if msg is not None:
            q.put(msg)
        else:
            time.sleep(0.001)


def vision_thread_fn(
    out_q: "queue.Queue[Optional[Detection]]", stop: threading.Event, camera_index: int = 0
) -> None:
    """YOLO + tracker on camera; pushes best tracked detection (or None) — slower than 20 Hz."""
    import cv2

    detector = YoloDetector()
    tracker = DetectionTracker()
    cap = cv2.VideoCapture(camera_index)
    while not stop.is_set():
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.01)
            continue
        detections = detector.detect(frame)
        state = tracker.update(detections)
        out_q.put(None if state is None else Detection(state.cls_name, state.confidence, state.xywh))
    cap.release()


def main(use_mock: bool) -> None:
    control_link, audio_link = make_links(use_mock)
    audio_reader = AudioStreamReader(audio_link)  # type: ignore[arg-type]
    audio_reader.start()

    # UDP: commands on LISTEN_PORT; telemetry/state sent to laptop on LISTEN_PORT+1
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setblocking(False)
    udp_sock.bind((UDP_LISTEN_HOST, UDP_LISTEN_PORT))
    dashboard_addr: Optional[tuple[str, int]] = None

    stop = threading.Event()
    udp_q: "queue.Queue[tuple[ParsedMessage, tuple[str, int]]]" = queue.Queue()
    ctrl_q: "queue.Queue[ParsedMessage]" = queue.Queue()
    vision_q: "queue.Queue[Optional[Detection]]" = queue.Queue(maxsize=2)
    state = ControlState()

    waypoint_nav = WaypointNavigator()
    follow_ctl = TargetFollowController(frame_width=640, frame_height=480)

    threads = [
        threading.Thread(target=udp_reader_thread, args=(udp_sock, udp_q, stop), daemon=True),
        threading.Thread(target=serial_reader_thread, args=(control_link, ctrl_q, stop), daemon=True),
    ]  # serial_reader: control Teensy only; audio is consumed inside AudioStreamReader
    for t in threads:
        t.start()

    vision_stop = threading.Event()
    vision_t = threading.Thread(target=vision_thread_fn, args=(vision_q, vision_stop), daemon=True)
    try:
        vision_t.start()
    except Exception:
        vision_t = None  # camera/OpenCV optional at import time on headless dev machines

    print(f"Jetson main loop running at {MAIN_LOOP_HZ:.1f}Hz, mock={use_mock}")
    try:
        while True:
            loop_start = time.time()

            # 1) Drain control Teensy telemetry; forward raw lines to dashboard when known
            while True:
                try:
                    msg = ctrl_q.get_nowait()
                except queue.Empty:
                    break
                if dashboard_addr is not None:
                    udp_sock.sendto(serialize(msg).encode("ascii"), dashboard_addr)

            # 2) Short audio window for TDOA (bearing for STATE line)
            chunk = audio_reader.get_chunk(1024, timeout_s=0.001)
            if chunk is not None:
                try:
                    state.latest_bearing_deg = estimate_bearing(
                        chunk[0].astype(np.float64),
                        chunk[1].astype(np.float64),
                        chunk[2].astype(np.float64),
                        chunk[3].astype(np.float64),
                        sample_rate_hz=20000,
                    )
                except Exception:
                    pass

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

            # Latest vision result (AUTO_TRACK)
            while True:
                try:
                    det = vision_q.get_nowait()
                except queue.Empty:
                    break
                state.latest_detection = det
                if det is not None:
                    state.latest_det_class = det.cls_name
                    state.latest_det_conf = det.confidence

            # 4–6) Mode-dependent command (ESTOP overrides)
            if state.estop:
                out_cmd = CmdMsg(0, 0, 0, -1)
                state.mode = Mode.MANUAL
                state.estop = False
            elif state.mode == Mode.MANUAL:
                out_cmd = state.manual_cmd
            elif state.mode == Mode.AUTO_WAYPOINT:
                # TODO: wire GPS + IMU-derived heading from real telemetry
                out_cmd = waypoint_nav.compute(
                    current_lat=39.95,
                    current_lon=-75.17,
                    current_heading_deg=0.0,
                    target_lat=39.951,
                    target_lon=-75.168,
                    dt=MAIN_LOOP_DT,
                )
            else:
                out_cmd = follow_ctl.update(state.latest_detection)

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
        if vision_t is not None:
            vision_stop.set()
            vision_t.join(timeout=1.0)
        audio_reader.stop()
        control_link.close()
        audio_link.close()
        udp_sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Albacore Jetson orchestrator.")
    parser.add_argument("--mock", action="store_true", help="Use mock comms instead of real serial ports.")
    args = parser.parse_args()
    main(args.mock)
