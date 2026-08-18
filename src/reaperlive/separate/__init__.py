"""Stem separation backends."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from reaperlive.config import SeparationOptions
from reaperlive.separate.base import Separator, color_for, sort_stems


class NullSeparator:
    """No separation - the project gets the untouched mix on a single track."""

    name = "none"

    def __init__(self, options: SeparationOptions | None = None):
        self.options = options

    def available(self) -> bool:
        return True

    def separate(self, mix: Path, outdir: Path,
                 cancel: Optional[threading.Event] = None,
                 on_percent: Optional[Callable[[int], None]] = None
                 ) -> dict[str, Path]:
        return {}


def get_separator(options: SeparationOptions):
    backend = options.backend.lower()
    if backend in ("none", "off", "skip"):
        return NullSeparator(options)
    if backend == "demucs":
        from reaperlive.separate.demucs_backend import DemucsSeparator
        return DemucsSeparator(options)
    if backend in ("roformer", "audio-separator", "mdx"):
        from reaperlive.separate.roformer_backend import RoformerSeparator
        return RoformerSeparator(options)
    raise ValueError(f"Unknown separator backend: {options.backend!r}")


__all__ = ["get_separator", "Separator", "NullSeparator", "sort_stems", "color_for"]
