"""Configuration objects shared by the whole pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

#: Separator backends the CLI knows how to drive.
SEPARATORS = ("demucs", "roformer", "none")

#: How the detected beat grid is turned into DAW tempo markers.
TEMPO_MODES = ("constant", "measure", "beat")

#: DAW project formats we can write.
TARGETS = ("reaper", "ableton", "both")


@dataclass
class SeparationOptions:
    backend: str = "demucs"
    #: Demucs model name. htdemucs_ft is the fine-tuned (slower, better) variant.
    model: str = "htdemucs"
    #: Split into just {vocals, no_vocals} instead of the full 4/6 stem set.
    two_stems: Optional[str] = None
    #: Demucs shift trick: more shifts = better quality, linearly slower.
    shifts: int = 1
    jobs: int = 1
    device: str = "auto"


@dataclass
class TempoOptions:
    mode: str = "measure"
    #: Force a known BPM instead of detecting one.
    fixed_bpm: Optional[float] = None
    beats_per_bar: int = 4
    beat_unit: int = 4
    #: Bars of count-in placed before the song starts.
    lead_in_bars: int = 1
    #: Snap the detected average tempo to the nearest whole BPM.
    round_bpm: bool = False
    #: Reject detected tempi outside this range (halving/doubling is applied first).
    bpm_min: float = 60.0
    bpm_max: float = 200.0
    #: A new tempo marker is only written when the tempo moves by more than this %.
    tolerance_pct: float = 1.5
    #: Median-filter width (in bars/beats) applied to measured spans before mapping.
    smooth_window: int = 5


@dataclass
class StructureOptions:
    enabled: bool = True
    #: How far below the vocal stem's loud passages counts as "no vocal", in dB.
    threshold_db: float = 32.0
    #: Segments shorter than this are absorbed into their neighbour.
    min_segment_sec: float = 6.0
    #: Snap section boundaries to the nearest bar line.
    snap_to_bars: bool = True
    style: str = "markers"  # markers | regions | both


@dataclass
class ClickOptions:
    midi: bool = True
    audio: bool = True
    accent_note: int = 84
    beat_note: int = 79
    accent_velocity: int = 112
    beat_velocity: int = 80
    #: MIDI channel, 1-based. 10 = GM drum channel.
    channel: int = 1


@dataclass
class ProjectOptions:
    source: str = ""
    outdir: Path = Path("projects")
    name: Optional[str] = None
    target: str = "reaper"
    sample_rate: int = 44100
    keep_mix: bool = True
    separation: SeparationOptions = field(default_factory=SeparationOptions)
    tempo: TempoOptions = field(default_factory=TempoOptions)
    structure: StructureOptions = field(default_factory=StructureOptions)
    click: ClickOptions = field(default_factory=ClickOptions)


@dataclass
class Stem:
    """One separated part, on disk and ready to drop on a track."""

    name: str
    path: Path
    #: Path as written into the project file, relative to the project root.
    relpath: str
    duration: float
    color: Optional[str] = None


@dataclass
class Section:
    start: float
    end: float
    label: str
    has_vocals: bool

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class TempoPoint:
    """A tempo marker at ``time`` seconds of project time."""

    time: float
    bpm: float
    time_signature: Optional[tuple] = None


@dataclass
class BeatGrid:
    bpm: float
    beat_times: Sequence[float]
    downbeat_times: Sequence[float]
    beats_per_bar: int
    beat_unit: int
    tempo_points: Sequence[TempoPoint] = ()
    #: Seconds every piece of audio is nudged by so bar 1 is a clean count-in.
    shift: float = 0.0

    @property
    def initial_bpm(self) -> float:
        """Tempo the project opens at - what a DAW's project tempo field wants."""
        return self.tempo_points[0].bpm if self.tempo_points else self.bpm
