"""Threaded audio stream reader for 4-channel Teensy audio data."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from comms.protocol import AudMsg
from comms.serial_comms import SerialComms


@dataclass(slots=True)
class AudioFrame:
    ch0: int
    ch1: int
    ch2: int
    ch3: int
    ts: float


class AudioStreamReader:
    def __init__(self, link: SerialComms, max_frames: int = 100000) -> None:
        self.link = link
        self.queue: "queue.Queue[AudioFrame]" = queue.Queue(maxsize=max_frames)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self.link.read_message()
            if isinstance(msg, AudMsg):
                frame = AudioFrame(msg.ch0, msg.ch1, msg.ch2, msg.ch3, time.time())
                try:
                    self.queue.put_nowait(frame)
                except queue.Full:
                    try:
                        _ = self.queue.get_nowait()
                        self.queue.put_nowait(frame)
                    except queue.Empty:
                        pass
            else:
                time.sleep(0.0005)

    def get_chunk(self, n_samples: int, timeout_s: float = 1.0) -> Optional[np.ndarray]:
        frames: list[AudioFrame] = []
        end = time.time() + timeout_s
        while len(frames) < n_samples and time.time() < end:
            wait_s = max(0.0, end - time.time())
            try:
                frame = self.queue.get(timeout=min(0.02, wait_s))
                frames.append(frame)
            except queue.Empty:
                pass
        if len(frames) < n_samples:
            return None

        arr = np.zeros((4, n_samples), dtype=np.float32)
        for i, frame in enumerate(frames):
            arr[0, i] = frame.ch0
            arr[1, i] = frame.ch1
            arr[2, i] = frame.ch2
            arr[3, i] = frame.ch3
        return arr


if __name__ == "__main__":
    from comms.mock_comms import MockComms

    mock = MockComms("audio")
    mock.connect()

    class _Adapter:
        def __init__(self, inner: MockComms) -> None:
            self.inner = inner

        def read_message(self):  # noqa: ANN201
            return self.inner.read_message()

    stream = AudioStreamReader(_Adapter(mock))  # type: ignore[arg-type]
    stream.start()
    for _ in range(5):
        chunk = stream.get_chunk(1024, timeout_s=0.5)
        if chunk is None:
            print("No chunk available.")
            continue
        print("Chunk shape:", chunk.shape, "mean:", float(np.mean(chunk)))
    stream.stop()
    mock.close()
