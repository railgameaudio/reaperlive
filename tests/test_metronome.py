import pytest

from fixtures import accelerating_loop, drum_loop
from reaperlive.analysis.tempo import build_grid
from reaperlive.config import ClickOptions, TempoOptions
from reaperlive.render.metronome import (
    click_times, reaper_midi_events, render_click_wav, write_midi_file,
)
from reaperlive.render.validate import _validate_midi_events, validate_midi_file


def grid_for(tmp_path, bpm=120.0, bars=8, **kwargs):
    path = drum_loop(tmp_path / "loop.wav", bpm=bpm, bars=bars)
    return build_grid(path, TempoOptions(**kwargs)), path


def test_click_starts_on_an_accented_downbeat(tmp_path):
    grid, _ = grid_for(tmp_path)
    clicks = click_times(grid, 20.0)
    assert clicks[0][0] == pytest.approx(0.0, abs=1e-6)
    assert clicks[0][1] is True


def test_count_in_is_exactly_one_bar(tmp_path):
    grid, _ = grid_for(tmp_path)
    clicks = click_times(grid, 20.0)
    before = [t for t, _ in clicks if t < grid.downbeat_times[0] - 1e-6]
    assert len(before) == grid.beats_per_bar


def test_two_bar_count_in(tmp_path):
    grid, _ = grid_for(tmp_path, lead_in_bars=2)
    clicks = click_times(grid, 20.0)
    before = [t for t, _ in clicks if t < grid.downbeat_times[0] - 1e-6]
    assert len(before) == 2 * grid.beats_per_bar


def test_every_bar_line_is_accented(tmp_path):
    grid, _ = grid_for(tmp_path)
    clicks = click_times(grid, 20.0)
    accents = [i for i, (_, accent) in enumerate(clicks) if accent]
    assert accents == list(range(0, len(clicks), grid.beats_per_bar))


def test_clicks_stay_on_the_beat_when_the_tempo_moves(tmp_path):
    path, true_bars = accelerating_loop(tmp_path / "accel.wav", 100.0, 130.0, bars=12)
    grid = build_grid(path, TempoOptions())
    clicks = click_times(grid, 40.0)
    accents = [t - grid.shift for t, accent in clicks if accent]
    for bar in true_bars[1:-1]:
        assert min(abs(a - bar) for a in accents) < 0.05


def test_click_covers_the_whole_project(tmp_path):
    grid, _ = grid_for(tmp_path, bars=8)
    duration = 20.0
    clicks = click_times(grid, duration)
    assert clicks[-1][0] < duration
    assert duration - clicks[-1][0] < 60.0 / grid.bpm + 1e-6


def test_reaper_midi_events_are_well_formed(tmp_path):
    grid, _ = grid_for(tmp_path)
    events = reaper_midi_events(grid, 20.0, ClickOptions())
    assert _validate_midi_events(events) == []
    assert events[-1] == "E 0 b0 7b 00"
    assert events[0].startswith("E 0 90 54")  # first beat is the accent note


def test_midi_channel_is_honoured(tmp_path):
    grid, _ = grid_for(tmp_path)
    events = reaper_midi_events(grid, 20.0, ClickOptions(channel=10))
    assert events[0].split()[2] == "99"


def test_midi_file_round_trips(tmp_path):
    grid, _ = grid_for(tmp_path)
    path = write_midi_file(tmp_path / "click.mid", grid, 20.0, ClickOptions())
    assert validate_midi_file(path) == []


def test_variable_tempo_midi_file_round_trips(tmp_path):
    path, _ = accelerating_loop(tmp_path / "accel.wav", 100.0, 130.0, bars=12)
    grid = build_grid(path, TempoOptions())
    out = write_midi_file(tmp_path / "click.mid", grid, 40.0, ClickOptions())
    assert validate_midi_file(out) == []


def test_click_wav_has_a_transient_on_every_beat(tmp_path):
    import numpy as np
    import soundfile as sf

    grid, _ = grid_for(tmp_path)
    path = render_click_wav(tmp_path / "click.wav", grid, 20.0)
    audio, sr = sf.read(str(path))
    for when, _ in click_times(grid, 20.0):
        window = audio[int(when * sr):int(when * sr) + int(0.05 * sr)]
        assert np.abs(window).max() > 0.05
