"""End-to-end runs. Separation is skipped so the suite stays fast."""

import json

import pytest

from fixtures import drum_loop
from reaperlive.cli import build_parser, main, options_from_args
from reaperlive.ingest.fetch import is_url, slugify
from reaperlive.pipeline import build
from reaperlive.render.validate import validate_project


def options(source, outdir, **overrides):
    argv = [str(source), "-o", str(outdir), "--separator", "none"]
    for key, value in overrides.items():
        flag = "--" + key.replace("_", "-")
        argv.append(flag)
        if value is not True:
            argv.append(str(value))
    return options_from_args(build_parser().parse_args(argv))


def test_builds_a_reaper_project(tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=12)
    result = build(options(source, tmp_path / "out", name="My Song"))

    assert result.project_dir == tmp_path / "out" / "my-song"
    assert (result.project_dir / "my-song.rpp").exists()
    assert (result.project_dir / "metronome.mid").exists()
    assert (result.project_dir / "audio" / "mix.wav").exists()
    assert (result.project_dir / "audio" / "click.wav").exists()
    assert (result.project_dir / "SONG-NOTES.md").exists()
    assert validate_project(result.project_dir) == []
    assert result.grid.initial_bpm == pytest.approx(120.0, rel=0.01)


def test_builds_both_targets(tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=110.0, bars=10)
    result = build(options(source, tmp_path / "out", name="Both", target="both"))
    assert (result.project_dir / "both.rpp").exists()
    assert (result.project_dir / "both.als").exists()
    assert validate_project(result.project_dir) == []


def test_manifest_describes_the_analysis(tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=128.0, bars=10)
    result = build(options(source, tmp_path / "out", name="Manifest"))
    data = json.loads((result.project_dir / "reaperlive.json").read_text())
    assert data["title"] == "Manifest"
    assert data["bpm"] == pytest.approx(128.0, rel=0.01)
    assert data["time_signature"] == "4/4"
    assert data["tempo_markers"]
    assert any(stem["name"] == "mix" for stem in data["stems"])


def test_without_separation_the_mix_is_the_only_stem(tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=8)
    result = build(options(source, tmp_path / "out", name="Solo"))
    assert [stem.name for stem in result.stems] == ["mix"]


def test_fixed_bpm_and_time_signature_flow_through(tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=8, beats_per_bar=3)
    result = build(options(source, tmp_path / "out", name="Waltz",
                           bpm=120, time_sig="3/4"))
    assert result.grid.beats_per_bar == 3
    assert result.grid.initial_bpm == pytest.approx(120.0)
    data = json.loads((result.project_dir / "reaperlive.json").read_text())
    assert data["time_signature"] == "3/4"


def test_cli_reports_success(tmp_path, capsys):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=8)
    code = main([str(source), "-o", str(tmp_path / "out"), "--separator", "none",
                 "-n", "Cli Song", "-q"])
    assert code == 0
    assert "Cli Song" in capsys.readouterr().out


def test_cli_reports_a_missing_file(tmp_path, capsys):
    code = main([str(tmp_path / "nope.wav"), "-o", str(tmp_path / "out"), "-q"])
    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_url_detection():
    assert is_url("https://www.youtube.com/watch?v=abc")
    assert is_url("http://example.com/a.mp3")
    assert not is_url("/home/me/song.wav")
    assert not is_url("song.wav")


def test_slugify_makes_safe_names():
    assert slugify("Björk - Army of Me (Live!)") == "björk-army-of-me-live"
    assert slugify("///") == "song"
    assert "/" not in slugify("AC/DC - Back in Black")


def test_stage_progress_is_reported_in_order(tmp_path):
    from reaperlive.progress import STAGES

    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=8)
    seen = []
    build(options(source, tmp_path / "out", name="Stages"), on_stage=seen.append)
    assert [stage.label for stage in seen] == STAGES
    assert [stage.index for stage in seen] == list(range(1, len(STAGES) + 1))
    overall = [stage.overall for stage in seen]
    assert overall == sorted(overall)


def test_a_cancelled_build_stops(tmp_path):
    import threading

    from reaperlive.progress import Cancelled

    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=8)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        build(options(source, tmp_path / "out", name="Nope"), cancel=cancel)


def test_result_exposes_project_paths(tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=6)
    result = build(options(source, tmp_path / "out", name="Paths", target="both"))
    assert result.reaper_project.suffix == ".rpp"
    assert result.ableton_project.suffix == ".als"
