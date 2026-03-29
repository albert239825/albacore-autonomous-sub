"""End-to-end vision integration harness (camera -> detector -> tracker).

Run this module to validate tracking behavior with live camera input while also
serving the MJPEG feed from ``VisionStream``.

Example:
    cd jetson && python -m vision.integration_test --log-hz 2

Hardware (Teensy): ``--send-commands`` opens the control serial port and sends
``CMD`` each tick (implies target-follow). Teensy watchdog expects periodic CMD.

    cd jetson && python -m vision.integration_test --send-commands --serial-port /dev/ttyTHS1
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from comms.protocol import CmdMsg, clamp_cmd
from comms.serial_comms import SerialComms
from config import (
    CAMERA_INDEX,
    CONTROL_BAUD,
    CONTROL_SERIAL_PORT,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    TRACKER_ACQUIRE_FRAMES,
    TRACKER_LOST_HOLD_FRAMES,
    TRACKER_LOST_STOP_FRAMES,
    TRACKER_SMOOTHING_ALPHA,
    VISION_CONF_THRESHOLD,
    VISION_IOU_THRESHOLD,
    VISION_TARGET_CLASSES,
    YOLO_MODEL_NAME,
)
from nav.target_follow import compute as target_follow_compute
from vision.detector import Detector
from vision.stream import VisionStream
from vision.tracker import TrackState, Tracker


def _phase(track: TrackState, acquire_frames: int, lost_hold_frames: int, lost_stop_frames: int) -> str:
    if not track.is_tracking:
        if track.frames_visible > 0:
            return f"ACQUIRING({track.frames_visible}/{acquire_frames})"
        return "IDLE"
    if track.frames_lost == 0:
        return "TRACKING"
    if track.frames_lost <= lost_hold_frames:
        return "LOST_HOLD"
    if track.frames_lost <= lost_stop_frames:
        return "LOST_DECAY"
    return "RESET_PENDING"


def _to_json_row(
    ts: float,
    det_count: int,
    fps: float,
    track: TrackState,
    phase: str,
    cmd: CmdMsg | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "t": ts,
        "phase": phase,
        "fps": round(fps, 2),
        "detections": det_count,
        "track": {
            "is_tracking": track.is_tracking,
            "class_name": track.class_name,
            "confidence": round(track.confidence, 4),
            "x": round(track.x, 2),
            "y": round(track.y, 2),
            "w": round(track.w, 2),
            "h": round(track.h, 2),
            "frames_visible": track.frames_visible,
            "frames_lost": track.frames_lost,
        },
    }
    if cmd is not None:
        row["cmd"] = {
            "thruster_pct": cmd.thruster_pct,
            "bow_pct": cmd.bow_pct,
            "rudder_deg": cmd.rudder_deg,
            "elevator_deg": cmd.elevator_deg,
            "ballast_dir": cmd.ballast_dir,
        }
    return row


def run(args: argparse.Namespace) -> None:
    target_classes = args.target_class if args.target_class is not None else list(VISION_TARGET_CLASSES)
    detector = Detector(
        model_name=YOLO_MODEL_NAME,
        conf_threshold=VISION_CONF_THRESHOLD,
        iou_threshold=VISION_IOU_THRESHOLD,
        target_classes=target_classes,
    )
    vision = VisionStream(
        camera_index=args.camera_index,
        detector=detector,
        frame_width=args.frame_width,
        frame_height=args.frame_height,
        mjpeg_port=args.mjpeg_port,
        target_classes=target_classes,
    )
    tracker = Tracker(
        smoothing_alpha=TRACKER_SMOOTHING_ALPHA,
        acquire_frames=TRACKER_ACQUIRE_FRAMES,
        lost_hold_frames=TRACKER_LOST_HOLD_FRAMES,
        lost_stop_frames=TRACKER_LOST_STOP_FRAMES,
        target_classes=target_classes,
    )

    jsonl_fp = None
    if args.jsonl:
        out_path = Path(args.jsonl)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_fp = out_path.open("w", encoding="utf-8")
        print(f"Writing tracker rows to: {out_path}")

    link: Optional[SerialComms] = None
    if args.send_commands:
        link = SerialComms(args.serial_port, args.baud)
        link.connect()
        print(f"Serial CMD out: {args.serial_port} @ {args.baud} (Teensy watchdog: send every loop)")

    use_target_follow = args.with_target_follow or args.send_commands

    vision.start()
    print(f"MJPEG stream: http://localhost:{args.mjpeg_port}")
    print(f"Target classes: {sorted(target_classes) if target_classes else 'ALL'}")
    print("Press Ctrl+C to stop.")

    start = time.time()
    next_log_t = start
    interval = 1.0 / max(0.1, args.log_hz)
    try:
        while True:
            if args.duration_s > 0 and (time.time() - start) >= args.duration_s:
                break

            detections = vision.get_detections()
            track = tracker.update(detections)
            fps = vision.get_fps()
            cmd = (
                target_follow_compute(track, args.frame_width, args.frame_height) if use_target_follow else None
            )
            if link is not None and cmd is not None:
                link.send_cmd(clamp_cmd(cmd))

            now = time.time()
            if now >= next_log_t:
                phase = _phase(
                    track,
                    acquire_frames=tracker.acquire_frames,
                    lost_hold_frames=tracker.lost_hold_frames,
                    lost_stop_frames=tracker.lost_stop_frames,
                )
                line = (
                    f"[{phase:12}] dets={len(detections):2d} fps={fps:5.1f} "
                    f"cls={track.class_name or '-':10} conf={track.confidence:0.2f} "
                    f"x={track.x:6.1f} y={track.y:6.1f} w={track.w:6.1f} h={track.h:6.1f} "
                    f"vis={track.frames_visible:2d} lost={track.frames_lost:2d}"
                )
                if cmd is not None:
                    line += (
                        f" cmd(thr={cmd.thruster_pct:4d} bow={cmd.bow_pct:4d}"
                        f" rud={cmd.rudder_deg:4d})"
                    )
                print(line)
                if jsonl_fp is not None:
                    jsonl_fp.write(json.dumps(_to_json_row(now, len(detections), fps, track, phase, cmd=cmd)) + "\n")
                    jsonl_fp.flush()
                next_log_t = now + interval

            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        vision.stop()
        if link is not None:
            link.send_cmd(clamp_cmd(CmdMsg(0, 0, 0, 0, 0)))
            link.close()
        if jsonl_fp is not None:
            jsonl_fp.close()
        print("Integration test stopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run camera->detector->tracker integration test.")
    parser.add_argument("--camera-index", type=int, default=CAMERA_INDEX, help="OpenCV camera index.")
    parser.add_argument("--frame-width", type=int, default=FRAME_WIDTH, help="Capture width.")
    parser.add_argument("--frame-height", type=int, default=FRAME_HEIGHT, help="Capture height.")
    parser.add_argument("--mjpeg-port", type=int, default=8080, help="Expected MJPEG port to display in logs.")
    parser.add_argument("--log-hz", type=float, default=2.0, help="Console update rate (Hz).")
    parser.add_argument("--duration-s", type=float, default=0.0, help="Run duration in seconds (0 = until Ctrl+C).")
    parser.add_argument(
        "--with-target-follow",
        action="store_true",
        help="Also compute and log target-follow CmdMsg from TrackState each tick.",
    )
    parser.add_argument(
        "--send-commands",
        action="store_true",
        help="Send clamped CMD to Teensy over serial each tick (implies --with-target-follow).",
    )
    parser.add_argument(
        "--serial-port",
        type=str,
        default=CONTROL_SERIAL_PORT,
        help="Serial device for Teensy (default: config.CONTROL_SERIAL_PORT).",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=CONTROL_BAUD,
        help="Serial baud rate (default: config.CONTROL_BAUD).",
    )
    parser.add_argument(
        "--target-class",
        action="append",
        default=None,
        help=(
            "Optional class filter; repeat for multiple classes "
            "(e.g. --target-class bottle). Defaults to config.VISION_TARGET_CLASSES."
        ),
    )
    parser.add_argument("--jsonl", type=str, default="", help="Optional path to write one JSON line per log tick.")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
