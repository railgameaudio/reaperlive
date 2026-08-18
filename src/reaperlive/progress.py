"""Cancellation and progress reporting, shared by the CLI and the GUI."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

STAGES = [
    "Loading audio",
    "Separating stems",
    "Finding the tempo",
    "Marking sections",
    "Building the metronome",
    "Writing the project",
]


@dataclass
class Stage:
    """Where the build has got to."""

    label: str
    index: int
    total: int
    #: How far through this particular stage we are, 0.0 to 1.0. Only the slow
    #: stages (separation) report it; the rest jump straight from 0 to done.
    fraction: float = 0.0
    #: Extra text for the status line, e.g. " - 45%".
    detail: str = ""

    @property
    def overall(self) -> float:
        """Progress across the whole build, 0.0 to 1.0."""
        return min(1.0, (self.index - 1 + self.fraction) / max(self.total, 1))


#: Called with a :class:`Stage` each time the build moves along.
StageCallback = Callable[[Stage], None]


class Cancelled(Exception):
    """Raised when the caller asked for the build to stop."""


def check(cancel: Optional[threading.Event]) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("Build cancelled.")


class Reporter:
    """Tracks which stage we are on and forwards it to whoever is listening."""

    def __init__(self, on_stage: Optional[StageCallback] = None,
                 cancel: Optional[threading.Event] = None):
        self.on_stage = on_stage
        self.cancel = cancel
        self.index = 0
        self.label = ""

    def stage(self, label: str) -> None:
        check(self.cancel)
        self.index += 1
        self.label = label
        self._emit()

    def progress(self, fraction: float, detail: str = "") -> None:
        """Report movement inside the current stage."""
        check(self.cancel)
        self._emit(max(0.0, min(fraction, 1.0)), detail)

    def percent(self, percent: int) -> None:
        """Convenience for backends that count in whole percent."""
        self.progress(percent / 100.0, f" - {percent}%")

    def check(self) -> None:
        check(self.cancel)

    def _emit(self, fraction: float = 0.0, detail: str = "") -> None:
        if self.on_stage:
            self.on_stage(Stage(self.label, self.index, len(STAGES),
                                fraction, detail))
