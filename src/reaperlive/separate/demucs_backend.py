"""Demucs (Meta AI) - Hybrid Transformer Demucs, the default separator."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from reaperlive.config import SeparationOptions

log = logging.getLogger(__name__)

#: Models worth exposing. htdemucs_ft is the fine-tuned variant (4x slower).
KNOWN_MODELS = ("htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra", "mdx_extra_q")


class DemucsSeparator:
    name = "demucs"

    def __init__(self, options: SeparationOptions):
        self.options = options

    def _device(self) -> str:
        if self.options.device != "auto":
            return self.options.device
        try:
            import torch
        except ImportError:
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def available(self) -> bool:
        import importlib.util

        return importlib.util.find_spec("demucs") is not None

    def separate(self, mix: Path, outdir: Path,
                 cancel: Optional[threading.Event] = None,
                 on_percent: Optional[Callable[[int], None]] = None
                 ) -> dict[str, Path]:
        from reaperlive.progress import Cancelled, check

        check(cancel)
        if not self.available():
            raise RuntimeError(
                "Demucs is not installed. Run:  pip install 'reaperlive[demucs]'\n"
                "(or pass --separator none to build the project from the mix alone)."
            )
        outdir.mkdir(parents=True, exist_ok=True)
        raw = outdir / "_demucs"
        device = self._device()
        cmd = [
            sys.executable, "-m", "demucs.separate",
            "-n", self.options.model,
            "-o", str(raw),
            "--filename", "{stem}.{ext}",
            "-d", device,
            "--shifts", str(max(self.options.shifts, 0)),
            "-j", str(max(self.options.jobs, 1)),
        ]
        if self.options.two_stems:
            cmd += ["--two-stems", self.options.two_stems]
        cmd.append(str(mix))

        log.info("Separating with Demucs %s on %s (this is the slow part)",
                 self.options.model, device)
        code, tail = _run_streaming(cmd, cancel, on_percent)
        if code != 0:
            if cancel is not None and cancel.is_set():
                raise Cancelled("Separation cancelled.")
            raise RuntimeError("Demucs failed:\n" + "\n".join(tail))

        produced = sorted(raw.rglob("*.wav"))
        if not produced:
            raise RuntimeError(f"Demucs produced no stems under {raw}")

        stems: dict[str, Path] = {}
        for src in produced:
            dest = outdir / f"{src.stem}.wav"
            shutil.move(str(src), dest)
            stems[src.stem] = dest
        shutil.rmtree(raw, ignore_errors=True)
        log.info("Stems: %s", ", ".join(sorted(stems)))
        return stems


_PERCENT = re.compile(r"(\d{1,3})%")


def _run_streaming(cmd: list[str], cancel: Optional[threading.Event],
                   on_percent: Optional[Callable[[int], None]] = None
                   ) -> tuple[int, list[str]]:
    """Run Demucs, relaying its progress as it goes.

    Separation takes minutes, so the output is streamed rather than collected
    at the end - otherwise the caller has nothing to show for the wait. Demucs
    redraws its progress bar with carriage returns, so we split on those and
    only report when the percentage actually moves.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    tail: list[str] = []
    last_percent = -1
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover - rare
                    proc.kill()
                return proc.returncode or 1, tail
            for piece in raw.replace("\r", "\n").splitlines():
                line = piece.strip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-40]
                match = _PERCENT.search(line)
                if match:
                    percent = int(match.group(1))
                    if percent >= last_percent + 5 or percent == 100:
                        last_percent = percent
                        log.info("  separating... %d%%", percent)
                        if on_percent:
                            on_percent(percent)
                else:
                    log.debug("  %s", line)
    finally:
        proc.wait()
    return proc.returncode, tail
