"""Drives the real window. Skipped where there is no display or no Tkinter."""

import time

import pytest

tk = pytest.importorskip("tkinter", reason="Tkinter is not available")

from fixtures import drum_loop  # noqa: E402


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no display: {exc}")
    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture
def app(root, tmp_path, monkeypatch):
    from reaperlive.gui import app as app_module

    monkeypatch.setattr(app_module, "settings_path",
                        lambda: tmp_path / "settings.json")
    instance = app_module.App(root)
    instance.outdir.set(str(tmp_path / "out"))
    return instance


def pump(root, app, timeout=180.0):
    """Run the Tk event loop until the build finishes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if app.job and not app.job.running and app.job.messages.empty():
            root.update()
            return True
        time.sleep(0.02)
    return False


def test_window_builds(app):
    assert str(app.build_button["state"]) == "normal"
    assert str(app.cancel_button["state"]) == "disabled"
    assert str(app.open_reaper["state"]) == "disabled"


def test_argv_matches_the_chosen_options(app, tmp_path):
    app.source.set("https://youtu.be/abc")
    app.name.set("My Song")
    app.target.set("both")
    app.tempo_box.set("One tempo for the song")
    app.bpm.set("128")
    app.time_sig.set("3/4")
    app.lead_in.set("2")
    app.vocals_only.set(True)
    argv = app.argv()

    assert argv[0] == "https://youtu.be/abc"
    assert argv[argv.index("-t") + 1] == "both"
    assert argv[argv.index("--tempo-mode") + 1] == "constant"
    assert argv[argv.index("--bpm") + 1] == "128"
    assert argv[argv.index("--time-sig") + 1] == "3/4"
    assert argv[argv.index("--lead-in-bars") + 1] == "2"
    assert argv[argv.index("--two-stems") + 1] == "vocals"
    assert "--no-markers" not in argv


def test_argv_is_accepted_by_the_cli_parser(app):
    from reaperlive.cli import build_parser, options_from_args

    app.source.set("/tmp/song.wav")
    app.separator_box.set("None - just the mix")
    options = options_from_args(build_parser().parse_args(app.argv()))
    assert options.separation.backend == "none"
    assert options.source == "/tmp/song.wav"


def test_disabling_markers_shows_up(app):
    app.source.set("/tmp/song.wav")
    app.mark_sections.set(False)
    assert "--no-markers" in app.argv()


def test_model_box_greys_out_without_separation(app):
    app.separator_box.set("None - just the mix")
    app._on_separator()
    assert str(app.model_box["state"]) == "disabled"
    app.separator_box.set("Demucs (Meta)")
    app._on_separator()
    assert str(app.model_box["state"]) == "readonly"


def test_empty_source_is_refused(app, monkeypatch):
    from reaperlive.gui import app as app_module

    warned = []
    monkeypatch.setattr(app_module.messagebox, "showwarning",
                        lambda *a, **k: warned.append(a))
    app.source.set("")
    app.start_build()
    assert warned and app.job is None


def test_a_full_build_finishes_and_enables_open(root, app, tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=8)
    app.source.set(str(source))
    app.name.set("Gui Song")
    app.separator_box.set("None - just the mix")
    app.start_build()
    assert str(app.build_button["state"]) == "disabled"
    assert pump(root, app), "the build did not finish in time"

    assert app.result is not None
    assert app.result.project_dir == tmp_path / "out" / "gui-song"
    assert (app.result.project_dir / "gui-song.rpp").exists()
    assert str(app.open_reaper["state"]) == "normal"
    assert str(app.open_ableton["state"]) == "disabled"  # REAPER-only build
    assert str(app.show_folder["state"]) == "normal"
    assert str(app.build_button["state"]) == "normal"
    assert "120" in app.status.get()
    assert "Finished" in app.log.get("1.0", "end")


def test_both_targets_enable_both_buttons(root, app, tmp_path):
    source = drum_loop(tmp_path / "song.wav", bpm=120.0, bars=6)
    app.source.set(str(source))
    app.name.set("Two Daws")
    app.target.set("both")
    app.separator_box.set("None - just the mix")
    app.start_build()
    assert pump(root, app)
    assert str(app.open_reaper["state"]) == "normal"
    assert str(app.open_ableton["state"]) == "normal"


def test_a_failed_build_is_reported(root, app, tmp_path, monkeypatch):
    from reaperlive.gui import app as app_module

    errors = []
    monkeypatch.setattr(app_module.messagebox, "showerror",
                        lambda *a, **k: errors.append(a))
    app.source.set(str(tmp_path / "missing.wav"))
    app.separator_box.set("None - just the mix")
    app.start_build()
    assert pump(root, app, timeout=60)
    assert errors
    assert app.result is None
    assert str(app.build_button["state"]) == "normal"
    assert "error" in app.log.get("1.0", "end").lower()


def test_settings_survive_a_restart(root, app, tmp_path, monkeypatch):
    from reaperlive.gui import app as app_module

    app.target.set("ableton")
    app.time_sig.set("6/8")
    app.lead_in.set("3")
    app._save_settings()

    fresh = app_module.App(root)
    assert fresh.target.get() == "ableton"
    assert fresh.time_sig.get() == "6/8"
    assert fresh.lead_in.get() == "3"


def test_progress_bar_follows_stage_fraction(app):
    from reaperlive.gui.runner import StageMessage
    from reaperlive.progress import Stage

    app._handle(StageMessage(Stage("Separating stems", 2, 6, 0.25, " - 25%")))
    assert app.progress["value"] == pytest.approx(1000 * (1.25 / 6), abs=1)
    assert app.status.get() == "Step 2 of 6 - Separating stems - 25%"

    app._handle(StageMessage(Stage("Separating stems", 2, 6, 1.0, " - 100%")))
    assert app.progress["value"] == pytest.approx(1000 * (2 / 6), abs=1)


def test_warnings_are_marked_in_the_log(app):
    import logging

    from reaperlive.gui.runner import LogMessage

    app._handle(LogMessage("something looks off", logging.WARNING))
    assert "something looks off" in app.log.get("1.0", "end")
