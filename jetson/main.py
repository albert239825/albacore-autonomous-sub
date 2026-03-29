"""Jetson orchestrator: UDP from laptop, single Teensy serial link, optional vision.

Runs a fixed-rate loop (~``MAIN_LOOP_HZ``) that (1) drains Teensy telemetry and
forwards it to the laptop dashboard, (2) applies laptop ``CMD`` / ``MODE`` /
``ESTOP``, (3) selects ``CMD`` from manual input (COMMS-only mode) or future
nav modules, and (4) sends the result to the Teensy. Use ``--mock`` for
``MockComms`` (no hardware).

Dashboard UDP: laptop sends to ``UDP_LISTEN_PORT``; first packet sets relay
address to ``(client_ip, UDP_LISTEN_PORT + 1)`` so telemetry and ``STATE`` lines
match ``laptop/controller.py`` binding on port 5006.

Vision: ``VisionStream`` + ``Tracker`` feed ``AUTO_TRACK`` via ``target_follow.compute``.
Audio TDOA remains optional / not wired here.
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
from comms.protocol import (
    AudMsg,
    BatMsg,
    CmdMsg,
    DepMsg,
    EStopMsg,
    ImuMsg,
    ModeMsg,
    ParsedMessage,
    StateMsg,
    UssMsg,
    clamp_cmd,
    serialize,
)
from comms.serial_comms import SerialComms
from config import (
    BACKEND_PORT,
    CAMERA_INDEX,
    CAPTURE_CONFIDENCE_THRESHOLD,
    CAPTURE_CONFIRM_FRAMES,
    CAPTURE_COOLDOWN_S,
    CONTROL_BAUD,
    CONTROL_SERIAL_PORT,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    LAPTOP_IP,
    MAIN_LOOP_DT,
    MAIN_LOOP_HZ,
    MJPEG_PORT,
    TRACKER_ACQUIRE_FRAMES,
    TRACKER_LOST_HOLD_FRAMES,
    TRACKER_LOST_STOP_FRAMES,
    TRACKER_SMOOTHING_ALPHA,
    TELEMETRY_PUSH_INTERVAL,
    UDP_LISTEN_HOST,
    UDP_LISTEN_PORT,
    VISION_CONF_THRESHOLD,
    VISION_IOU_THRESHOLD,
    VISION_TARGET_CLASSES,
    YOLO_MODEL_NAME,
)
from http_sender import HttpSender
from nav.target_follow import compute as target_follow_compute
from vision.capture import DetectionCapture
from vision.detector import Detector
from vision.stream import VisionStream
from vision.tracker import TrackState, Tracker


class Mode(str, Enum):
    """Operating mode; laptop sends ``MODE,<name>`` to switch."""

    MANUAL = "MANUAL"
    AUTO_WAYPOINT = "AUTO_WAYPOINT"
    AUTO_TRACK = "AUTO_TRACK"


MAX_CTRL_MSGS_PER_LOOP = 256
MAX_AUD_QUEUE = 4096


@dataclass(slots=True)
class ControlState:
    """Shared state between the main loop and worker threads (vision updates track)."""
    mode: Mode = Mode.MANUAL
    manual_cmd: CmdMsg = field(default_factory=lambda: CmdMsg(0, 0, 0, 0, 0))
    estop: bool = False
    latest_track: TrackState = field(default_factory=TrackState)
    latest_bearing_deg: float = 0.0
    latest_det_class: str = "none"
    latest_det_conf: float = 0.0
    latest_imu: Optional[ImuMsg] = None
    latest_uss: Optional[UssMsg] = None
    latest_bat: Optional[BatMsg] = None
    latest_dep: Optional[DepMsg] = None


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


def vision_thread_fn(
    vision: VisionStream,
    out_q: "queue.Queue[TrackState]",
    stop: threading.Event,
    capture: Optional[DetectionCapture] = None,
) -> None:
    """Tracker on top of ``VisionStream`` detections; pushes latest ``TrackState`` (drop-oldest if full)."""
    tracker = Tracker(
        smoothing_alpha=TRACKER_SMOOTHING_ALPHA,
        acquire_frames=TRACKER_ACQUIRE_FRAMES,
        lost_hold_frames=TRACKER_LOST_HOLD_FRAMES,
        lost_stop_frames=TRACKER_LOST_STOP_FRAMES,
        target_classes=list(VISION_TARGET_CLASSES),
    )
    while not stop.is_set():
        detections = vision.get_detections()
        track = tracker.update(detections)
        if capture is not None:
            capture.update(track, vision.get_jpeg())
        try:
            out_q.put_nowait(track)
        except queue.Full:
            try:
                out_q.get_nowait()
            except queue.Empty:
                pass
            try:
                out_q.put_nowait(track)
            except queue.Full:
                pass
        time.sleep(0.05)


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
    vision_q: "queue.Queue[TrackState]" = queue.Queue(maxsize=2)
    state = ControlState()

    detector = Detector(
        model_name=YOLO_MODEL_NAME,
        conf_threshold=VISION_CONF_THRESHOLD,
        iou_threshold=VISION_IOU_THRESHOLD,
        target_classes=list(VISION_TARGET_CLASSES),
    )
    vision = VisionStream(
        camera_index=CAMERA_INDEX,
        detector=detector,
        frame_width=FRAME_WIDTH,
        frame_height=FRAME_HEIGHT,
        mjpeg_port=MJPEG_PORT,
        target_classes=list(VISION_TARGET_CLASSES),
    )
    vision.start()
    sender = HttpSender()
    capture = DetectionCapture(
        sender=sender,
        backend_url=f"http://{LAPTOP_IP}:{BACKEND_PORT}",
        cooldown_s=CAPTURE_COOLDOWN_S,
        confidence_threshold=CAPTURE_CONFIDENCE_THRESHOLD,
        confirm_frames=CAPTURE_CONFIRM_FRAMES,
    )

    vision_stop = threading.Event()
    vision_t = threading.Thread(
        target=vision_thread_fn,
        args=(vision, vision_q, vision_stop, capture),
        daemon=True,
    )

    threads = [
        threading.Thread(target=udp_reader_thread, args=(udp_sock, udp_q, stop), daemon=True),
        threading.Thread(target=serial_reader_thread, args=(control_link, ctrl_q, aud_q, stop), daemon=True),
    ]
    for t in threads:
        t.start()
    vision_t.start()

    print(f"Jetson main loop at {MAIN_LOOP_HZ:.1f}Hz, mock={use_mock}, MJPEG :{MJPEG_PORT}")
    last_telemetry_push = 0.0
    try:
        while True:
            loop_start = time.time()

            # 1) Drain control Teensy telemetry; forward to dashboard
            for _ in range(MAX_CTRL_MSGS_PER_LOOP):
                try:
                    msg = ctrl_q.get_nowait()
                except queue.Empty:
                    break
                if isinstance(msg, ImuMsg):
                    state.latest_imu = msg
                elif isinstance(msg, UssMsg):
                    state.latest_uss = msg
                elif isinstance(msg, BatMsg):
                    state.latest_bat = msg
                elif isinstance(msg, DepMsg):
                    state.latest_dep = msg
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

            # Latest vision result (AUTO_TRACK + HUD)
            while True:
                try:
                    track = vision_q.get_nowait()
                except queue.Empty:
                    break
                state.latest_track = track
                if track.is_tracking:
                    state.latest_det_class = track.class_name
                    state.latest_det_conf = track.confidence
                else:
                    state.latest_det_class = "none"
                    state.latest_det_conf = 0.0

            # 4–6) Mode-dependent command (ESTOP overrides)
            if state.estop:
                out_cmd = CmdMsg(0, 0, 0, 0, -1)
                state.mode = Mode.MANUAL
                state.estop = False
            elif state.mode == Mode.MANUAL:
                out_cmd = state.manual_cmd
            elif state.mode == Mode.AUTO_TRACK:
                out_cmd = target_follow_compute(state.latest_track, FRAME_WIDTH, FRAME_HEIGHT)
            else:
                # AUTO_WAYPOINT not yet implemented; fall back to manual
                out_cmd = state.manual_cmd

            # Push compact telemetry to laptop backend at 5Hz.
            now = time.time()
            if now - last_telemetry_push >= TELEMETRY_PUSH_INTERVAL:
                last_telemetry_push = now
                imu = state.latest_imu
                uss = state.latest_uss
                bat = state.latest_bat
                dep = state.latest_dep
                sender.post_json(
                    f"http://{LAPTOP_IP}:{BACKEND_PORT}/api/ingest/telemetry",
                    {
                        "imu": (
                            {
                                "ax": imu.ax,
                                "ay": imu.ay,
                                "az": imu.az,
                                "gx": imu.gx,
                                "gy": imu.gy,
                                "gz": imu.gz,
                            }
                            if imu is not None
                            else {}
                        ),
                        "uss": (
                            {
                                "top": uss.top_cm,
                                "left": uss.left_cm,
                                "right": uss.right_cm,
                                "front": uss.front_cm,
                            }
                            if uss is not None
                            else {}
                        ),
                        "bat": ({"voltage": bat.voltage} if bat is not None else {}),
                        "dep": ({"depth_m": dep.depth_m} if dep is not None else {}),
                        "cmd": {
                            "thruster": out_cmd.thruster_pct,
                            "bow": out_cmd.bow_pct,
                            "rudder": out_cmd.rudder_deg,
                            "elevator": out_cmd.elevator_deg,
                            "ballast": out_cmd.ballast_dir,
                        },
                        "mode": state.mode.value,
                    },
                    timeout=0.5,
                )

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
        vision_stop.set()
        vision_t.join(timeout=2.0)
        vision.stop()
        sender.close()
        control_link.close()
        udp_sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Albacore Jetson orchestrator.")
    parser.add_argument("--mock", action="store_true", help="Use mock comms instead of real serial ports.")
    args = parser.parse_args()
    main(args.mock)
