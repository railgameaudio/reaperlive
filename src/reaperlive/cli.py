"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from reaperlive import __version__
from reaperlive.config import (
    ClickOptions, ProjectOptions, SeparationOptions, SEPARATORS, StructureOptions,
    TARGETS, TEMPO_MODES, TempoOptions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reaperlive",
        description="Turn a YouTube link or an audio file into a tempo-mapped "
                    "REAPER or Ableton project with separated stems, a metronome "
                    "and section markers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("source", help="YouTube (or other yt-dlp) URL, or a local audio file")
    parser.add_argument("-o", "--outdir", type=Path, default=Path("projects"),
                        help="Where the project folder is created")
    parser.add_argument("-n", "--name", help="Project name (default: the track title)")
    parser.add_argument("-t", "--target", choices=TARGETS, default="reaper",
                        help="Which DAW project to write")
    parser.add_argument("--version", action="version", version=f"reaperlive {__version__}")

    sep = parser.add_argument_group("separation")
    sep.add_argument("--separator", choices=SEPARATORS, default="demucs",
                     help="Stem separation backend")
    sep.add_argument("--model", default="htdemucs",
                     help="Model name (htdemucs, htdemucs_ft, htdemucs_6s, ...)")
    sep.add_argument("--two-stems", metavar="STEM",
                     help="Only split this stem out, e.g. --two-stems vocals")
    sep.add_argument("--shifts", type=int, default=1,
                     help="Demucs shift trick passes: higher is better and slower")
    sep.add_argument("--jobs", type=int, default=1, help="Parallel separation jobs")
    sep.add_argument("--device", default="auto", help="auto, cpu, cuda or mps")
    sep.add_argument("--no-mix", action="store_true",
                     help="Leave the original mix out of the project")

    tempo = parser.add_argument_group("tempo")
    tempo.add_argument("--tempo-mode", choices=TEMPO_MODES, default="measure",
                       help="constant: one marker; measure: one per bar; beat: one per beat")
    tempo.add_argument("--bpm", type=float, help="Skip detection and use this tempo")
    tempo.add_argument("--time-sig", default="4/4", help="Time signature, e.g. 3/4 or 6/8")
    tempo.add_argument("--lead-in-bars", type=int, default=1,
                       help="Bars of count-in before the song starts")
    tempo.add_argument("--round-bpm", action="store_true",
                       help="Snap the detected tempo to a whole number")
    tempo.add_argument("--bpm-range", default="60-200",
                       help="Fold octave-confused tempi into this range")
    tempo.add_argument("--tempo-tolerance", type=float, default=1.5,
                       help="Percent change needed before a new tempo marker is written")

    struct = parser.add_argument_group("structure")
    struct.add_argument("--no-markers", action="store_true",
                        help="Skip vocal/instrumental section markers")
    struct.add_argument("--marker-style", choices=("markers", "regions", "both"),
                        default="markers", help="Ruler markers, regions, or both")
    struct.add_argument("--vocal-threshold", type=float, default=32.0,
                        help="dB below the loudest vocal that still counts as singing")
    struct.add_argument("--min-section", type=float, default=6.0,
                        help="Shortest section to mark, in seconds")
    struct.add_argument("--no-snap", action="store_true",
                        help="Do not snap section markers to bar lines")

    click = parser.add_argument_group("metronome")
    click.add_argument("--no-click-midi", action="store_true",
                       help="Skip the MIDI metronome clip")
    click.add_argument("--no-click-audio", action="store_true",
                       help="Skip the rendered click track")
    click.add_argument("--click-channel", type=int, default=1,
                       help="MIDI channel for the click (10 = GM drums)")

    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Errors only")
    return parser


def _parse_pair(text: str, sep: str, what: str) -> tuple[float, float]:
    try:
        a, b = text.split(sep)
        return float(a), float(b)
    except ValueError:
        raise SystemExit(f"Could not read {what} from {text!r}")


def options_from_args(args: argparse.Namespace) -> ProjectOptions:
    num, den = _parse_pair(args.time_sig, "/", "time signature")
    lo, hi = _parse_pair(args.bpm_range, "-", "BPM range")
    return ProjectOptions(
        source=args.source,
        outdir=args.outdir,
        name=args.name,
        target=args.target,
        keep_mix=not args.no_mix,
        separation=SeparationOptions(
            backend=args.separator, model=args.model, two_stems=args.two_stems,
            shifts=args.shifts, jobs=args.jobs, device=args.device,
        ),
        tempo=TempoOptions(
            mode=args.tempo_mode, fixed_bpm=args.bpm, beats_per_bar=int(num),
            beat_unit=int(den), lead_in_bars=args.lead_in_bars,
            round_bpm=args.round_bpm, bpm_min=lo, bpm_max=hi,
            tolerance_pct=args.tempo_tolerance,
        ),
        structure=StructureOptions(
            enabled=not args.no_markers, threshold_db=args.vocal_threshold,
            min_segment_sec=args.min_section, snap_to_bars=not args.no_snap,
            style=args.marker_style,
        ),
        click=ClickOptions(
            midi=not args.no_click_midi, audio=not args.no_click_audio,
            channel=args.click_channel,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.DEBUG if args.verbose else logging.ERROR if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)

    from reaperlive.pipeline import build

    try:
        result = build(options_from_args(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - the CLI is the boundary
        logging.getLogger(__name__).debug("build failed", exc_info=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"  {result.title}")
    print(f"  {result.grid.bpm:.2f} BPM  {result.grid.beats_per_bar}/"
          f"{result.grid.beat_unit}  |  {len(result.stems)} stems  |  "
          f"{len(result.sections)} sections")
    print(f"  {result.project_dir}")
    for written in result.written:
        print(f"    {written.relative_to(result.project_dir)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
