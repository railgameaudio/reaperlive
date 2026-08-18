"""Write an Ableton Live set (.als) - gzipped XML.

Live's schema is large and version-sensitive, so this writes the smallest tree
Live will accept: audio tracks each holding one arrangement clip, a tempo on the
master track, and locators for the section markers. It targets the Live 11/12
schema (MajorVersion 5).

If your Live refuses the file, everything needed is still on disk - see
SONG-NOTES.md for the drag-and-drop route, which takes about a minute.
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
from typing import Optional, Sequence
from xml.etree import ElementTree as ET

from reaperlive.config import BeatGrid, Section, Stem

log = logging.getLogger(__name__)

MAJOR_VERSION = "5"
MINOR_VERSION = "11.0_11300"
CREATOR = "Ableton Live 11.3"
SCHEMA_CHANGE_COUNT = "3"

#: Live's fixed track-colour palette; indices chosen to echo the REAPER colours.
COLOR_INDEX = {
    "vocals": 0, "drums": 6, "bass": 10, "other": 18, "guitar": 22,
    "piano": 30, "no_vocals": 42, "instrumental": 42, "mix": 45, "click": 49,
}


def _el(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def _val(parent, tag, value):
    """Live's ubiquitous ``<Tag Value="..." />`` node."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    return ET.SubElement(parent, tag, {"Value": str(value)})


class _Ids:
    """Live wants unique, monotonically increasing ids across the document."""

    def __init__(self, start: int = 1):
        self._next = start

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


def _seconds_to_beats(grid: BeatGrid, seconds: float) -> float:
    """Project seconds -> Live's beat-based timeline, following the tempo map."""
    points = list(grid.tempo_points)
    if not points:
        return seconds * grid.bpm / 60.0
    beats = 0.0
    for i, point in enumerate(points):
        end = points[i + 1].time if i + 1 < len(points) else float("inf")
        if seconds <= point.time:
            break
        span = min(seconds, end) - point.time
        beats += span * point.bpm / 60.0
        if seconds <= end:
            break
    return beats


def _pointee(parent, ids: _Ids, tag: str = "AutomationTarget"):
    node = _el(parent, tag, Id=ids.take())
    _val(node, "LockEnvelope", 0)
    return node


def _mixer(parent, ids: _Ids):
    mixer = _el(parent, "Mixer")
    _val(mixer, "LomId", 0)
    _val(mixer, "LomIdView", 0)
    _val(mixer, "IsExpanded", True)
    for name in ("On",):
        node = _el(mixer, name)
        _val(node, "LomId", 0)
        _val(node, "Manual", True)
        _pointee(node, ids)
        _pointee(node, ids, "MidiCCOnOffThresholds")
    _val(mixer, "ModulationSourceCount", 0)
    _val(mixer, "ParametersListWrapper", "")
    _val(mixer, "Pointee", ids.take())
    _val(mixer, "LastSelectedTimeableIndex", 0)
    _val(mixer, "LastSelectedClipEnvelopeIndex", 0)
    lanes = _el(mixer, "LastPresetRef")
    _el(lanes, "Value")
    _val(mixer, "LockedScripts", "")
    _val(mixer, "IsFolded", False)
    _val(mixer, "ShouldShowPresetName", False)
    _val(mixer, "UserName", "")
    _val(mixer, "Annotation", "")
    _el(mixer, "SourceContext")

    for tag, manual in (("Speaker", True),):
        node = _el(mixer, tag)
        _val(node, "LomId", 0)
        _val(node, "Manual", manual)
        _pointee(node, ids)
        _pointee(node, ids, "MidiCCOnOffThresholds")

    for tag, manual, minv, maxv in (
        ("Volume", 1.0, 0.0003162277571, 1.99526238),
        ("Pan", 0.0, -1.0, 1.0),
    ):
        node = _el(mixer, tag)
        _val(node, "LomId", 0)
        _val(node, "Manual", manual)
        midi = _el(node, "MidiControllerRange")
        _val(midi, "Min", minv)
        _val(midi, "Max", maxv)
        _pointee(node, ids)
        _val(node, "ModulationTarget", 0)
    _val(mixer, "ViewStateSesstionTrackWidth", 93)
    _val(mixer, "CrossFadeState", 1)
    return mixer


def _routing(parent, tag: str, target: str, upper: str, lower: str):
    node = _el(parent, tag)
    _val(node, "Target", target)
    _val(node, "UpperDisplayString", upper)
    _val(node, "LowerDisplayString", lower)
    mpe = _el(node, "MpeSettings")
    _val(mpe, "ZoneType", 0)
    _val(mpe, "FirstNoteChannel", 1)
    _val(mpe, "LastNoteChannel", 15)
    return node


def _audio_clip(parent, ids: _Ids, name: str, path: Path, relpath: str,
                start_beats: float, end_beats: float, duration: float,
                sample_rate: int, warped: bool, bpm: float,
                numerator: int = 4, denominator: int = 4):
    clip = _el(parent, "AudioClip", Id=ids.take(), Time=f"{start_beats:.10f}")
    _val(clip, "LomId", 0)
    _val(clip, "LomIdView", 0)
    _val(clip, "CurrentStart", f"{start_beats:.10f}")
    _val(clip, "CurrentEnd", f"{end_beats:.10f}")
    loop = _el(clip, "Loop")
    _val(loop, "LoopStart", 0)
    _val(loop, "LoopEnd", f"{end_beats - start_beats:.10f}")
    _val(loop, "StartRelative", 0)
    _val(loop, "LoopOn", False)
    _val(loop, "OutMarker", f"{end_beats - start_beats:.10f}")
    _val(loop, "HiddenLoopStart", 0)
    _val(loop, "HiddenLoopEnd", f"{end_beats - start_beats:.10f}")
    _val(clip, "Name", name)
    _val(clip, "Annotation", "")
    _val(clip, "ColorIndex", COLOR_INDEX.get(name.lower(), 45))
    _val(clip, "LaunchMode", 0)
    _val(clip, "LaunchQuantisation", 0)
    tw = _el(clip, "TimeSignature")
    sigs = _el(tw, "TimeSignatures")
    rt = _el(sigs, "RemoteableTimeSignature", Id=ids.take())
    _val(rt, "Numerator", numerator)
    _val(rt, "Denominator", denominator)
    _val(rt, "Time", 0)
    _el(clip, "Envelopes")
    sc = _el(clip, "ScrollerTimePreserver")
    _val(sc, "LeftTime", 0)
    _val(sc, "RightTime", f"{end_beats - start_beats:.10f}")
    tsel = _el(clip, "TimeSelection")
    _val(tsel, "AnchorTime", 0)
    _val(tsel, "OtherTime", 0)
    _val(clip, "Legato", False)
    _val(clip, "Ram", False)
    _val(clip, "GrooveSettings", "")
    _val(clip, "Disabled", False)
    _val(clip, "VelocityAmount", 0)
    _val(clip, "FollowTime", 4)
    _val(clip, "FollowActionA", 4)
    _val(clip, "FollowActionB", 0)
    _val(clip, "FollowChanceA", 1)
    _val(clip, "FollowChanceB", 0)
    _val(clip, "Grid", 4)

    sample = _el(clip, "SampleRef")
    fileref = _el(sample, "FileRef")
    _val(fileref, "RelativePathType", 3)
    _val(fileref, "RelativePath", relpath)
    _val(fileref, "Path", str(path))
    _val(fileref, "Type", 1)
    _val(fileref, "LivePackName", "")
    _val(fileref, "LivePackId", "")
    _val(fileref, "OriginalFileSize", path.stat().st_size if path.exists() else 0)
    _val(fileref, "OriginalCrc", 0)
    _val(sample, "LastModDate", 0)
    _el(sample, "SourceContext")
    _val(sample, "SampleUsageHint", 0)
    _val(sample, "DefaultDuration", int(duration * sample_rate))
    _val(sample, "DefaultSampleRate", sample_rate)

    _val(clip, "OnsetSpread", 0)
    _val(clip, "WarpMode", 4)
    _val(clip, "GranularityTones", 30)
    _val(clip, "GranularityTexture", 65)
    _val(clip, "FluctuationTexture", 25)
    _val(clip, "ComplexProFormants", 100)
    _val(clip, "ComplexProEnvelope", 128)
    _val(clip, "TransientResolution", 6)
    _val(clip, "TransientLoopMode", 2)
    _val(clip, "TransientEnvelope", 100)
    _val(clip, "IsWarped", warped)
    _val(clip, "TakeId", 1)

    # Two warp markers pin the file to the grid at the project tempo.
    markers = _el(clip, "WarpMarkers")
    _el(markers, "WarpMarker", Id=str(ids.take()), SecTime="0", BeatTime="0")
    _el(markers, "WarpMarker", Id=str(ids.take()),
        SecTime=f"{duration:.10f}", BeatTime=f"{duration * bpm / 60.0:.10f}")
    _val(clip, "SavedWarpMarkersForStretched", "")
    _val(clip, "MarkersGenerated", True)
    _val(clip, "IsSongTempoMaster", False)
    return clip


def _audio_track(tracks, ids: _Ids, stem: Stem, grid: BeatGrid, project_dir: Path,
                 start_beats: float, end_beats: float, sample_rate: int):
    track = _el(tracks, "AudioTrack", Id=ids.take())
    _val(track, "LomId", 0)
    _val(track, "LomIdView", 0)
    _val(track, "IsContentSelectedInDocument", False)
    _val(track, "PreferredContentViewMode", 0)
    delay = _el(track, "TrackDelay")
    _val(delay, "Value", 0)
    _val(delay, "IsValueSampleBased", False)
    name = _el(track, "Name")
    label = stem.name.replace("_", " ").title()
    _val(name, "EffectiveName", label)
    _val(name, "UserName", label)
    _val(name, "Annotation", "")
    _val(name, "MemorizedFirstClipName", Path(stem.relpath).name)
    _val(track, "Color", COLOR_INDEX.get(stem.name, 45))
    env = _el(track, "AutomationEnvelopes")
    _el(env, "Envelopes")
    _val(track, "TrackGroupId", -1)
    _val(track, "TrackUnfolded", True)
    _val(track, "DevicesListWrapper", "")
    _val(track, "ClipSlotsListWrapper", "")
    _val(track, "ViewData", "{}")
    _el(track, "TakeLanes")
    _val(track, "LinkedTrackGroupId", -1)
    _val(track, "SavedPlayingSlot", -1)
    _val(track, "SavedPlayingOffset", 0)
    _val(track, "Freeze", False)
    _val(track, "VelocityDetail", 0)
    _val(track, "NeedArrangerRefreeze", True)
    _val(track, "PostProcessFreezeClips", 0)

    chain = _el(track, "DeviceChain")
    _el(chain, "AutomationLanes")
    _routing(chain, "AudioInputRouting", "AudioIn/External/S0", "Ext. In", "1/2")
    _routing(chain, "MidiInputRouting", "MidiIn/External.All/-1", "Ext: All Ins", "")
    _routing(chain, "AudioOutputRouting", "AudioOut/Main", "Main", "")
    _routing(chain, "MidiOutputRouting", "MidiOut/None", "None", "")
    _mixer(chain, ids)
    _el(chain, "Devices")  # Live expects the node, empty is fine

    seq = _el(chain, "MainSequencer")
    _val(seq, "LomId", 0)
    _el(seq, "ClipSlotList")
    _val(seq, "MonitoringEnum", 1)
    sample = _el(seq, "Sample")
    arranger = _el(sample, "ArrangerAutomation")
    events = _el(arranger, "Events")
    _audio_clip(events, ids, stem.name, stem.path,
                Path(stem.relpath).as_posix(), start_beats, end_beats,
                stem.duration, sample_rate, warped=True, bpm=grid.initial_bpm,
                numerator=grid.beats_per_bar, denominator=grid.beat_unit)
    freeze = _el(chain, "FreezeSequencer")
    _val(freeze, "LomId", 0)
    _el(freeze, "ClipSlotList")
    _val(freeze, "MonitoringEnum", 0)
    return track


def _master_track(parent, ids: _Ids, grid: BeatGrid):
    track = _el(parent, "MasterTrack")
    _val(track, "LomId", 0)
    _val(track, "LomIdView", 0)
    _val(track, "IsContentSelectedInDocument", False)
    _val(track, "PreferredContentViewMode", 0)
    delay = _el(track, "TrackDelay")
    _val(delay, "Value", 0)
    _val(delay, "IsValueSampleBased", False)
    name = _el(track, "Name")
    _val(name, "EffectiveName", "Main")
    _val(name, "UserName", "")
    _val(name, "Annotation", "")
    _val(name, "MemorizedFirstClipName", "")
    _val(track, "Color", -1)
    env = _el(track, "AutomationEnvelopes")
    _el(env, "Envelopes")
    _val(track, "TrackGroupId", -1)
    _val(track, "TrackUnfolded", True)
    _val(track, "DevicesListWrapper", "")
    _val(track, "ClipSlotsListWrapper", "")
    _val(track, "ViewData", "{}")

    chain = _el(track, "DeviceChain")
    _el(chain, "AutomationLanes")
    mixer = _mixer(chain, ids)
    tempo = _el(mixer, "Tempo")
    _val(tempo, "LomId", 0)
    _val(tempo, "Manual", f"{grid.initial_bpm:.6f}")
    midi = _el(tempo, "MidiControllerRange")
    _val(midi, "Min", 60)
    _val(midi, "Max", 200)
    _pointee(tempo, ids)
    _val(tempo, "ModulationTarget", 0)
    sig = _el(mixer, "TimeSignature")
    _val(sig, "LomId", 0)
    _val(sig, "Manual", _live_time_signature(grid))
    _pointee(sig, ids)
    _el(chain, "Devices")
    return track


def _live_time_signature(grid: BeatGrid) -> int:
    """Live encodes a signature as ``(numerator - 1) + 99 * log2(denominator)``."""
    import math
    denom_pow = int(round(math.log2(grid.beat_unit)))
    return (grid.beats_per_bar - 1) + 99 * denom_pow


def _locators(parent, grid: BeatGrid, sections: Sequence[Section], ids: _Ids):
    locators = _el(parent, "Locators")
    inner = _el(locators, "Locators")
    if grid.shift > 1e-3:
        node = _el(inner, "Locator", Id=str(ids.take()))
        _val(node, "LomId", 0)
        _val(node, "Time", 0)
        _val(node, "Name", "COUNT-IN")
        _val(node, "Annotation", "")
        _val(node, "IsSongStart", False)
    for section in sections:
        node = _el(inner, "Locator", Id=str(ids.take()))
        _val(node, "LomId", 0)
        _val(node, "Time", f"{_seconds_to_beats(grid, section.start):.10f}")
        _val(node, "Name", section.label)
        _val(node, "Annotation", "")
        _val(node, "IsSongStart", False)
    return locators


def write_project(path: Path, stems: Sequence[Stem], grid: BeatGrid,
                  sections: Sequence[Section], duration: float,
                  click_wav: Optional[str] = None,
                  sample_rate: int = 44100) -> Path:
    ids = _Ids(8)
    root = ET.Element("Ableton", {
        "MajorVersion": MAJOR_VERSION,
        "MinorVersion": MINOR_VERSION,
        "SchemaChangeCount": SCHEMA_CHANGE_COUNT,
        "Creator": CREATOR,
        "Revision": "",
    })
    live = _el(root, "LiveSet")
    _val(live, "NextPointeeId", 40000)
    _val(live, "OverwriteProtectionNumber", 2819)
    _val(live, "LomId", 0)
    _val(live, "LomIdView", 0)
    tracks = _el(live, "Tracks")

    project_dir = path.parent
    start_beats = _seconds_to_beats(grid, grid.shift)
    end_beats = _seconds_to_beats(grid, grid.shift + duration)

    all_stems = list(stems)
    if click_wav:
        click_path = project_dir / click_wav
        all_stems.insert(0, Stem(name="click", path=click_path, relpath=click_wav,
                                 duration=duration, color="#9aa0a6"))
    for stem in all_stems:
        offset = 0.0 if stem.name == "click" else start_beats
        _audio_track(tracks, ids, stem, grid, project_dir, offset,
                     offset + (end_beats - start_beats), sample_rate)

    _master_track(live, ids, grid)
    _locators(live, grid, sections, ids)
    _val(live, "SignalVersion", 3)
    _val(live, "GlobalQuantisation", 4)
    _val(live, "AutoQuantisation", 0)
    sig = _el(live, "TimeSelection")
    _val(sig, "AnchorTime", 0)
    _val(sig, "OtherTime", 0)
    _val(live, "SequencerNavigator", "")
    _val(live, "AreArrangementLocked", False)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    xml = ET.tostring(root, encoding="unicode")
    payload = ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n").encode("utf-8")

    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    log.info("Wrote %s (Live set, experimental - see SONG-NOTES.md)", path)
    return path
