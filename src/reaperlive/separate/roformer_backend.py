"""Mel-Band RoFormer / MDX-Net models via python-audio-separator.

Usually beats Demucs on vocal isolation, at the cost of a heavier install and
one model download per stem pass.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from reaperlive.config import SeparationOptions

log = logging.getLogger(__name__)

DEFAULT_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

#: audio-separator names outputs by role; map them onto our stem vocabulary.
_ALIASES = {
    "vocals": "vocals",
    "instrumental": "instrumental",
    "no_vocals": "instrumental",
    "drums": "drums",
    "bass": "bass",
    "other": "other",
}


class RoformerSeparator:
    name = "roformer"

    def __init__(self, options: SeparationOptions):
        self.options = options
        self.model = (options.model if options.model not in ("htdemucs", "")
                      else DEFAULT_MODEL)

    def available(self) -> bool:
        try:
            import audio_separator  # noqa: F401
        except ImportError:
            return False
        return True

    def separate(self, mix: Path, outdir: Path) -> dict[str, Path]:
        if not self.available():
            raise RuntimeError(
                "audio-separator is not installed. Run:\n"
                "  pip install 'reaperlive[roformer]'\n"
                "or use --separator demucs."
            )
        from audio_separator.separator import Separator as AudioSeparator

        outdir.mkdir(parents=True, exist_ok=True)
        raw = outdir / "_roformer"
        raw.mkdir(parents=True, exist_ok=True)

        log.info("Separating with %s", self.model)
        sep = AudioSeparator(output_dir=str(raw), output_format="wav")
        sep.load_model(model_filename=self.model)
        produced = sep.separate(str(mix))

        stems: dict[str, Path] = {}
        for item in produced:
            src = Path(item)
            if not src.is_absolute():
                src = raw / src
            role = _classify(src.stem)
            dest = outdir / f"{role}.wav"
            shutil.move(str(src), dest)
            stems[role] = dest
        shutil.rmtree(raw, ignore_errors=True)
        log.info("Stems: %s", ", ".join(sorted(stems)))
        return stems


def _classify(filename: str) -> str:
    lowered = filename.lower()
    for token, role in _ALIASES.items():
        if token in lowered:
            return role
    return "other"
