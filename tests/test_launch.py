import pytest

from reaperlive.gui.launch import KNOWN_APPS, find_app, open_command, platform_key


def test_platform_key_normalises():
    assert platform_key("win32") == "win32"
    assert platform_key("windows") == "win32"
    assert platform_key("darwin") == "darwin"
    assert platform_key("linux") == "linux"
    assert platform_key("freebsd13") == "linux"


def test_macos_falls_back_to_the_default_handler(tmp_path):
    project = tmp_path / "song.rpp"
    project.touch()
    assert open_command(project, None, "darwin") == ["open", str(project)]


def test_windows_uses_the_os_association(tmp_path):
    project = tmp_path / "song.rpp"
    project.touch()
    assert open_command(project, None, "win32") is None


def test_linux_uses_xdg_open(tmp_path):
    project = tmp_path / "song.als"
    project.touch()
    assert open_command(project, None, "linux") == ["xdg-open", str(project)]


def test_named_app_is_used_when_installed(tmp_path, monkeypatch):
    fake = tmp_path / "REAPER.app"
    fake.mkdir()
    monkeypatch.setitem(KNOWN_APPS["reaper"], "darwin", [str(fake)])
    assert find_app("reaper", "darwin") == str(fake)
    project = tmp_path / "song.rpp"
    project.touch()
    assert open_command(project, "reaper", "darwin") == [
        "open", "-a", str(fake), str(project)]


def test_missing_app_is_not_invented(monkeypatch):
    monkeypatch.setitem(KNOWN_APPS["ableton"], "darwin", ["/nope/Live.app"])
    assert find_app("ableton", "darwin") is None


def test_open_project_rejects_a_missing_file(tmp_path):
    from reaperlive.gui.launch import open_project
    with pytest.raises(RuntimeError):
        open_project(tmp_path / "gone.rpp")
