"""Beat tracking, downbeat estimation and DAW tempo-map construction."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional, Sequence

import librosa
import numpy as np

from reaperlive.config import BeatGrid, TempoOptions, TempoPoint

log = logging.getLogger(__name__)

ANALYSIS_SR = 22050
#: 256 samples @ 22.05 kHz = 11.6 ms of grid resolution, enough that bar-length
#: measurements are not dominated by frame quantisation.
HOP = 256


def _load_mono(path: Path, sr: int = ANALYSIS_SR) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    return y


def _fold_bpm(bpm: float, lo: float, hi: float) -> float:
    """Bring an octave-confused tempo into the expected range."""
    if bpm <= 0:
        return bpm
    while bpm < lo - 1e-6:
        bpm *= 2.0
    while bpm > hi + 1e-6:
        bpm /= 2.0
    return bpm


def track_beats(path: Path, options: TempoOptions,
                percussive_ref: Optional[Path] = None) -> tuple[np.ndarray, float]:
    """Return (beat times in seconds, median BPM).

    ``percussive_ref`` should be the isolated drum stem when one exists: beat
    tracking on drums alone is markedly more stable than on a full mix.
    """
    ref = percussive_ref if percussive_ref and Path(percussive_ref).exists() else path
    y = _load_mono(Path(ref))
    if ref is path or percussive_ref is None:
        # No drum stem - emphasise transients so harmony does not smear the grid.
        y = librosa.effects.percussive(y, margin=2.0)

    onset_env = librosa.onset.onset_strength(
        y=y, sr=ANALYSIS_SR, hop_length=HOP, aggregate=np.median)
    start_bpm = options.fixed_bpm or 120.0
    tempo, beats = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=ANALYSIS_SR, hop_length=HOP,
        start_bpm=start_bpm, units="time", trim=False,
    )
    beat_times = np.asarray(beats, dtype=float)
    if beat_times.size < 4:
        raise RuntimeError(
            "Could not find a stable beat. Pass --bpm to set the tempo by hand."
        )

    median_bpm = 60.0 / _robust_period(np.diff(beat_times))
    folded = _fold_bpm(median_bpm, options.bpm_min, options.bpm_max)
    if not math.isclose(folded, median_bpm, rel_tol=1e-3):
        factor = folded / median_bpm
        log.info("Tempo %.1f folded to %.1f BPM (x%.2f)", median_bpm, folded, factor)
        beat_times = _rescale_beats(beat_times, factor)
        median_bpm = folded

    beat_times = correct_latency(y, beat_times, 60.0 / median_bpm)
    # The tracker runs the pulse past the final transient; drop what is not audio.
    duration = librosa.get_duration(y=y, sr=ANALYSIS_SR)
    beat_times = beat_times[(beat_times >= -1e-6) & (beat_times <= duration)]
    return beat_times, median_bpm


def _robust_period(intervals: np.ndarray) -> float:
    """Average beat length, ignoring intervals where the tracker skipped a beat.

    A plain median snaps to the frame grid (a 0.5 s beat measures as an
    alternating 0.488/0.511 s), so we average the inliers instead.
    """
    intervals = np.asarray(intervals, dtype=float)
    intervals = intervals[intervals > 0]
    if intervals.size == 0:
        raise RuntimeError("No usable beat intervals were found.")
    med = float(np.median(intervals))
    inliers = intervals[np.abs(intervals - med) <= 0.25 * med]
    return float(inliers.mean()) if inliers.size else med


def _rescale_beats(beat_times: np.ndarray, factor: float) -> np.ndarray:
    """Double or halve the beat grid to match a folded tempo."""
    if factor > 1.5:  # tempo doubled -> interpolate beats
        steps = int(round(factor))
        out = []
        for i in range(len(beat_times) - 1):
            a, b = beat_times[i], beat_times[i + 1]
            out.extend(a + (b - a) * k / steps for k in range(steps))
        out.append(beat_times[-1])
        return np.asarray(out)
    if factor < 0.75:  # tempo halved -> drop every other beat
        steps = int(round(1.0 / factor))
        return beat_times[::steps]
    return beat_times


def correct_latency(y: np.ndarray, beat_times: np.ndarray, period: float) -> np.ndarray:
    """Remove the systematic lag between onset-envelope peaks and real transients.

    Onset strength peaks a frame or two after the attack, so tracked beats sit
    consistently late. We measure the median offset against back-tracked onsets
    (which point at where the transient actually starts) and slide the whole
    grid by it - a constant shift, so the grid itself stays perfectly even.
    """
    onsets = librosa.onset.onset_detect(
        y=y, sr=ANALYSIS_SR, hop_length=HOP, backtrack=True, units="time")
    if onsets.size == 0 or beat_times.size == 0:
        return beat_times
    deltas = []
    tol = 0.25 * period
    for bt in beat_times:
        nearest = onsets[int(np.argmin(np.abs(onsets - bt)))]
        if abs(nearest - bt) <= tol:
            deltas.append(bt - nearest)
    if len(deltas) < max(4, beat_times.size // 8):
        return beat_times
    offset = float(np.median(deltas))
    offset = float(np.clip(offset, -0.15 * period, 0.15 * period))
    log.info("Beat latency correction: %+.1f ms", -offset * 1000.0)
    return beat_times - offset


def estimate_downbeats(path: Path, beat_times: np.ndarray, beats_per_bar: int,
                       percussive_ref: Optional[Path] = None) -> np.ndarray:
    """Pick which of every ``beats_per_bar`` beats carries the bar line.

    Beat trackers give a pulse but no phase. Bar ones tend to be the loudest
    onsets with the most low-end (kick), so we score each candidate phase and
    keep the winner.
    """
    if beats_per_bar <= 1 or beat_times.size < beats_per_bar * 2:
        return beat_times.copy()

    ref = percussive_ref if percussive_ref and Path(percussive_ref).exists() else path
    y = _load_mono(Path(ref))
    onset_env = librosa.onset.onset_strength(
        y=y, sr=ANALYSIS_SR, hop_length=HOP, aggregate=np.median)
    times = librosa.times_like(onset_env, sr=ANALYSIS_SR, hop_length=HOP)

    # Low-band energy carries the kick, which usually lands on beat one.
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=ANALYSIS_SR, n_fft=2048)
    low = spec[freqs < 160].sum(axis=0)
    low_times = librosa.times_like(low, sr=ANALYSIS_SR, hop_length=512)

    onset_at_beat = np.interp(beat_times, times, onset_env)
    low_at_beat = np.interp(beat_times, low_times, low)
    onset_at_beat = onset_at_beat / (onset_at_beat.max() or 1.0)
    low_at_beat = low_at_beat / (low_at_beat.max() or 1.0)
    score = onset_at_beat + low_at_beat

    phases = [score[phase::beats_per_bar].mean() for phase in range(beats_per_bar)]
    best = int(np.argmax(phases))
    log.info("Downbeat phase %d/%d (scores %s)", best + 1, beats_per_bar,
             ", ".join(f"{p:.2f}" for p in phases))
    return beat_times[best::beats_per_bar]


def _compress(points: list[TempoPoint], tolerance_pct: float) -> list[TempoPoint]:
    """Drop tempo markers within ``tolerance_pct`` of the tempo already in force.

    Measured bar lengths always wobble by a few milliseconds; without a
    deadzone every bar becomes a tempo marker and the map is unreadable.
    Genuine drift still accumulates past the threshold and is kept.
    """
    out: list[TempoPoint] = []
    for pt in points:
        if out and pt.time_signature is None:
            if abs(pt.bpm - out[-1].bpm) <= out[-1].bpm * tolerance_pct / 100.0:
                continue
        out.append(pt)
    return out


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Median filter that keeps the array length, for de-jittering bar lengths."""
    if window <= 1 or values.size < window:
        return values
    pad = window // 2
    padded = np.pad(values, pad, mode="edge")
    return np.asarray([
        np.median(padded[i:i + window]) for i in range(values.size)
    ])


def build_grid(path: Path, options: TempoOptions,
               percussive_ref: Optional[Path] = None) -> BeatGrid:
    """Full tempo analysis: beats, downbeats, count-in shift and tempo markers."""
    beat_times, bpm = track_beats(path, options, percussive_ref)
    if options.fixed_bpm:
        bpm = float(options.fixed_bpm)
    elif options.round_bpm:
        bpm = float(round(bpm))

    downbeats = estimate_downbeats(path, beat_times, options.beats_per_bar, percussive_ref)
    if downbeats.size == 0:
        downbeats = beat_times[:1]

    sig = (options.beats_per_bar, options.beat_unit)
    variable = options.fixed_bpm is None and options.mode != "constant"

    # Local tempo per bar (or per beat), de-jittered before it becomes a map.
    locals_: list[tuple[float, float]] = []
    if variable and downbeats.size >= 2:
        anchors = np.asarray(
            downbeats if options.mode == "measure" else beat_times, dtype=float)
        span_beats = options.beats_per_bar if options.mode == "measure" else 1
        spans = _smooth(np.diff(anchors), options.smooth_window)
        locals_ = [
            (float(a), float(np.clip(60.0 * span_beats / sp, 20.0, 400.0)))
            for a, sp in zip(anchors[:-1], spans) if sp > 0
        ]
        # The final span straddles the ending, where the tracker is least
        # reliable, and a tempo change in the last bar buys nothing.
        if len(locals_) >= 3:
            locals_.pop()

    # A steady song should carry one marker holding the best global estimate,
    # not the noisier measurement of its first bar.
    if not locals_:
        first_bpm = bpm
    else:
        probe = _compress([TempoPoint(0.0, locals_[0][1], sig)]
                          + [TempoPoint(t, b) for t, b in locals_[1:]],
                          options.tolerance_pct)
        first_bpm = bpm if len(probe) == 1 else locals_[0][1]
    first_bpm = float(np.clip(first_bpm, 20.0, 400.0))

    # Nudge everything so the first downbeat lands exactly on a bar line, with
    # a whole number of count-in bars in front of it.
    t0 = float(downbeats[0])
    bar_seconds = options.beats_per_bar * 60.0 / first_bpm
    bars = max(options.lead_in_bars, 0)
    shift = bars * bar_seconds - t0
    while shift < 0:
        bars += 1
        shift = bars * bar_seconds - t0
    log.info("Count-in: %d bar(s), audio shifted %+.3fs", bars, shift)

    beat_times = beat_times + shift
    downbeats = downbeats + shift

    points = [TempoPoint(0.0, first_bpm, sig)]
    points += [TempoPoint(t + shift, b) for t, b in locals_[1:]]
    points = _compress(points, options.tolerance_pct)
    log.info("Tempo map: %.2f BPM, %d marker(s), mode=%s", first_bpm, len(points),
             options.mode)

    return BeatGrid(
        bpm=float(first_bpm if len(points) == 1 else bpm),
        beat_times=beat_times,
        downbeat_times=downbeats,
        beats_per_bar=options.beats_per_bar,
        beat_unit=options.beat_unit,
        tempo_points=points,
        shift=shift,
    )


def bar_lines(grid: BeatGrid, until: float) -> np.ndarray:
    """Bar-line times covering the project, extrapolating past the last downbeat."""
    downbeats = np.asarray(grid.downbeat_times, dtype=float)
    if downbeats.size == 0:
        return np.asarray([0.0])
    bar_seconds = grid.beats_per_bar * 60.0 / grid.bpm
    lines = list(downbeats)
    # Count-in bars in front of the first detected downbeat.
    t = downbeats[0] - bar_seconds
    while t > -1e-6:
        lines.insert(0, t)
        t -= bar_seconds
    t = downbeats[-1] + bar_seconds
    while t < until:
        lines.append(t)
        t += bar_seconds
    return np.asarray(sorted(lines))


def snap(value: float, anchors: Sequence[float]) -> float:
    """Nearest anchor to ``value``."""
    arr = np.asarray(anchors, dtype=float)
    if arr.size == 0:
        return value
    return float(arr[int(np.argmin(np.abs(arr - value)))])
