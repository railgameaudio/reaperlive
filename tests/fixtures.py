"""Synthetic audio so the tests do not need a real song on disk."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def _blip(freq: float, dur: float, sr: int = SR, decay: float = 40.0) -> np.ndarray:
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    return np.sin(2 * np.pi * freq * t) * np.exp(-decay * t)


def drum_loop(path: Path, bpm: float = 120.0, bars: int = 16, beats_per_bar: int = 4,
              lead_silence: float = 0.75, sr: int = SR, vocal_bars=()) -> Path:
    """A kick/snare pattern with an optional sung-ish tone over given bar ranges."""
    beat = 60.0 / bpm
    total = lead_silence + bars * beats_per_bar * beat + 1.0
    out = np.zeros(int(total * sr))

    for i in range(bars * beats_per_bar):
        t = lead_silence + i * beat
        start = int(t * sr)
        if i % beats_per_bar == 0:
            hit = _blip(55, 0.25, sr, decay=18.0) * 1.0   # kick, on the bar line
        elif i % beats_per_bar == 2:
            hit = _blip(190, 0.15, sr, decay=30.0) * 0.6  # snare
        else:
            hit = _blip(320, 0.05, sr, decay=90.0) * 0.25  # hat
        end = min(start + len(hit), len(out))
        out[start:end] += hit[: end - start]

    for bar_start, bar_end in vocal_bars:
        t0 = lead_silence + bar_start * beats_per_bar * beat
        t1 = lead_silence + bar_end * beats_per_bar * beat
        seg = np.arange(int(t0 * sr), min(int(t1 * sr), len(out)))
        tt = (seg - seg[0]) / sr
        melody = 220 * (1 + 0.05 * np.sin(2 * np.pi * 0.5 * tt))
        out[seg] += 0.5 * np.sin(2 * np.pi * melody * tt) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * tt))

    out /= np.max(np.abs(out)) * 1.05
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.stack([out, out], axis=1), sr, subtype="PCM_16")
    return path


def accelerating_loop(path: Path, start_bpm: float = 100.0, end_bpm: float = 120.0,
                      bars: int = 16, beats_per_bar: int = 4,
                      lead_silence: float = 0.5, sr: int = SR) -> tuple[Path, list]:
    """A drum loop that speeds up linearly. Returns the true downbeat times."""
    total_beats = bars * beats_per_bar
    times = []
    t = lead_silence
    for i in range(total_beats):
        times.append(t)
        bpm = start_bpm + (end_bpm - start_bpm) * i / max(total_beats - 1, 1)
        t += 60.0 / bpm

    out = np.zeros(int((t + 1.0) * sr))
    for i, when in enumerate(times):
        start = int(when * sr)
        if i % beats_per_bar == 0:
            hit = _blip(55, 0.25, sr, decay=18.0)
        elif i % beats_per_bar == 2:
            hit = _blip(190, 0.15, sr, decay=30.0) * 0.6
        else:
            hit = _blip(320, 0.05, sr, decay=90.0) * 0.25
        end = min(start + len(hit), len(out))
        out[start:end] += hit[: end - start]

    out /= np.max(np.abs(out)) * 1.05
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.stack([out, out], axis=1), sr, subtype="PCM_16")
    return path, times[::beats_per_bar]
