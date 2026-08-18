import numpy as np
import pytest
import soundfile as sf

from fixtures import drum_loop
from reaperlive.analysis.structure import detect_sections, vocal_activity
from reaperlive.analysis.tempo import build_grid
from reaperlive.config import StructureOptions, TempoOptions

SR = 44100


def vocal_stem(path, bpm=120.0, bars=20, sung=((4, 8), (12, 16)), level=0.5,
               lead=0.75):
    bar = 4 * 60.0 / bpm
    duration = lead + bars * bar
    y = np.zeros(int(duration * SR))
    for start_bar, end_bar in sung:
        span = np.arange(int((lead + start_bar * bar) * SR),
                         int((lead + end_bar * bar) * SR))
        t = (span - span[0]) / SR
        y[span] = level * np.sin(2 * np.pi * 220 * t)
    sf.write(str(path), y, SR, subtype="PCM_16")
    return path, duration


def test_sections_follow_the_singing(tmp_path):
    stem, duration = vocal_stem(tmp_path / "vocals.wav")
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=20)
    grid = build_grid(mix, TempoOptions())
    sections = detect_sections(stem, duration, grid, StructureOptions())

    labels = [s.label.split()[0] for s in sections]
    assert labels == ["INTRO", "VOCALS", "BREAK", "VOCALS", "OUTRO"]

    bar = 4 * 60.0 / grid.initial_bpm
    sung = [s for s in sections if s.has_vocals]
    assert (sung[0].start - grid.shift) == pytest.approx(0.75 + 4 * bar, abs=0.15)
    assert (sung[1].start - grid.shift) == pytest.approx(0.75 + 12 * bar, abs=0.15)


def test_sections_are_contiguous_and_ordered(tmp_path):
    stem, duration = vocal_stem(tmp_path / "vocals.wav")
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=20)
    grid = build_grid(mix, TempoOptions())
    sections = detect_sections(stem, duration, grid, StructureOptions())
    for earlier, later in zip(sections, sections[1:]):
        assert earlier.end == pytest.approx(later.start, abs=1e-6)
        assert earlier.duration > 0


def test_sections_snap_to_bar_lines(tmp_path):
    stem, duration = vocal_stem(tmp_path / "vocals.wav")
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=20)
    grid = build_grid(mix, TempoOptions())
    sections = detect_sections(stem, duration, grid, StructureOptions())
    bar = 4 * 60.0 / grid.initial_bpm
    for section in sections[1:]:
        assert (section.start / bar) == pytest.approx(round(section.start / bar), abs=0.02)


def test_silent_stem_reads_as_instrumental(tmp_path):
    path = tmp_path / "vocals.wav"
    sf.write(str(path), np.zeros(SR * 20) + 1e-4, SR, subtype="PCM_16")
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=10)
    grid = build_grid(mix, TempoOptions())
    assert detect_sections(path, 20.0, grid, StructureOptions()) == []


def test_flat_noisy_stem_reads_as_instrumental(tmp_path):
    """A stem with no dynamic range must not be read as wall-to-wall vocals."""
    rng = np.random.default_rng(0)
    path = tmp_path / "vocals.wav"
    sf.write(str(path), rng.normal(0, 0.002, SR * 20), SR, subtype="PCM_16")
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=10)
    grid = build_grid(mix, TempoOptions())
    assert detect_sections(path, 20.0, grid, StructureOptions()) == []


def test_disabled_structure_returns_nothing(tmp_path):
    stem, duration = vocal_stem(tmp_path / "vocals.wav")
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=20)
    grid = build_grid(mix, TempoOptions())
    assert detect_sections(stem, duration, grid,
                           StructureOptions(enabled=False)) == []


def test_missing_stem_returns_nothing(tmp_path):
    mix = drum_loop(tmp_path / "mix.wav", bpm=120.0, bars=8)
    grid = build_grid(mix, TempoOptions())
    assert detect_sections(None, 10.0, grid, StructureOptions()) == []
    assert detect_sections(tmp_path / "nope.wav", 10.0, grid, StructureOptions()) == []


def test_vocal_activity_marks_the_sung_span(tmp_path):
    stem, _ = vocal_stem(tmp_path / "vocals.wav", sung=((4, 8),))
    active, times = vocal_activity(stem, 32.0)
    assert active.any()
    sung = times[active]
    assert sung.min() == pytest.approx(0.75 + 8.0, abs=0.3)
    assert sung.max() == pytest.approx(0.75 + 16.0, abs=0.3)
