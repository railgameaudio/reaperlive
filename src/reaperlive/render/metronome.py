"""Metronome generation: a MIDI click clip, a standalone .mid, and a click wav."""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import soundfile as sf

from reaperlive.config import BeatGrid, ClickOptions

log = logging.getLogger(__name__)

#: MIDI ticks per quarter note. Matches what REAPER writes by default.
PPQ = 960


def beat_plan(grid: BeatGrid, duration: float) -> list[bool]:
    """One entry per click in the project: True where the beat is a bar line."""
    return [accent for _, accent in click_times(grid, duration)]


def reaper_midi_events(grid: BeatGrid, duration: float,
                       options: ClickOptions) -> list[str]:
    """REAPER inline-MIDI ``E`` lines: one note per beat, on the musical grid.

    Positions are in ticks, so the clip follows the project tempo map for free -
    the click stays locked to the bars even where the song speeds up or drags.
    """
    status_on = 0x90 | ((options.channel - 1) & 0x0F)
    status_off = 0x80 | ((options.channel - 1) & 0x0F)
    note_ticks = PPQ // 8  # short blip, then rest until the next beat

    lines: list[str] = []
    pending = 0
    for accent in beat_plan(grid, duration):
        note = options.accent_note if accent else options.beat_note
        vel = options.accent_velocity if accent else options.beat_velocity
        lines.append(f"E {pending} {status_on:02x} {note:02x} {vel:02x}")
        lines.append(f"E {note_ticks} {status_off:02x} {note:02x} 00")
        pending = PPQ - note_ticks  # rest until the next beat
    lines.append("E 0 b0 7b 00")  # all notes off
    return lines


# --------------------------------------------------------------------------- #
# Standalone .mid (handy for dragging into Ableton, Logic, anything)
# --------------------------------------------------------------------------- #

def _vlq(value: int) -> bytes:
    """MIDI variable-length quantity."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack(">I", len(body)) + body


def write_midi_file(path: Path, grid: BeatGrid, duration: float,
                    options: ClickOptions) -> Path:
    """A format-1 SMF: tempo map on track 1, the click itself on track 2."""
    # --- tempo / time-signature track ---
    tempo_events = bytearray()
    last_tick = 0
    for point in grid.tempo_points:
        tick = _tick_at(grid, point.time)
        tempo_events += _vlq(max(0, tick - last_tick))
        tempo_events += b"\xff\x51\x03" + struct.pack(">I", int(60_000_000 / point.bpm))[1:]
        last_tick = tick
    denom_pow = max(0, int(np.log2(grid.beat_unit)))
    tempo_events = (
        _vlq(0) + b"\xff\x58\x04" + bytes([grid.beats_per_bar, denom_pow, 24, 8])
        + bytes(tempo_events)
    )
    tempo_events += _vlq(0) + b"\xff\x2f\x00"

    # --- click track ---
    status_on = 0x90 | ((options.channel - 1) & 0x0F)
    status_off = 0x80 | ((options.channel - 1) & 0x0F)
    note_ticks = PPQ // 8
    click = bytearray(_vlq(0) + b"\xff\x03" + bytes([5]) + b"CLICK")
    pending = 0
    for accent in beat_plan(grid, duration):
        note = options.accent_note if accent else options.beat_note
        vel = options.accent_velocity if accent else options.beat_velocity
        click += _vlq(pending) + bytes([status_on, note, vel])
        click += _vlq(note_ticks) + bytes([status_off, note, 0])
        pending = PPQ - note_ticks
    click += _vlq(0) + b"\xff\x2f\x00"

    header = struct.pack(">HHH", 1, 2, PPQ)
    data = _chunk(b"MThd", header) + _chunk(b"MTrk", bytes(tempo_events)) + _chunk(b"MTrk", bytes(click))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _tick_at(grid: BeatGrid, seconds: float) -> int:
    """Project seconds -> MIDI ticks, walking the tempo map."""
    points = list(grid.tempo_points)
    if not points:
        return int(round(seconds / (60.0 / grid.bpm) * PPQ))
    ticks = 0.0
    for i, point in enumerate(points):
        end = points[i + 1].time if i + 1 < len(points) else float("inf")
        if seconds <= point.time:
            break
        span = min(seconds, end) - point.time
        ticks += span / (60.0 / point.bpm) * PPQ
        if seconds <= end:
            break
    return int(round(ticks))


# --------------------------------------------------------------------------- #
# Audible click - so the project makes sense with no instrument loaded
# --------------------------------------------------------------------------- #

def render_click_wav(path: Path, grid: BeatGrid, duration: float,
                     sample_rate: int = 44100) -> Path:
    """Render the beat grid as short sine blips, accented on the bar line."""
    times = click_times(grid, duration)
    total = int(np.ceil((duration + 1.0) * sample_rate))
    out = np.zeros(total, dtype=np.float32)
    blip_len = int(0.045 * sample_rate)
    envelope = np.exp(-np.linspace(0, 28, blip_len))
    t = np.arange(blip_len) / sample_rate

    accent_blip = (np.sin(2 * np.pi * 1500 * t) * envelope * 0.7).astype(np.float32)
    beat_blip = (np.sin(2 * np.pi * 1000 * t) * envelope * 0.45).astype(np.float32)

    for when, accent in times:
        start = int(round(when * sample_rate))
        if start < 0 or start >= total:
            continue
        blip = accent_blip if accent else beat_blip
        end = min(start + blip_len, total)
        out[start:end] += blip[: end - start]

    peak = float(np.max(np.abs(out))) or 1.0
    out = out / max(peak, 1.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), out, sample_rate, subtype="PCM_16")
    return path


def _bpm_at(grid: BeatGrid, when: float) -> float:
    """Tempo in force at ``when``, per the tempo map."""
    bpm = grid.bpm
    for point in grid.tempo_points:
        if point.time <= when + 1e-9:
            bpm = point.bpm
        else:
            break
    return bpm


def click_times(grid: BeatGrid, duration: float) -> list[tuple[float, bool]]:
    """(time, is_downbeat) for every click in the project, count-in included.

    The count-in is laid out backwards from the first detected downbeat at the
    tempo the map opens with. That downbeat sits at a whole number of bars by
    construction, so the count-in divides exactly and click one lands on 0.0.
    Tracked beats take over from there, which keeps the click glued to the
    performance even where it drifts.
    """
    beats = [float(b) for b in grid.beat_times]
    downbeats = [float(d) for d in grid.downbeat_times]
    if not beats:
        return []

    first_downbeat = downbeats[0] if downbeats else beats[0]
    lead_beat = 60.0 / _bpm_at(grid, 0.0)

    lead: list[float] = []
    t = first_downbeat - lead_beat
    while t > 1e-6:
        lead.insert(0, t)
        t -= lead_beat
    if lead:
        lead.insert(0, 0.0) if abs(t) < 1e-6 else None
    if not lead or lead[0] > 1e-6:
        lead.insert(0, 0.0)

    # Tracked beats inside the count-in are replaced by the even count-in grid.
    times = lead + [b for b in beats if b >= first_downbeat - 1e-6]
    anchor_idx = len(lead)

    tail = times[-1] if times else 0.0
    while True:
        tail += 60.0 / _bpm_at(grid, tail)
        if tail >= duration:
            break
        times.append(tail)

    return [
        (when, (i - anchor_idx) % grid.beats_per_bar == 0)
        for i, when in enumerate(times)
    ]
