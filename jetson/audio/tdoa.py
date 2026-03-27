"""TDOA bearing estimation using GCC-PHAT across 4 channels."""

from __future__ import annotations

import math
from itertools import combinations
from typing import Iterable

import numpy as np

from config import ARRAY_GEOMETRY, SOUND_SPEED_MPS


def gcc_phat(sig: np.ndarray, refsig: np.ndarray, fs: int, max_tau: float | None = None) -> float:
    n = sig.shape[0] + refsig.shape[0]
    sig_fft = np.fft.rfft(sig, n=n)
    ref_fft = np.fft.rfft(refsig, n=n)
    cross = sig_fft * np.conj(ref_fft)
    denom = np.abs(cross)
    denom[denom < 1e-12] = 1e-12
    cross /= denom
    corr = np.fft.irfft(cross, n=n * 2)

    max_shift = int(n)
    if max_tau is not None:
        max_shift = min(int(fs * max_tau), max_shift)

    corr = np.concatenate((corr[-max_shift:], corr[: max_shift + 1]))
    shift = int(np.argmax(np.abs(corr)) - max_shift)
    return shift / float(fs)


def _solve_direction_from_tdoa(
    pairs: Iterable[tuple[int, int, float]],
    geometry: list[tuple[float, float, float]],
    sound_speed: float,
) -> np.ndarray:
    a_rows: list[list[float]] = []
    b_vals: list[float] = []
    for i, j, tau in pairs:
        pi = np.array(geometry[i][:2], dtype=np.float64)
        pj = np.array(geometry[j][:2], dtype=np.float64)
        delta = pi - pj
        a_rows.append([delta[0], delta[1]])
        b_vals.append(sound_speed * tau)
    a = np.array(a_rows, dtype=np.float64)
    b = np.array(b_vals, dtype=np.float64)
    d, *_ = np.linalg.lstsq(a, b, rcond=None)
    norm = np.linalg.norm(d)
    if norm > 1e-9:
        d = d / norm
    return d


def estimate_bearing(
    ch0: np.ndarray,
    ch1: np.ndarray,
    ch2: np.ndarray,
    ch3: np.ndarray,
    sample_rate_hz: int,
    geometry: list[tuple[float, float, float]] | None = None,
    sound_speed: float = SOUND_SPEED_MPS,
) -> float:
    channels = [ch0, ch1, ch2, ch3]
    geometry = geometry or ARRAY_GEOMETRY
    max_baseline = max(
        np.linalg.norm(np.array(geometry[i]) - np.array(geometry[j])) for i, j in combinations(range(4), 2)
    )
    max_tau = max_baseline / sound_speed

    pair_taus: list[tuple[int, int, float]] = []
    for i, j in combinations(range(4), 2):
        tau = gcc_phat(channels[i], channels[j], sample_rate_hz, max_tau=max_tau)
        pair_taus.append((i, j, tau))

    direction = _solve_direction_from_tdoa(pair_taus, geometry, sound_speed)
    bearing_rad = math.atan2(direction[0], direction[1])  # 0 = +Y (forward), +clockwise
    bearing_deg = math.degrees(bearing_rad)
    return ((bearing_deg + 180.0) % 360.0) - 180.0


def _fractional_delay(sig: np.ndarray, delay_s: float, fs: int) -> np.ndarray:
    t = np.arange(len(sig), dtype=np.float64) / fs
    shifted_t = t - delay_s
    return np.interp(shifted_t, t, sig, left=0.0, right=0.0)


if __name__ == "__main__":
    rng = np.random.default_rng(4)
    fs = 20000
    n = 4096
    click = np.zeros(n, dtype=np.float64)
    click[500:504] = [1.0, 0.8, 0.4, 0.2]
    click += 0.03 * rng.standard_normal(n)

    truth_bearing_deg = 45.0
    truth_rad = math.radians(truth_bearing_deg)
    direction = np.array([math.sin(truth_rad), math.cos(truth_rad)], dtype=np.float64)

    delays = []
    for p in ARRAY_GEOMETRY:
        pos = np.array(p[:2], dtype=np.float64)
        tau = float(np.dot(pos, direction) / SOUND_SPEED_MPS)
        delays.append(tau)
    delays = np.array(delays)
    delays -= np.min(delays)

    channels = [_fractional_delay(click, d, fs) for d in delays]
    est = estimate_bearing(channels[0], channels[1], channels[2], channels[3], sample_rate_hz=fs)

    err = ((est - truth_bearing_deg + 180.0) % 360.0) - 180.0
    print(f"True bearing: {truth_bearing_deg:.2f} deg")
    print(f"Estimated:    {est:.2f} deg")
    print(f"Error:        {err:.2f} deg")
    assert abs(err) <= 5.0, f"TDOA estimate too far off: {err:.2f} deg"
    print("TDOA self-test passed.")
