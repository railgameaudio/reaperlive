"""Separator plug-in interface."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional, Protocol

#: Track colours by stem name, reused by both DAW writers.
STEM_COLORS = {
    "vocals": "#e8483f",
    "drums": "#f0a52c",
    "bass": "#6f5bd6",
    "other": "#2f9e8f",
    "guitar": "#3d7fd6",
    "piano": "#c265c9",
    "no_vocals": "#5f6c7b",
    "instrumental": "#5f6c7b",
    "mix": "#8a8f98",
    "click": "#9aa0a6",
}

#: A tidy running order for the track list.
STEM_ORDER = ["vocals", "drums", "bass", "guitar", "piano", "other",
              "no_vocals", "instrumental"]


class Separator(Protocol):
    name: str

    def separate(self, mix: Path, outdir: Path,
                 cancel: Optional[threading.Event] = None,
                 on_percent: Optional[Callable[[int], None]] = None
                 ) -> dict[str, Path]:
        """Split ``mix`` and return {stem name: wav path}.

        ``on_percent`` is called with 0-100 while the work is under way, so a
        UI can show movement during a separation that runs for minutes.
        """


def sort_stems(stems: dict[str, Path]) -> list[tuple[str, Path]]:
    def key(item: tuple[str, Path]) -> tuple[int, str]:
        name = item[0]
        return (STEM_ORDER.index(name) if name in STEM_ORDER else len(STEM_ORDER), name)

    return sorted(stems.items(), key=key)


def color_for(stem: str) -> str:
    return STEM_COLORS.get(stem, "#7a8290")
