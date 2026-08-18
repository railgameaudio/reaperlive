"""Checks on the DAW project files themselves."""

import gzip
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import soundfile as sf

from fixtures import drum_loop
from reaperlive.analysis.tempo import build_grid
from reaperlive.config import ClickOptions, Section, Stem, TempoOptions
from reaperlive.render.ableton import write_project as write_als
from reaperlive.render.metronome import render_click_wav
from reaperlive.render.reaper import native_color, quote, write_project as write_rpp
from reaperlive.render.validate import parse_rpp, validate_als, validate_rpp


@pytest.fixture
def project(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    mix = drum_loop(audio / "mix.wav", bpm=120.0, bars=12)
    grid = build_grid(mix, TempoOptions())
    duration = sf.info(str(mix)).duration
    stems = []
    for name in ("vocals", "drums", "mix"):
        path = audio / f"{name}.wav"
        if not path.exists():
            drum_loop(path, bpm=120.0, bars=12)
        stems.append(Stem(name=name, path=path, relpath=f"audio/{name}.wav",
                          duration=duration))
    render_click_wav(audio / "click.wav", grid, duration + grid.shift)
    sections = [
        Section(0.0 + grid.shift, 8.0 + grid.shift, "INTRO", False),
        Section(8.0 + grid.shift, 20.0 + grid.shift, "VOCALS 1", True),
    ]
    return tmp_path, stems, grid, sections, duration + grid.shift


def test_rpp_validates(project):
    root, stems, grid, sections, duration = project
    path = write_rpp(root / "song.rpp", stems, grid, sections, duration,
                     ClickOptions(), "markers", "audio/click.wav")
    assert validate_rpp(path) == []


def test_rpp_has_a_track_per_stem_plus_clicks(project):
    root, stems, grid, sections, duration = project
    path = write_rpp(root / "song.rpp", stems, grid, sections, duration,
                     ClickOptions(), "markers", "audio/click.wav")
    tree = parse_rpp(path.read_text())
    names = [node.field("NAME").strip('"') for node in tree.find_all("TRACK")]
    assert names[:2] == ["CLICK (MIDI)", "CLICK (audio)"]
    assert {"Vocals", "Drums", "Mix"} <= set(names)


def test_rpp_places_stems_after_the_count_in(project):
    root, stems, grid, sections, duration = project
    path = write_rpp(root / "song.rpp", stems, grid, sections, duration,
                     ClickOptions(), "markers", "audio/click.wav")
    tree = parse_rpp(path.read_text())
    for track in tree.find_all("TRACK"):
        name = track.field("NAME").strip('"')
        item = next(track.find_all("ITEM"))
        position = float(item.field("POSITION"))
        expected = 0.0 if name.startswith("CLICK") else grid.shift
        assert position == pytest.approx(expected, abs=1e-6), name


def test_rpp_project_tempo_matches_the_first_marker(project):
    root, stems, grid, sections, duration = project
    path = write_rpp(root / "song.rpp", stems, grid, sections, duration,
                     ClickOptions(), "markers", "audio/click.wav")
    tree = parse_rpp(path.read_text())
    assert float(tree.field("TEMPO").split()[0]) == pytest.approx(
        grid.initial_bpm, abs=1e-4)


def test_rpp_writes_markers_and_regions(project):
    root, stems, grid, sections, duration = project
    for style, expect_regions in (("markers", False), ("regions", True), ("both", True)):
        path = write_rpp(root / f"{style}.rpp", stems, grid, sections, duration,
                         ClickOptions(), style, None)
        lines = [line.strip() for line in path.read_text().splitlines()
                 if line.strip().startswith("MARKER ")]
        assert any("VOCALS 1" in line for line in lines)
        assert any("COUNT-IN" in line for line in lines)
        has_regions = any(line.split()[4] == "1" for line in lines)
        assert has_regions == expect_regions


def test_rpp_reports_missing_media(project):
    root, stems, grid, sections, duration = project
    path = write_rpp(root / "song.rpp", stems, grid, sections, duration,
                     ClickOptions(), "markers", "audio/click.wav")
    (root / "audio" / "vocals.wav").unlink()
    assert any("missing media" in issue for issue in validate_rpp(path))


def test_rpp_rejects_unbalanced_chunks():
    with pytest.raises(ValueError):
        parse_rpp("<REAPER_PROJECT 0.1\n  TEMPO 120 4 4\n")
    with pytest.raises(ValueError):
        parse_rpp("<REAPER_PROJECT 0.1\n>\n>\n")


def test_quote_survives_awkward_names():
    assert quote('Song "Live"') == "'Song \"Live\"'"
    assert quote("It's a song") == '"It\'s a song"'
    assert parse_rpp(f"<X\n  NAME {quote(chr(34) + chr(39))}\n>\n") is not None


def test_native_color_packs_bgr():
    assert native_color("#ff0000") == 0x1000000 | 0x0000FF
    assert native_color("#0000ff") == 0x1000000 | 0xFF0000


def test_als_validates(project):
    root, stems, grid, sections, duration = project
    path = write_als(root / "song.als", stems, grid, sections, duration,
                     "audio/click.wav")
    assert validate_als(path) == []


def test_als_carries_tempo_tracks_and_locators(project):
    root, stems, grid, sections, duration = project
    path = write_als(root / "song.als", stems, grid, sections, duration,
                     "audio/click.wav")
    tree = ET.fromstring(gzip.open(path, "rb").read())
    tempo = tree.find(".//MasterTrack//Tempo/Manual")
    assert float(tempo.get("Value")) == pytest.approx(grid.initial_bpm, abs=1e-3)
    assert len(tree.findall(".//AudioTrack")) == len(stems) + 1  # + click
    names = [el.get("Value") for el in tree.findall(".//Locator/Name")]
    assert "VOCALS 1" in names and "COUNT-IN" in names


def test_als_clip_paths_are_relative(project):
    root, stems, grid, sections, duration = project
    path = write_als(root / "song.als", stems, grid, sections, duration, None)
    tree = ET.fromstring(gzip.open(path, "rb").read())
    for ref in tree.findall(".//SampleRef/FileRef/RelativePath"):
        assert ref.get("Value").startswith("audio/")
