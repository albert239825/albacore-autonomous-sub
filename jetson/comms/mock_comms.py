"""Mock serial-like comms interface for hardware-free development."""

from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from .protocol import AudMsg, BatMsg, CmdMsg, DepMsg, ImuMsg, ParsedMessage, UssMsg, parse_line, serialize


@dataclass(slots=True)
class MockAudioConfig:
    sample_rate_hz: int = 20_000
    tone_hz: float = 500.0
    amplitude: float = 1700.0
    offsets: tuple[int, int, int, int] = (0, 2, 4, 6)
    propeller_noise_mode: bool = False


class MockComms:
    """Duck-typed replacement for SerialComms with the same main methods."""

    def __init__(self, role: str, telemetry_hz: float = 20.0, audio_config: Optional[MockAudioConfig] = None) -> None:
        self.role = role
        self.telemetry_hz = telemetry_hz
        self.audio_config = audio_config or MockAudioConfig()
        self.connected = False
        self._line_buffer: Deque[str] = deque(maxlen=5000)
        self._start = time.time()
        self._last_telemetry = 0.0
        self._last_audio = 0.0
        self._battery_v = 12.6
        self._cmd = CmdMsg(0, 0, 0, 0)
        self._sample_idx = 0

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def send_line(self, line: str) -> None:
        msg = parse_line(line)
        if isinstance(msg, CmdMsg):
            self._cmd = msg

    def send_cmd(self, cmd: CmdMsg) -> None:
        self._cmd = cmd

    def _enqueue_control_telemetry(self) -> None:
        now = time.time()
        t = now - self._start
        yaw_rate = 5.0 * math.sin(0.3 * t)
        imu = ImuMsg(
            ax=0.1 * math.sin(0.2 * t),
            ay=0.1 * math.cos(0.15 * t),
            az=9.81 + 0.05 * math.sin(0.5 * t),
            gx=0.5 * math.sin(0.4 * t),
            gy=0.5 * math.cos(0.35 * t),
            gz=yaw_rate,
        )

        near_front = random.random() < 0.07
        uss = UssMsg(
            top_cm=random.randint(60, 200),
            left_cm=random.randint(30, 200),
            right_cm=random.randint(30, 200),
            front_cm=random.randint(20, 50) if near_front else random.randint(60, 220),
        )

        self._battery_v = max(11.0, self._battery_v - 0.001 * (1.0 / self.telemetry_hz))
        bat = BatMsg(self._battery_v)

        dep = DepMsg(depth_m=max(0.0, 1.0 + 0.2 * math.sin(0.1 * t)))

        for msg in (imu, uss, bat, dep):
            self._line_buffer.append(serialize(msg))

    def _enqueue_audio_samples(self) -> None:
        dt = 1.0 / float(self.audio_config.sample_rate_hz)
        now = time.time()
        elapsed = now - self._last_audio
        if self._last_audio <= 0:
            elapsed = dt
        sample_count = max(1, int(elapsed * self.audio_config.sample_rate_hz))
        self._last_audio = now

        for _ in range(sample_count):
            base_phase = 2.0 * math.pi * self.audio_config.tone_hz * (self._sample_idx * dt)
            ch_vals = []
            for offset in self.audio_config.offsets:
                delayed_phase = 2.0 * math.pi * self.audio_config.tone_hz * ((self._sample_idx - offset) * dt)
                tone = 2048 + self.audio_config.amplitude * math.sin(delayed_phase)
                if self.audio_config.propeller_noise_mode:
                    tone += random.uniform(-120.0, 120.0)
                    if random.random() < 0.005:
                        tone += random.uniform(-500.0, 500.0)
                ch_vals.append(int(max(0, min(4095, tone))))
            self._sample_idx += 1
            _ = base_phase
            self._line_buffer.append(serialize(AudMsg(ch_vals[0], ch_vals[1], ch_vals[2], ch_vals[3])))

    def _pump(self) -> None:
        if not self.connected:
            return
        now = time.time()
        if self.role == "control":
            period = 1.0 / self.telemetry_hz
            if (now - self._last_telemetry) >= period:
                self._last_telemetry = now
                self._enqueue_control_telemetry()
        elif self.role == "audio":
            self._enqueue_audio_samples()

    def read_raw_line(self) -> Optional[str]:
        self._pump()
        if not self._line_buffer:
            return None
        return self._line_buffer.popleft().strip()

    def read_message(self) -> Optional[ParsedMessage]:
        line = self.read_raw_line()
        if line is None:
            return None
        return parse_line(line)


if __name__ == "__main__":
    control = MockComms("control", telemetry_hz=20.0)
    audio = MockComms("audio", audio_config=MockAudioConfig(propeller_noise_mode=True))
    control.connect()
    audio.connect()
    control.send_cmd(CmdMsg(30, -5, 0, 1))
    start = time.time()
    while time.time() - start < 1.5:
        msg_c = control.read_message()
        if msg_c is not None:
            print("CONTROL:", msg_c)
        msg_a = audio.read_message()
        if msg_a is not None and isinstance(msg_a, AudMsg):
            print("AUDIO:", msg_a)
        time.sleep(0.01)
