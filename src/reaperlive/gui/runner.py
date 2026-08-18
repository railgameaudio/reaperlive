"""Runs a build on a worker thread and reports back to the UI thread.

Tk is not thread safe, so nothing here touches a widget. The worker pushes
messages onto a queue and the UI drains it from its own event loop.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Optional

from reaperlive.config import ProjectOptions
from reaperlive.pipeline import BuildResult
from reaperlive.progress import Cancelled, Stage


@dataclass
class LogMessage:
    text: str
    level: int = logging.INFO


@dataclass
class StageMessage:
    stage: Stage


@dataclass
class DoneMessage:
    result: Optional[BuildResult] = None
    error: Optional[str] = None
    cancelled: bool = False


class QueueLogHandler(logging.Handler):
    """Feeds log records to the UI instead of the console."""

    def __init__(self, sink: "queue.Queue"):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put(LogMessage(self.format(record), record.levelno))
        except Exception:  # pragma: no cover - a logging handler must not raise
            pass


class BuildJob:
    """One build, running in the background."""

    def __init__(self, options: ProjectOptions, level: int = logging.INFO):
        self.options = options
        self.level = level
        self.messages: "queue.Queue" = queue.Queue()
        self.cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("This job is already running.")
        self.cancel_event.clear()
        self._thread = threading.Thread(target=self._run, name="reaperlive-build",
                                        daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        from reaperlive.pipeline import build

        logger = logging.getLogger("reaperlive")
        handler = QueueLogHandler(self.messages)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.setLevel(self.level)
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(min(previous or self.level, self.level))
        try:
            result = build(self.options, cancel=self.cancel_event,
                           on_stage=self._stage)
            self.messages.put(DoneMessage(result=result))
        except Cancelled:
            self.messages.put(DoneMessage(cancelled=True))
        except Exception as exc:  # noqa: BLE001 - the worker is the boundary
            logger.debug("build failed", exc_info=True)
            self.messages.put(DoneMessage(error=str(exc)))
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)

    def _stage(self, stage: Stage) -> None:
        self.messages.put(StageMessage(stage))

    def drain(self, limit: int = 200) -> list:
        """Everything queued since the last call, for the UI to render."""
        out = []
        for _ in range(limit):
            try:
                out.append(self.messages.get_nowait())
            except queue.Empty:
                break
        return out
