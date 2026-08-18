"""Demucs (Meta AI) - Hybrid Transformer Demucs, the default separator."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

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
        try:
            import demucs  # noqa: F401
        except ImportError:
            return False
        return True

    def separate(self, mix: Path, outdir: Path) -> dict[str, Path]:
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
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
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
