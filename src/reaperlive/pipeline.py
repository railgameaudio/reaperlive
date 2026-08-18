"""End-to-end: a URL or a file in, a rehearsal-ready DAW project out."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from reaperlive.analysis.structure import detect_sections
from reaperlive.analysis.tempo import build_grid
from reaperlive.config import BeatGrid, ProjectOptions, Section, Stem
from reaperlive.ingest.fetch import acquire, probe_duration, slugify
from reaperlive.render import metronome
from reaperlive.separate import color_for, get_separator, sort_stems

log = logging.getLogger(__name__)

AUDIO_DIR = "audio"


@dataclass
class BuildResult:
    project_dir: Path
    title: str
    grid: BeatGrid
    stems: list[Stem]
    sections: list[Section]
    duration: float
    written: list[Path]

    @property
    def bpm(self) -> float:
        return self.grid.bpm


def build(options: ProjectOptions) -> BuildResult:
    if not options.source:
        raise ValueError("No source given.")

    outdir = Path(options.outdir).expanduser()
    workdir = outdir / "_work"
    source = acquire(options.source, workdir, options.sample_rate, options.name)

    slug = slugify(options.name or source.title)
    project_dir = outdir / slug
    audio_dir = project_dir / AUDIO_DIR
    audio_dir.mkdir(parents=True, exist_ok=True)

    mix = audio_dir / "mix.wav"
    shutil.move(str(source.path), mix)
    shutil.rmtree(workdir, ignore_errors=True)
    duration = probe_duration(mix)

    # ---- separation -------------------------------------------------------
    separator = get_separator(options.separation)
    raw_stems = separator.separate(mix, audio_dir) if separator.name != "none" else {}

    drums = raw_stems.get("drums")
    vocals = raw_stems.get("vocals")

    # ---- tempo ------------------------------------------------------------
    grid = build_grid(mix, options.tempo, percussive_ref=drums)
    project_end = duration + grid.shift

    # ---- structure --------------------------------------------------------
    sections = detect_sections(vocals, duration, grid, options.structure)

    # ---- click ------------------------------------------------------------
    click_rel: Optional[str] = None
    if options.click.audio:
        click_path = metronome.render_click_wav(
            audio_dir / "click.wav", grid, project_end, options.sample_rate)
        click_rel = f"{AUDIO_DIR}/{click_path.name}"
    midi_path = metronome.write_midi_file(
        project_dir / "metronome.mid", grid, project_end, options.click)

    # ---- stem list --------------------------------------------------------
    stems: list[Stem] = []
    for name, path in sort_stems(raw_stems):
        stems.append(Stem(name=name, path=path, relpath=f"{AUDIO_DIR}/{path.name}",
                          duration=duration, color=color_for(name)))
    if options.keep_mix or not stems:
        stems.append(Stem(name="mix", path=mix, relpath=f"{AUDIO_DIR}/mix.wav",
                          duration=duration, color=color_for("mix")))

    # ---- project files ----------------------------------------------------
    written: list[Path] = [midi_path]
    target = options.target.lower()
    if target in ("reaper", "both"):
        from reaperlive.render.reaper import write_project
        written.append(write_project(
            project_dir / f"{slug}.rpp", stems, grid, sections, project_end,
            options.click, options.structure.style, click_rel, options.sample_rate))
    if target in ("ableton", "both"):
        from reaperlive.render.ableton import write_project as write_als
        written.append(write_als(
            project_dir / f"{slug}.als", stems, grid, sections, project_end,
            click_rel, options.sample_rate))

    result = BuildResult(project_dir=project_dir, title=source.title, grid=grid,
                         stems=stems, sections=sections, duration=duration,
                         written=written)
    written.append(write_notes(result, source.origin, options))
    written.append(write_manifest(result, source.origin, options))

    # Neither DAW is here to open the result, so check it ourselves before
    # claiming success.
    from reaperlive.render.validate import validate_project
    for problem in validate_project(project_dir):
        log.warning("project check: %s", problem)
    return result


def write_manifest(result: BuildResult, origin: str, options: ProjectOptions) -> Path:
    """Machine-readable summary, so the analysis can be reused without a rerun."""
    data = {
        "title": result.title,
        "source": origin,
        "bpm": round(result.grid.bpm, 3),
        "time_signature": f"{result.grid.beats_per_bar}/{result.grid.beat_unit}",
        "duration_seconds": round(result.duration, 3),
        "count_in_seconds": round(result.grid.shift, 4),
        "tempo_markers": [
            {"time": round(p.time, 4), "bpm": round(p.bpm, 3)}
            for p in result.grid.tempo_points
        ],
        "sections": [
            {"label": s.label, "start": round(s.start, 3), "end": round(s.end, 3),
             "vocals": s.has_vocals}
            for s in result.sections
        ],
        "stems": [{"name": s.name, "file": s.relpath} for s in result.stems],
        "separator": options.separation.backend,
        "separator_model": options.separation.model,
    }
    path = result.project_dir / "reaperlive.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_notes(result: BuildResult, origin: str, options: ProjectOptions) -> Path:
    """A short human-readable brief to hand to the band along with the project."""
    grid = result.grid
    lines = [
        f"# {result.title}",
        "",
        f"- Tempo: **{grid.bpm:.2f} BPM**, {grid.beats_per_bar}/{grid.beat_unit}",
        f"- Length: {int(result.duration // 60)}:{result.duration % 60:04.1f}",
        f"- Count-in: {grid.shift:.2f}s before the song starts (bar 1 is the click alone)",
        f"- Tempo markers: {len(grid.tempo_points)}"
        + (" (steady tempo)" if len(grid.tempo_points) == 1 else " (tempo moves)"),
        f"- Source: {origin}",
        f"- Separated with: {options.separation.backend} / {options.separation.model}",
        "",
        "## Tracks",
        "",
    ]
    for stem in result.stems:
        lines.append(f"- **{stem.name}** - `{stem.relpath}`")
    if not result.sections:
        lines += ["", "No vocal sections were marked - either the track is "
                  "instrumental, or", "the singing runs from end to end.", ""]
    if result.sections:
        lines += ["", "## Sections", "",
                  "| Marker | Start | Length | Vocals |",
                  "| --- | --- | --- | --- |"]
        for section in result.sections:
            start = f"{int(section.start // 60)}:{section.start % 60:05.2f}"
            lines.append(
                f"| {section.label} | {start} | {section.duration:.1f}s | "
                f"{'yes' if section.has_vocals else 'no'} |")
    lines += [
        "",
        "## Notes",
        "",
        "- Section markers are a rough guide from vocal detection, not a chart.",
        "- The MIDI click follows the tempo map, so it stays locked if the song drifts.",
        "- `audio/click.wav` is the same click already rendered, if you would rather",
        "  not load an instrument.",
        "",
    ]
    path = result.project_dir / "SONG-NOTES.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
