# reaperlive

Point it at a YouTube link or an audio file, get back a DAW project your band
can open: **stems separated, tempo mapped, a metronome track, and markers for
where the singing is**.

It is a standalone tool, not a DAW plugin or script. You launch reaperlive, it
builds the project, and then it opens REAPER or Live for you.

```bash
reaperlive-gui                                            # the window
reaperlive "https://www.youtube.com/watch?v=..." -o ~/band # the command line
reaperlive ~/recordings/rehearsal.wav -t both
```

```
~/band/song-title/
├── song-title.rpp        REAPER project - tempo map, markers, one track per stem
├── song-title.als        Ableton Live set (with -t ableton or -t both)
├── metronome.mid         the click as a standalone MIDI file
├── reaperlive.json       the analysis, if you want to do your own thing with it
├── SONG-NOTES.md         a one-page brief to send round with the project
└── audio/
    ├── vocals.wav  drums.wav  bass.wav  other.wav
    ├── click.wav         the click, already rendered
    └── mix.wav           the original, muted, as a reference
```

## What you get

**Stems.** [Demucs](https://github.com/facebookresearch/demucs) (Hybrid
Transformer Demucs, from Meta AI) by default — the strongest open-source
separator that installs in one command. `htdemucs_6s` adds guitar and piano.
If you want the best vocal isolation available and don't mind a heavier
install, `--separator roformer` runs Mel-Band RoFormer / MDX-Net models via
[python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator).

**A tempo map, not just a BPM.** Beats are tracked on the isolated drum stem —
much steadier than tracking the full mix — then bar lines are found by scoring
which beat carries the kick. A steady song gets one tempo marker at its true
tempo; a song that drifts or pushes gets a marker per bar, so the grid follows
the performance instead of sliding out of it. Measurement jitter is filtered
out, so you get real tempo changes and not one marker per bar of noise.

**A count-in.** Everything is nudged so bar 1 is a clean, empty count-in bar and
the song starts exactly on bar 2. All stems move together, so nothing drifts
out of sync.

**A metronome.** A MIDI clip on the musical grid (so it tracks the tempo map
wherever it goes) plus the same click already rendered to audio, so the project
makes sense before anyone loads an instrument.

**Section markers.** The vocal stem is gated to find where someone is actually
singing, and the boundaries snap to bar lines:
`INTRO`, `VOCALS 1`, `BREAK`, `INSTRUMENTAL 1`, `VOCALS 2`, `OUTRO`. It's a
rough map for rehearsal, not a chord chart. Instrumental tracks are detected as
such and get no markers rather than a wrong one.

## Install

You need **Python 3.9+** and **ffmpeg**. Everything else comes from pip.

The repository is private, so plain HTTPS cloning returns "repository not
found" - that is GitHub declining to confirm a private repo exists, not a bad
URL. Clone as an account with access to the `railgameaudio` org:

```bash
gh repo clone railgameaudio/reaperlive          # GitHub CLI, after gh auth login
# or
git clone git@github.com:railgameaudio/reaperlive.git    # SSH key on the account
# or
git clone https://github.com/railgameaudio/reaperlive.git  # asks for a token, not your password

cd reaperlive

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[demucs]"         # with stem separation
```

ffmpeg, if you do not already have it:

| | |
| --- | --- |
| macOS | `brew install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |

That first install pulls in PyTorch, so it downloads a couple of GB and takes a
few minutes. If you only want the tempo mapping and want to skip separation
entirely, `pip install -e .` is much smaller — then run with `--separator none`.

Then check it works on something you already have:

```bash
reaperlive ~/Music/some-song.mp3 -o ~/band
```

Separation is much faster on a GPU. `--device` is autodetected, and can be
forced to `cpu`, `cuda` or `mps`.

### The window

```bash
reaperlive-gui          # or: python -m reaperlive.gui
```

Paste a link or pick a file, choose REAPER or Live, press **Build project**.
The log streams as it works, the progress bar tracks the separation, and
**Cancel** stops it part way. When it finishes, **Open in REAPER** /
**Open in Ableton** launches the DAW on the project it just built — which is
the point of the tool being standalone. Your settings are remembered between
runs.

On Linux the window needs Tk, which is a system package rather than a pip one:

```bash
sudo apt install python3-tk      # Fedora: sudo dnf install python3-tkinter
```

macOS and Windows builds from python.org already include it.

### Why not run it inside REAPER?

A ReaScript/ReaImGui version would have to run *inside* REAPER, so REAPER would
already be open before the tool started — the opposite of launching the DAW when
the project is ready. It would also do nothing for anyone working in Live. The
analysis needs PyTorch, which does not live comfortably inside a DAW script host
either. So the tool stands on its own and hands the finished project to
whichever DAW you use. If you would rather kick it off from inside REAPER, add
the `reaperlive` command as a custom action — it is an ordinary command line
program.

## Useful flags

| Flag | Does |
| --- | --- |
| `-t reaper\|ableton\|both` | which project to write |
| `--separator demucs\|roformer\|none` | separation backend |
| `--model htdemucs_ft` | slower, better; `htdemucs_6s` adds guitar + piano |
| `--two-stems vocals` | just vocals and backing, much faster |
| `--tempo-mode constant` | one tempo for the whole song |
| `--bpm 128` | skip detection, you already know the tempo |
| `--time-sig 3/4` | anything other than 4/4 |
| `--lead-in-bars 2` | longer count-in |
| `--marker-style regions` | coloured regions instead of ruler markers |
| `--shifts 5` | better separation, linearly slower |

`reaperlive --help` has the rest. Every option in the window maps onto one of
these flags — the window builds an argument list and hands it to the same
parser, so the two cannot drift apart.

## If the tempo comes out wrong

Beat tracking is the one step that can be confidently wrong, usually by an
octave — half or double the real tempo. Two fixes:

- `--bpm-range 80-160` folds the detection into a range you trust.
- `--bpm 128` skips detection entirely.

For a song with a rubato intro, `--tempo-mode constant` and trimming the intro
off the input file usually beats fighting the tracker.

## Ableton support

The REAPER writer is the one that's been verified against the format in detail.
The `.als` writer builds a Live 11/12 set — tracks, clips, tempo and locators —
but **it has not been opened in a real copy of Live**, so treat it as
experimental. It's checked on write for structure, valid gzip/XML and unique
element ids, which catches the ways a generated set usually fails.

If your Live won't open it, nothing is lost — the manual route takes a minute:

1. Set the project tempo to the BPM in `SONG-NOTES.md`.
2. Drag everything in `audio/` into the arrangement at bar 1. The stems all
   share a timeline, so they line up with each other automatically.
3. Drag `metronome.mid` onto a MIDI track for the click.

Note that this route gives you one fixed tempo. If the song's tempo actually
moves, `reaperlive.json` has every tempo marker with its time in seconds.

## How the analysis works

| Step | Approach |
| --- | --- |
| Download | `yt-dlp`, best available audio stream, decoded to 44.1 kHz PCM |
| Separation | Demucs v4 (htdemucs), or RoFormer/MDX via audio-separator |
| Beats | librosa onset strength + beat tracking, run on the drum stem |
| Latency | grid shifted by the median offset to back-tracked onsets |
| Bar lines | beat phase scored by onset strength plus sub-160 Hz energy |
| Tempo map | per-bar spans, median filtered, with a change threshold |
| Sections | RMS gate on the vocal stem, hysteresis, snapped to bars |

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests generate their own audio, so no fixture files are needed and nothing
touches the network. `reaperlive.render.validate` structurally checks written
projects and runs automatically after every build.

The window tests drive a real Tk window and skip themselves where there is no
display. On a headless box:

```bash
xvfb-run -a pytest
```

Layout is roughly:

| Module | Does |
| --- | --- |
| `ingest/fetch.py` | download and decode |
| `separate/` | pluggable separation backends |
| `analysis/tempo.py` | beats, bar lines, tempo map |
| `analysis/structure.py` | vocal sections |
| `render/reaper.py`, `render/ableton.py` | project writers |
| `render/metronome.py` | MIDI and audio click |
| `render/validate.py` | structural checks on what was written |
| `gui/` | the standalone window |
