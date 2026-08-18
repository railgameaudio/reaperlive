"""Loose song-structure marking: where the singing is, and where it is not.

Working from the separated vocal stem rather than the full mix makes this far
more reliable than any spectral heuristic on the mixdown - the separator has
already answered "is there a voice here".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import librosa
import numpy as np

from reaperlive.config import BeatGrid, Section, StructureOptions
from reaperlive.analysis.tempo import bar_lines, snap

log = logging.getLogger(__name__)

ANALYSIS_SR = 22050
FRAME = 2048
HOP = 512

#: A vocal stem whose loudest moment is below this is not carrying a voice;
#: without this floor, an instrumental track's near-silent stem gets normalised
#: against its own noise and the whole song reads as "vocals".
SILENT_STEM_DBFS = -45.0

#: Nothing quieter than this counts as singing, however the stem is normalised.
ABSOLUTE_FLOOR_DBFS = -48.0

#: A stem carrying real singing swings well above its own noise floor. Less
#: range than this means there is nothing to tell apart, so we mark nothing.
MIN_CONTRAST_DB = 8.0

SECTION_COLORS = {
    "INTRO": "#4a5568",
    "OUTRO": "#4a5568",
    "VOCALS": "#c0392b",
    "INSTRUMENTAL": "#1f7a6c",
    "BREAK": "#8a6d1f",
}


def vocal_activity(vocals: Path, threshold_db: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame boolean 'a voice is audible here', plus the frame times."""
    y, sr = librosa.load(str(vocals), sr=ANALYSIS_SR, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=FRAME, hop_length=HOP)[0]
    times = librosa.times_like(rms, sr=sr, hop_length=HOP)
    db = librosa.amplitude_to_db(rms, ref=1.0)  # absolute dBFS, not self-relative

    # Smooth over ~0.4 s so breaths and consonant gaps do not chop the segment up.
    win = max(3, int(round(0.4 * sr / HOP)) | 1)
    db = np.convolve(db, np.ones(win) / win, mode="same")

    peak = float(db.max()) if db.size else -np.inf
    if peak < SILENT_STEM_DBFS:
        log.info("Vocal stem peaks at %.1f dBFS - treating the track as instrumental",
                 peak)
        return np.zeros(db.size, dtype=bool), times

    loud = float(np.percentile(db, 95))
    floor = float(np.percentile(db, 10))
    if loud - floor < MIN_CONTRAST_DB:
        log.info("Vocal stem is flat (%.1f dB of range) - treating the track as "
                 "instrumental", loud - floor)
        return np.zeros(db.size, dtype=bool), times

    # Judge against how loud this song's vocal actually gets, but never call
    # anything below the absolute floor - or below this stem's own noise - a
    # vocal. Keeping the release above the noise floor stops the gate latching
    # open for the rest of the song.
    on = max(loud - threshold_db, floor + 6.0, ABSOLUTE_FLOOR_DBFS)
    off = max(on - 4.0, floor + 2.0)
    log.debug("Vocal gate: on %.1f dBFS, off %.1f dBFS (loud %.1f, floor %.1f)",
              on, off, loud, floor)
    active = np.zeros(db.size, dtype=bool)
    state = False
    for i, value in enumerate(db):
        if state:
            state = value > off
        else:
            state = value > on
        active[i] = state
    return active, times


def _runs(active: np.ndarray, times: np.ndarray, duration: float) -> list[tuple[float, float, bool]]:
    """Collapse the boolean mask into (start, end, is_vocal) runs."""
    if active.size == 0:
        return [(0.0, duration, False)]
    runs: list[tuple[float, float, bool]] = []
    start = 0.0
    current = bool(active[0])
    for i in range(1, active.size):
        if bool(active[i]) != current:
            edge = float(times[i])
            runs.append((start, edge, current))
            start, current = edge, bool(active[i])
    runs.append((start, duration, current))
    return runs


def _merge_short(runs: list[tuple[float, float, bool]],
                 min_len: float) -> list[tuple[float, float, bool]]:
    """Absorb runs shorter than ``min_len`` into their neighbours."""
    if not runs:
        return runs
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, (start, end, is_vocal) in enumerate(runs):
            if end - start >= min_len:
                continue
            if i == 0:
                runs[1] = (start, runs[1][1], runs[1][2])
            elif i == len(runs) - 1:
                runs[i - 1] = (runs[i - 1][0], end, runs[i - 1][2])
            else:
                # Hand the orphan to whichever neighbour is longer.
                prev_len = runs[i - 1][1] - runs[i - 1][0]
                next_len = runs[i + 1][1] - runs[i + 1][0]
                if prev_len >= next_len:
                    runs[i - 1] = (runs[i - 1][0], end, runs[i - 1][2])
                else:
                    runs[i + 1] = (start, runs[i + 1][1], runs[i + 1][2])
            runs.pop(i)
            changed = True
            break
    # Neighbours can now share a state - fuse them.
    fused = [runs[0]]
    for start, end, is_vocal in runs[1:]:
        if is_vocal == fused[-1][2]:
            fused[-1] = (fused[-1][0], end, is_vocal)
        else:
            fused.append((start, end, is_vocal))
    return fused


def _label(runs: Sequence[tuple[float, float, bool]]) -> list[Section]:
    sections: list[Section] = []
    vocal_n = 0
    inst_n = 0
    last_vocal = max((i for i, r in enumerate(runs) if r[2]), default=-1)
    for i, (start, end, is_vocal) in enumerate(runs):
        if is_vocal:
            vocal_n += 1
            label = f"VOCALS {vocal_n}"
        elif i == 0:
            label = "INTRO"
        elif i > last_vocal:
            label = "OUTRO"
        elif end - start < 12.0:
            label = "BREAK"
        else:
            inst_n += 1
            label = f"INSTRUMENTAL {inst_n}"
        sections.append(Section(start=start, end=end, label=label, has_vocals=is_vocal))
    return sections


def detect_sections(vocals: Optional[Path], duration: float, grid: BeatGrid,
                    options: StructureOptions) -> list[Section]:
    """Vocal / no-vocal sections in *project* time, snapped to bar lines."""
    if not options.enabled or vocals is None or not Path(vocals).exists():
        return []

    active, times = vocal_activity(Path(vocals), options.threshold_db)
    if not active.any():
        log.info("No vocal activity found - skipping section markers")
        return []

    # Analysis runs on the raw stem; the project timeline is shifted by the count-in.
    stem_duration = float(times[-1]) if times.size else duration
    runs = _runs(active, times, stem_duration)
    runs = [(s + grid.shift, e + grid.shift, v) for s, e, v in runs]
    runs = _merge_short(runs, options.min_segment_sec)

    if options.snap_to_bars:
        bars = bar_lines(grid, until=duration + grid.shift)
        snapped: list[tuple[float, float, bool]] = []
        for start, end, is_vocal in runs:
            snapped.append((snap(start, bars), snap(end, bars), is_vocal))
        # Snapping can collapse or overlap neighbours; repair the chain.
        runs = []
        for start, end, is_vocal in snapped:
            if runs:
                start = runs[-1][1]
            if end - start <= 1e-3:
                continue
            runs.append((start, end, is_vocal))
        if runs:
            runs[0] = (max(0.0, runs[0][0]), runs[0][1], runs[0][2])

    sections = _label(runs)
    log.info("Sections: %s", ", ".join(f"{s.label}@{s.start:.1f}s" for s in sections))
    return sections


def color_for(label: str) -> str:
    key = label.split()[0]
    return SECTION_COLORS.get(key, "#5b6673")
