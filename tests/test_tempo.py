import numpy as np
import pytest

from fixtures import accelerating_loop, drum_loop
from reaperlive.analysis.tempo import bar_lines, build_grid, snap
from reaperlive.config import TempoOptions


@pytest.mark.parametrize("bpm", [92.0, 104.0, 120.0, 143.0])
def test_detects_steady_tempo(tmp_path, bpm):
    path = drum_loop(tmp_path / "loop.wav", bpm=bpm, bars=12)
    grid = build_grid(path, TempoOptions())
    assert grid.initial_bpm == pytest.approx(bpm, rel=0.01)


@pytest.mark.parametrize("bpm", [92.0, 120.0])
def test_downbeats_land_on_bar_lines(tmp_path, bpm):
    path = drum_loop(tmp_path / "loop.wav", bpm=bpm, bars=12)
    grid = build_grid(path, TempoOptions())
    true_bars = 0.75 + np.arange(12) * (4 * 60.0 / bpm)
    detected = np.asarray(grid.downbeat_times) - grid.shift
    inside = [d for d in detected if d <= true_bars[-1] + 0.05]
    assert len(inside) >= 11
    for when in inside:
        assert min(abs(true_bars - when)) < 0.030  # within 30 ms of the real bar


def test_steady_tempo_gives_one_marker(tmp_path):
    path = drum_loop(tmp_path / "loop.wav", bpm=120.0, bars=16)
    grid = build_grid(path, TempoOptions())
    assert len(grid.tempo_points) == 1
    assert grid.tempo_points[0].time_signature == (4, 4)


def test_variable_tempo_gives_a_ramp(tmp_path):
    path, _ = accelerating_loop(tmp_path / "accel.wav", 100.0, 130.0, bars=16)
    grid = build_grid(path, TempoOptions())
    assert len(grid.tempo_points) >= 4
    tempi = [point.bpm for point in grid.tempo_points]
    assert tempi == sorted(tempi), "an accelerando should not produce a falling map"
    assert tempi[0] == pytest.approx(100.0, rel=0.05)
    assert tempi[-1] == pytest.approx(130.0, rel=0.08)


def test_constant_mode_collapses_a_ramp(tmp_path):
    path, _ = accelerating_loop(tmp_path / "accel.wav", 100.0, 130.0, bars=16)
    grid = build_grid(path, TempoOptions(mode="constant"))
    assert len(grid.tempo_points) == 1
    assert 100.0 < grid.initial_bpm < 130.0


def test_fixed_bpm_is_respected(tmp_path):
    path = drum_loop(tmp_path / "loop.wav", bpm=120.0, bars=8)
    grid = build_grid(path, TempoOptions(fixed_bpm=97.5))
    assert grid.initial_bpm == pytest.approx(97.5)
    assert len(grid.tempo_points) == 1


def test_count_in_is_a_whole_number_of_bars(tmp_path):
    path = drum_loop(tmp_path / "loop.wav", bpm=120.0, bars=8)
    for bars in (1, 2):
        grid = build_grid(path, TempoOptions(lead_in_bars=bars))
        bar_seconds = 4 * 60.0 / grid.initial_bpm
        assert grid.downbeat_times[0] == pytest.approx(bars * bar_seconds, abs=1e-6)
        assert grid.shift >= 0


def test_half_time_detection_is_folded_into_range(tmp_path):
    path = drum_loop(tmp_path / "loop.wav", bpm=180.0, bars=12)
    grid = build_grid(path, TempoOptions(bpm_min=60.0, bpm_max=140.0))
    assert 60.0 <= grid.initial_bpm <= 140.0


def test_bar_lines_cover_the_project(tmp_path):
    path = drum_loop(tmp_path / "loop.wav", bpm=120.0, bars=8)
    grid = build_grid(path, TempoOptions())
    lines = bar_lines(grid, until=40.0)
    assert lines[0] == pytest.approx(0.0, abs=1e-6)
    assert lines[-1] >= 38.0
    assert snap(9.9, lines) == pytest.approx(10.0, abs=0.1)
