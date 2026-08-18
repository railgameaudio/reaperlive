"""Open finished projects in a DAW, or show them in the file manager.

The tool is standalone: nothing runs inside REAPER or Live. When a build
finishes, we hand the project file to the OS, which opens whichever DAW is
registered for .rpp / .als.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

log = logging.getLogger(__name__)

#: Where each DAW usually lives, when it is not registered for the file type.
KNOWN_APPS = {
    "reaper": {
        "darwin": ["/Applications/REAPER.app", "/Applications/REAPER64.app"],
        "win32": [r"C:\Program Files\REAPER (x64)\reaper.exe",
                  r"C:\Program Files\REAPER\reaper.exe"],
        "linux": ["/usr/bin/reaper", "/opt/REAPER/reaper"],
    },
    "ableton": {
        "darwin": ["/Applications/Ableton Live 12 Suite.app",
                   "/Applications/Ableton Live 12 Standard.app",
                   "/Applications/Ableton Live 11 Suite.app",
                   "/Applications/Ableton Live 11 Standard.app"],
        "win32": [r"C:\ProgramData\Ableton\Live 12 Suite\Program\Ableton Live 12 Suite.exe",
                  r"C:\ProgramData\Ableton\Live 11 Suite\Program\Ableton Live 11 Suite.exe"],
        "linux": [],
    },
}


def platform_key(platform: Optional[str] = None) -> str:
    name = platform or sys.platform
    if name.startswith("win"):
        return "win32"
    if name == "darwin":
        return "darwin"
    return "linux"


def find_app(daw: str, platform: Optional[str] = None) -> Optional[str]:
    """Locate an installed DAW, or None if we cannot find one."""
    key = platform_key(platform)
    for candidate in KNOWN_APPS.get(daw, {}).get(key, []):
        if Path(candidate).exists():
            return candidate
    if key == "linux" and daw == "reaper":
        return shutil.which("reaper")
    return None


def open_command(path: Path, daw: Optional[str] = None,
                 platform: Optional[str] = None) -> Optional[Sequence[str]]:
    """The argv that opens ``path``, or None when the OS API should be used."""
    key = platform_key(platform)
    app = find_app(daw, platform) if daw else None

    if key == "darwin":
        if app:
            return ["open", "-a", app, str(path)]
        return ["open", str(path)]
    if key == "win32":
        if app:
            return [app, str(path)]
        return None  # os.startfile handles the default association
    if app:
        return [app, str(path)]
    return ["xdg-open", str(path)]


def open_project(path: Path, daw: Optional[str] = None) -> None:
    """Hand the project to the DAW. Raises RuntimeError if nothing can open it."""
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"{path} does not exist")

    command = open_command(path, daw)
    if command is None:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
        return
    if shutil.which(command[0]) is None and not Path(command[0]).exists():
        raise RuntimeError(
            f"Could not find {command[0]!r} to open {path.name}. "
            "Open the project from your DAW instead."
        )
    log.info("Opening %s", path.name)
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal(path: Path) -> None:
    """Show the file or folder in Finder / Explorer / the desktop file manager."""
    path = Path(path)
    key = platform_key()
    target = path if path.is_dir() else path.parent
    if key == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif key == "win32":
        if path.is_dir():
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        subprocess.Popen(["xdg-open", str(target)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
