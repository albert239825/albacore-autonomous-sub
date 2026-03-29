"""Poolside manual control: pygame input → UDP to Jetson.

Sends ``CMD,...`` at 20 Hz (keeps the Teensy watchdog fed). Listens on
``port + 1`` (default 5006) for telemetry and ``STATE`` lines relayed from the
Jetson—same protocol as ``jetson/comms/protocol.py``.

Run (from repo root, venv active)::

    python laptop/controller.py --jetson-ip <JETSON_IP> --port 5005

If ``jetson/main.py --mock`` runs on the **same machine** as this script, use
``--jetson-ip 127.0.0.1`` (the default ``172.20.10.10`` will not reach the local
mock and telemetry will stay empty).

Requires a small pygame window to stay focused for keyboard events. Gamepad
wins over keyboard when detected.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from dataclasses import dataclass
from typing import Optional

import pygame


MODES = ["MANUAL", "AUTO_TRACK"]
HUD_TELEMETRY_ORDER = ("IMU", "USS", "BAT", "DEP", "STATE")


@dataclass(slots=True)
class CmdState:
    """Current stick/command snapshot; ``mode`` is cycled with M (Jetson decides behavior)."""

    thruster_pct: int = 0
    bow_pct: int = 0
    rudder_deg: int = 0
    elevator_deg: int = 0
    ballast_dir: int = 0
    mode_idx: int = 0
    estop: bool = False

    @property
    def mode(self) -> str:
        return MODES[self.mode_idx]


def clamp_int(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, int(v))))


def next_mode(idx: int) -> int:
    return (idx + 1) % len(MODES)


def format_cmd(state: CmdState) -> str:
    return (
        f"CMD,{state.thruster_pct},{state.bow_pct},{state.rudder_deg},"
        f"{state.elevator_deg},{state.ballast_dir}\n"
    )


def read_gamepad_command(joy: Optional[pygame.joystick.Joystick]) -> Optional[tuple[int, int, int, int]]:
    """Axes 0/1: rudder + thruster; axis 2: bow; axes 4/5: ballast triggers if present."""
    if joy is None:
        return None
    lx = joy.get_axis(0)
    ly = joy.get_axis(1)
    rx = joy.get_axis(2) if joy.get_numaxes() > 2 else 0.0
    lt = joy.get_axis(4) if joy.get_numaxes() > 4 else 1.0
    rt = joy.get_axis(5) if joy.get_numaxes() > 5 else 1.0

    thruster = clamp_int(-ly * 100.0, -100, 100)
    bow = clamp_int(rx * 100.0, -100, 100)
    rudder = clamp_int(lx * 45.0, -45, 45)
    ballast_dir = 0
    if rt < 0.2:
        ballast_dir = 1
    elif lt < 0.2:
        ballast_dir = -1
    return thruster, bow, rudder, ballast_dir


def update_keyboard(state: CmdState, dt: float) -> None:
    """W/S thrust, J/L bow, A/D rudder, Q/E ballast; release coasts thrust/bow/rudder toward 0."""
    keys = pygame.key.get_pressed()
    ramp = int(100 * dt * 2.0)
    decay = int(100 * dt * 1.5)
    rudder_rate = int(45 * dt * 4.0)

    if keys[pygame.K_w]:
        state.thruster_pct = clamp_int(state.thruster_pct + ramp, -100, 100)
    elif keys[pygame.K_s]:
        state.thruster_pct = clamp_int(state.thruster_pct - ramp, -100, 100)
    else:
        if state.thruster_pct > 0:
            state.thruster_pct = max(0, state.thruster_pct - decay)
        elif state.thruster_pct < 0:
            state.thruster_pct = min(0, state.thruster_pct + decay)

    if keys[pygame.K_j]:
        state.bow_pct = clamp_int(state.bow_pct - ramp, -100, 100)
    elif keys[pygame.K_l]:
        state.bow_pct = clamp_int(state.bow_pct + ramp, -100, 100)
    else:
        if state.bow_pct > 0:
            state.bow_pct = max(0, state.bow_pct - decay)
        elif state.bow_pct < 0:
            state.bow_pct = min(0, state.bow_pct + decay)

    if keys[pygame.K_a]:
        state.rudder_deg = clamp_int(state.rudder_deg - rudder_rate, -45, 45)
    elif keys[pygame.K_d]:
        state.rudder_deg = clamp_int(state.rudder_deg + rudder_rate, -45, 45)
    else:
        if state.rudder_deg > 0:
            state.rudder_deg = max(0, state.rudder_deg - rudder_rate)
        elif state.rudder_deg < 0:
            state.rudder_deg = min(0, state.rudder_deg + rudder_rate)

    state.ballast_dir = 0
    if keys[pygame.K_q]:
        state.ballast_dir = -1
    elif keys[pygame.K_e]:
        state.ballast_dir = 1

def render_status(
    state: CmdState, latest_telemetry: dict[str, str], using_gamepad: bool, target: str
) -> None:
    """Redraw terminal (ANSI clear + home) so the HUD does not scroll."""
    print("\x1b[2J\x1b[H", end="")
    print("ALBACORE CONTROLLER")
    print(f"Target: {target}")
    print(f"Input: {'gamepad' if using_gamepad else 'keyboard'}")
    print(f"Mode: {state.mode}")
    print(
        f"CMD: thr={state.thruster_pct:>4} bow={state.bow_pct:>4} rud={state.rudder_deg:>3} "
        f"elev={state.elevator_deg:>3} bal={state.ballast_dir:>2}"
    )
    print("-" * 72)
    for msg_type in HUD_TELEMETRY_ORDER:
        line = latest_telemetry.get(msg_type)
        if line is not None:
            print(line)
    print("-" * 72)
    print("Keys: W/S thrust, J/L bow, A/D rudder, Q/E ballast, M mode, Space ESTOP, Esc quit")
    sys.stdout.flush()


def run_controller(jetson_ip: str, port: int) -> None:
    """Bind UDP ``port+1``, send commands to ``(jetson_ip, port)``, poll pygame at 60 FPS."""
    pygame.init()
    pygame.joystick.init()
    pygame.display.set_mode((400, 200))
    pygame.display.set_caption("Albacore Controller")
    clock = pygame.time.Clock()

    # Initialize gamepad if present
    joy: Optional[pygame.joystick.Joystick] = None
    if pygame.joystick.get_count() > 0:
        joy = pygame.joystick.Joystick(0)
        joy.init()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    listen_port = port + 1
    sock.bind(("0.0.0.0", listen_port))
    target = (jetson_ip, port)
    state = CmdState()
    latest_telemetry: dict[str, str] = {}
    send_period = 1.0 / 20.0
    last_send = 0.0
    running = True

    while running:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_m:
                    state.mode_idx = next_mode(state.mode_idx)
                    sock.sendto(f"MODE,{state.mode}\n".encode("ascii"), target)
                elif event.key == pygame.K_SPACE:
                    state.estop = True

        gamepad_cmd = read_gamepad_command(joy)
        using_gamepad = gamepad_cmd is not None
        if gamepad_cmd is not None:
            state.thruster_pct, state.bow_pct, state.rudder_deg, state.ballast_dir = gamepad_cmd
            state.elevator_deg = 0
        else:
            update_keyboard(state, dt)
            state.elevator_deg = 0

        now = time.time()
        if now - last_send >= send_period:
            if state.estop:
                sock.sendto(b"ESTOP\n", target)
                state.thruster_pct = 0
                state.bow_pct = 0
                state.rudder_deg = 0
                state.ballast_dir = -1
                state.estop = False
            else:
                sock.sendto(format_cmd(state).encode("ascii"), target)
            last_send = now

        # Unified single-stream mode can include high-rate AUD packets. Drain enough
        # UDP packets each frame and keep only control/status lines for HUD display.
        for _ in range(512):
            try:
                raw, _addr = sock.recvfrom(4096)
            except BlockingIOError:
                break
            line = raw.decode("ascii", errors="ignore").strip()
            if line.startswith("AUD,"):
                continue
            msg_type = line.split(",", 1)[0]
            if msg_type in HUD_TELEMETRY_ORDER:
                latest_telemetry[msg_type] = line

        render_status(state, latest_telemetry, using_gamepad, f"{jetson_ip}:{port}")

    sock.close()
    pygame.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Albacore laptop UDP controller.")
    parser.add_argument(
        "--jetson-ip",
        default="172.20.10.10",
        help="Jetson UDP address. Use 127.0.0.1 when jetson/main.py --mock runs on this machine.",
    )
    parser.add_argument("--port", type=int, default=5005)
    args = parser.parse_args()

    run_controller(args.jetson_ip, args.port)
