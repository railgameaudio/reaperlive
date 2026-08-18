"""Write a REAPER project (.RPP).

The format is a plain-text tree of ``<CHUNK ...>`` blocks, which makes it a
pleasant target: we can lay out tracks, a tempo map, markers and an inline MIDI
click without touching a binary format.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional, Sequence

from reaperlive.analysis import structure as structure_mod
from reaperlive.config import BeatGrid, ClickOptions, Section, Stem
from reaperlive.render.metronome import reaper_midi_events
from reaperlive.separate.base import color_for as stem_color

log = logging.getLogger(__name__)


def guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def native_color(hex_color: str) -> int:
    """#rrggbb -> REAPER's 0x01BBGGRR colour integer."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return 0x1000000 | (b << 16) | (g << 8) | r


class Chunk:
    """A ``<NAME ...>`` block. Children are lines or nested chunks."""

    def __init__(self, header: str):
        self.header = header
        self.children: list = []

    def line(self, text: str) -> "Chunk":
        self.children.append(text)
        return self

    def lines(self, texts: Iterable[str]) -> "Chunk":
        self.children.extend(texts)
        return self

    def chunk(self, header: str) -> "Chunk":
        child = Chunk(header)
        self.children.append(child)
        return child

    def render(self, indent: int = 0) -> list[str]:
        pad = "  " * indent
        out = [f"{pad}<{self.header}"]
        for child in self.children:
            if isinstance(child, Chunk):
                out.extend(child.render(indent + 1))
            else:
                out.append(f"{pad}  {child}")
        out.append(f"{pad}>")
        return out


def quote(text: str) -> str:
    """REAPER string literal: pick a quote character the value does not contain."""
    text = str(text)
    for ch in ('"', "'", "`"):
        if ch not in text:
            return f"{ch}{text}{ch}"
    return '"' + text.replace('"', "'") + '"'


def _tempo_envelope(grid: BeatGrid) -> Optional[Chunk]:
    """The tempo map. Square-shaped points hold until the next marker."""
    points = list(grid.tempo_points)
    if len(points) <= 1:
        return None
    env = Chunk("TEMPOENVEX")
    env.line("EGUID " + guid())
    env.line("ACT 1 -1")
    env.line("VIS 1 0 1")
    env.line("LANEHEIGHT 0 0")
    env.line("ARM 0")
    env.line("DEFSHAPE 0 -1 -1")
    for point in points:
        if point.time_signature:
            num, den = point.time_signature
            packed = num | (den << 16)
            env.line(f"PT {point.time:.10f} {point.bpm:.6f} 0 {packed} 0 0")
        else:
            env.line(f"PT {point.time:.10f} {point.bpm:.6f} 0")
    return env


def _markers(project: Chunk, sections: Sequence[Section], style: str,
             count_in_end: float) -> None:
    """Ruler markers and/or regions for the song sections."""
    index = 1
    if count_in_end > 1e-3:
        project.line(
            f"MARKER {index} 0 {quote('COUNT-IN')} 0 {native_color('#7f8c8d')} 1 B {guid()} 0"
        )
        index += 1

    for section in sections:
        color = native_color(structure_mod.color_for(section.label))
        if style in ("markers", "both"):
            project.line(
                f"MARKER {index} {section.start:.10f} {quote(section.label)} 0 {color} 1 B {guid()} 0"
            )
            index += 1
        if style in ("regions", "both"):
            project.line(
                f"MARKER {index} {section.start:.10f} {quote(section.label)} 1 {color} 1 R {guid()} 0"
            )
            project.line(
                f"MARKER {index} {section.end:.10f} {quote('')} 1 {color} 1 R {guid()} 0"
            )
            index += 1


def _audio_track(name: str, relpath: str, position: float, length: float,
                 color: str, muted: bool = False) -> Chunk:
    track = Chunk("TRACK " + guid())
    track.line(f"NAME {quote(name)}")
    track.line("PEAKCOL " + str(native_color(color)))
    track.line("BEAT -1")
    track.line("AUTOMODE 0")
    track.line("VOLPAN 1 0 -1 -1 1")
    track.line(f"MUTESOLO {1 if muted else 0} 0 0")
    track.line("IPHASE 0")
    track.line("PLAYOFFS 0 1")
    track.line("ISBUS 0 0")
    track.line("BUSCOMP 0 0 0 0 0")
    track.line("SHOWINMIX 1 0.6667 0.5 1 0.5 -1 -1 -1")
    track.line("SEL 0")
    track.line("REC 0 0 1 0 0 0 0 0")
    track.line("VU 2")
    track.line("TRACKHEIGHT 0 0 0 0 0 0")
    track.line("INQ 0 0 0 0.5 100 0 0 100")
    track.line("NCHAN 2")
    track.line("FX 1")
    track.line("TRACKID " + guid())
    track.line("PERF 0")
    track.line("MIDIOUT -1")
    track.line("MAINSEND 1 0")

    item = track.chunk("ITEM")
    item.line(f"POSITION {position:.10f}")
    item.line("SNAPOFFS 0")
    item.line(f"LENGTH {length:.10f}")
    item.line("LOOP 0")
    item.line("ALLTAKES 0")
    item.line("FADEIN 1 0 0 1 0 0 0")
    item.line("FADEOUT 1 0 0 1 0 0 0")
    item.line("MUTE 0 0")
    item.line("SEL 0")
    item.line("IGUID " + guid())
    item.line(f"IID {abs(hash(name)) % 9000 + 1}")
    item.line(f"NAME {quote(Path(relpath).name)}")
    item.line("VOLPAN 1 0 1 -1")
    item.line("SOFFS 0")
    item.line("PLAYRATE 1 1 0 -1 0 0.0025")
    item.line("CHANMODE 0")
    item.line("GUID " + guid())
    source = item.chunk("SOURCE WAVE")
    source.line(f"FILE {quote(relpath)}")
    return track


def _click_track(name: str, grid: BeatGrid, duration: float,
                 options: ClickOptions, color: str) -> Chunk:
    track = Chunk("TRACK " + guid())
    track.line(f"NAME {quote(name)}")
    track.line("PEAKCOL " + str(native_color(color)))
    track.line("BEAT -1")
    track.line("AUTOMODE 0")
    track.line("VOLPAN 1 0 -1 -1 1")
    track.line("MUTESOLO 0 0 0")
    track.line("IPHASE 0")
    track.line("ISBUS 0 0")
    track.line("SEL 0")
    track.line("REC 0 0 1 0 0 0 0 0")
    track.line("VU 2")
    track.line("TRACKHEIGHT 0 0 0 0 0 0")
    track.line("INQ 0 0 0 0.5 100 0 0 100")
    track.line("NCHAN 2")
    track.line("FX 1")
    track.line("TRACKID " + guid())
    track.line("PERF 0")
    track.line("MIDIOUT -1")
    track.line("MAINSEND 1 0")

    item = track.chunk("ITEM")
    item.line("POSITION 0")
    item.line("SNAPOFFS 0")
    item.line(f"LENGTH {duration:.10f}")
    item.line("LOOP 0")
    item.line("ALLTAKES 0")
    item.line("FADEIN 1 0 0 1 0 0 0")
    item.line("FADEOUT 1 0 0 1 0 0 0")
    item.line("MUTE 0 0")
    item.line("SEL 0")
    item.line("IGUID " + guid())
    item.line("IID 9001")
    item.line(f"NAME {quote('Metronome')}")
    item.line("VOLPAN 1 0 1 -1")
    item.line("SOFFS 0")
    item.line("PLAYRATE 1 1 0 -1 0 0.0025")
    item.line("CHANMODE 0")
    item.line("GUID " + guid())
    source = item.chunk("SOURCE MIDI")
    source.line("HASDATA 1 960 QN")
    source.lines(reaper_midi_events(grid, duration, options))
    source.line(f"IGNTEMPO 0 {grid.initial_bpm:.6f} {grid.beats_per_bar} {grid.beat_unit}")
    source.line("CCINTERP 32")
    source.line("GUID " + guid())
    return track


def write_project(path: Path, stems: Sequence[Stem], grid: BeatGrid,
                  sections: Sequence[Section], duration: float,
                  click: ClickOptions, marker_style: str = "markers",
                  click_wav: Optional[str] = None,
                  sample_rate: int = 44100) -> Path:
    """Assemble and write the .RPP."""
    project = Chunk(f'REAPER_PROJECT 0.1 "7.0/reaperlive" {int(time.time())}')
    project.line("RIPPLE 0")
    project.line("GROUPOVERRIDE 0 0 0")
    project.line("AUTOXFADE 129")
    project.line("ENVATTACH 3")
    project.line("POOLEDENVATTACH 0")
    project.line("MIXERUIFLAGS 11 48")
    project.line("ENVFADESZ10 40")
    project.line("PEAKGAIN 1")
    project.line("FEEDBACK 0")
    project.line("PANLAW 1")
    project.line("PROJOFFS 0 0 0")
    project.line("MAXPROJLEN 0 0")
    project.line("GRID 3199 8 1 8 1 0 8 0")
    project.line("TIMEMODE 1 5 -1 30 0 0 -1")
    project.line("VIDEO_CONFIG 0 0 256")
    project.line("PANMODE 3")
    project.line("CURSOR 0")
    project.line("ZOOM 5 0 0")
    project.line("VZOOMEX 6 0")
    project.line("USE_REC_CFG 0")
    project.line("RECMODE 1")
    project.line("SMPTESYNC 0 30 100 40 1000 300 0 0 1 0 0")
    project.line("LOOP 0")
    project.line("LOOPGRAN 0 4")
    project.line(f"RECORD_PATH {quote('audio')} {quote('')}")
    project.line(f"SAMPLERATE {sample_rate} 0 0")
    project.line(f"TEMPO {grid.initial_bpm:.6f} {grid.beats_per_bar} {grid.beat_unit}")
    project.line("PLAYRATE 1 0 0.25 4")
    project.line("SELECTION 0 0")
    project.line("SELECTION2 0 0")
    project.line("MASTERAUTOMODE 0")
    project.line("MASTERTRACKHEIGHT 0 0")
    project.line("MASTERPEAKCOL 16576")
    project.line("MASTERMUTESOLO 0")
    project.line("MASTER_NCH 2 2")
    project.line("MASTER_VOLUME 1 0 -1 -1 1")
    project.line("MASTER_PANMODE 3")

    _markers(project, sections, marker_style, count_in_end=grid.shift)

    envelope = _tempo_envelope(grid)
    if envelope is not None:
        project.children.append(envelope)

    if click.midi:
        project.children.append(
            _click_track("CLICK (MIDI)", grid, duration, click, stem_color("click")))
    if click_wav:
        project.children.append(
            _audio_track("CLICK (audio)", click_wav, 0.0, duration,
                         stem_color("click")))

    for stem in stems:
        project.children.append(
            _audio_track(stem.name.replace("_", " ").title(), stem.relpath,
                         grid.shift, stem.duration,
                         stem.color or stem_color(stem.name),
                         muted=stem.name == "mix"))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(project.render()) + "\n", encoding="utf-8")
    log.info("Wrote %s", path)
    return path
