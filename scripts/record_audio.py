"""Record 4-channel audio stream from Teensy (or mock) into WAV."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import scipy.io.wavfile

from jetson.audio.stream import AudioStreamReader
from jetson.comms.mock_comms import MockComms
from jetson.comms.serial_comms import SerialComms
from jetson.config import AUDIO_SAMPLE_RATE_HZ, CONTROL_BAUD, CONTROL_SERIAL_PORT


def main() -> None:
    parser = argparse.ArgumentParser(description="Record audio stream to WAV.")
    parser.add_argument("--output", default="audio_capture.wav")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    if args.mock:
        link = MockComms()
        link.connect()
    else:
        link = SerialComms(CONTROL_SERIAL_PORT, CONTROL_BAUD)
        link.connect()

    reader = AudioStreamReader(link)  # type: ignore[arg-type]
    reader.start()
    n_samples = int(AUDIO_SAMPLE_RATE_HZ * args.seconds)
    chunk = reader.get_chunk(n_samples, timeout_s=max(2.0, args.seconds + 1.0))
    if chunk is None:
        raise RuntimeError("Failed to collect enough audio samples.")

    wav_data = np.clip(chunk.T, 0, 4095).astype(np.int16)
    out = Path(args.output)
    scipy.io.wavfile.write(str(out), AUDIO_SAMPLE_RATE_HZ, wav_data)
    print(f"Saved {wav_data.shape[0]} samples x {wav_data.shape[1]} channels to {out}")
    reader.stop()
    link.close()


if __name__ == "__main__":
    main()
