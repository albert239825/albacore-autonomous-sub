"""Audio classifier using TensorFlow Hub YAMNet."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Tuple

import numpy as np
import scipy.io.wavfile
import tensorflow as tf
import tensorflow_hub as hub


YAMNET_URL = "https://tfhub.dev/google/yamnet/1"
CLASS_MAP_CSV = "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv"


class YAMNetClassifier:
    def __init__(self) -> None:
        self.model = hub.load(YAMNET_URL)
        self.class_names = self._load_class_names()

    def _load_class_names(self) -> list[str]:
        import csv
        import urllib.request

        with urllib.request.urlopen(CLASS_MAP_CSV) as response:
            rows = response.read().decode("utf-8").splitlines()
        reader = csv.DictReader(rows)
        names = [row["display_name"] for row in reader]
        return names

    def classify(self, audio: np.ndarray, sample_rate: int = 16000, top_k: int = 3) -> List[Tuple[str, float]]:
        if audio.ndim != 1:
            raise ValueError("Expected mono 1D audio array.")
        audio = audio.astype(np.float32)
        if sample_rate != 16000:
            target_len = int(len(audio) * (16000.0 / sample_rate))
            audio = np.interp(np.linspace(0, len(audio), target_len), np.arange(len(audio)), audio)

        scores, _embeddings, _spectrogram = self.model(audio)
        mean_scores = tf.reduce_mean(scores, axis=0).numpy()
        top_idx = np.argsort(mean_scores)[::-1][:top_k]
        return [(self.class_names[i], float(mean_scores[i])) for i in top_idx]


def load_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    sample_rate, data = scipy.io.wavfile.read(str(path))
    data = data.astype(np.float32)
    if data.ndim > 1:
        data = np.mean(data, axis=1)
    if np.max(np.abs(data)) > 0:
        data = data / np.max(np.abs(data))
    return data, int(sample_rate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run YAMNet classification on test tone or WAV file.")
    parser.add_argument("--wav", type=str, default="")
    args = parser.parse_args()

    if args.wav:
        audio, sr = load_wav_mono(Path(args.wav))
        print(f"Loaded WAV: {args.wav} ({len(audio)} samples @ {sr}Hz)")
    else:
        sr = 16000
        t = np.arange(sr * 2, dtype=np.float32) / sr
        audio = 0.5 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
        print("Generated 440Hz test tone.")

    classifier = YAMNetClassifier()
    preds = classifier.classify(audio, sample_rate=sr, top_k=3)
    print("Top-3 predictions:")
    for cls, conf in preds:
        print(f"  {cls:30s} {conf:.4f}")
