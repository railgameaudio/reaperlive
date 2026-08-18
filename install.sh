#!/usr/bin/env bash
#
# One-shot setup for reaperlive. Safe to re-run.
#
#   ./install.sh                 full install, with stem separation (Demucs)
#   ./install.sh --lite          analysis only, skips PyTorch (~2 GB smaller)
#   ./install.sh --dev           also install pytest and run the test suite
#   ./install.sh --with-roformer add the RoFormer / MDX separation backend
#   ./install.sh --yes           never prompt (installs ffmpeg if it is missing)
#   ./install.sh --python /path/to/python3.12
#
set -euo pipefail

MIN_MAJOR=3
MIN_MINOR=10
VENV_DIR=".venv"
EXTRAS="demucs"
DEV=0
ROFORMER=0
ASSUME_YES=0
VERIFY=1
PYTHON_OVERRIDE="${PYTHON:-}"

cd "$(dirname "$0")"

# ------------------------------------------------------------------ output
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    BOLD=$(tput bold); DIM=$(tput dim); RED=$(tput setaf 1)
    GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RESET=$(tput sgr0)
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

step()  { printf "\n%s==>%s %s%s\n" "$BOLD" "$RESET" "$1" "$RESET"; }
info()  { printf "    %s\n" "$1"; }
ok()    { printf "    %s%s%s\n" "$GREEN" "$1" "$RESET"; }
warn()  { printf "    %s%s%s\n" "$YELLOW" "$1" "$RESET"; }
die()   { printf "\n%serror:%s %s\n\n" "$RED" "$RESET" "$1" >&2; exit 1; }

ask() {
    # ask "question" -> 0 for yes
    [ "$ASSUME_YES" = "1" ] && return 0
    [ -t 0 ] || return 1
    printf "    %s [Y/n] " "$1"
    read -r reply
    case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ------------------------------------------------------------------- flags
while [ $# -gt 0 ]; do
    case "$1" in
        --lite)          EXTRAS="" ;;
        --dev)           DEV=1 ;;
        --with-roformer) ROFORMER=1 ;;
        --yes|-y)        ASSUME_YES=1 ;;
        --no-verify)     VERIFY=0 ;;
        --python)        shift; PYTHON_OVERRIDE="${1:-}" ;;
        --help|-h)       awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"; exit 0 ;;
        *)               die "Unknown option: $1  (try --help)" ;;
    esac
    shift
done

printf "%sreaperlive setup%s\n" "$BOLD" "$RESET"

# -------------------------------------------------------------- interpreter
step "Looking for a suitable Python"

python_ok() {
    [ -x "$(command -v "$1" 2>/dev/null || echo /nonexistent)" ] || return 1
    "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

PY=""
if [ -n "$PYTHON_OVERRIDE" ]; then
    python_ok "$PYTHON_OVERRIDE" \
        || die "$PYTHON_OVERRIDE is not Python $MIN_MAJOR.$MIN_MINOR or newer."
    PY="$PYTHON_OVERRIDE"
else
    for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if python_ok "$candidate"; then PY="$candidate"; break; fi
    done
fi

if [ -z "$PY" ]; then
    found=$(python3 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>/dev/null || echo "none")
    printf "\n%serror:%s reaperlive needs Python %s.%s or newer (found: %s).\n\n" \
        "$RED" "$RESET" "$MIN_MAJOR" "$MIN_MINOR" "$found" >&2
    case "$(uname -s)" in
        Darwin) printf "    brew install python@3.12\n    ./install.sh --python \$(brew --prefix)/bin/python3.12\n\n" >&2 ;;
        *)      printf "    sudo apt install python3.12 python3.12-venv\n    ./install.sh --python python3.12\n\n" >&2 ;;
    esac
    printf "    Or download it from https://www.python.org/downloads/\n\n" >&2
    exit 1
fi
ok "$($PY -c 'import sys; print("Python " + sys.version.split()[0])')  ($(command -v "$PY"))"

# ------------------------------------------------------------------ ffmpeg
step "Checking ffmpeg"
if command -v ffmpeg >/dev/null 2>&1; then
    ok "$(ffmpeg -version 2>/dev/null | head -1 | cut -c1-60)"
else
    warn "ffmpeg is not installed. reaperlive needs it to decode audio."
    installed=0
    if command -v brew >/dev/null 2>&1; then
        if ask "Install it now with Homebrew?"; then
            brew install ffmpeg && installed=1
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        if ask "Install it now with apt?"; then
            sudo apt-get update && sudo apt-get install -y ffmpeg && installed=1
        fi
    fi
    if [ "$installed" = "0" ]; then
        warn "Carrying on without it - install ffmpeg before your first run:"
        case "$(uname -s)" in
            Darwin) info "brew install ffmpeg" ;;
            *)      info "sudo apt install ffmpeg" ;;
        esac
    fi
fi

# ---------------------------------------------------------------- the venv
step "Setting up $VENV_DIR"
if [ -d "$VENV_DIR" ]; then
    existing=$("$VENV_DIR/bin/python" -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>/dev/null || echo "broken")
    wanted=$("$PY" -c "import sys; print('%d.%d' % sys.version_info[:2])")
    if [ "$existing" != "$wanted" ]; then
        warn "Existing venv is Python $existing, but we are using $wanted."
        if ask "Replace it?"; then
            rm -rf "$VENV_DIR"
            "$PY" -m venv "$VENV_DIR"
            ok "Rebuilt on Python $wanted"
        else
            die "Remove $VENV_DIR by hand, or re-run with --python matching it."
        fi
    else
        ok "Reusing the existing environment"
    fi
else
    "$PY" -m venv "$VENV_DIR" || die "Could not create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv"
    ok "Created"
fi

VPY="$VENV_DIR/bin/python"
[ -x "$VPY" ] || VPY="$VENV_DIR/Scripts/python.exe"   # git-bash on Windows
[ -x "$VPY" ] || die "The virtualenv looks broken - delete $VENV_DIR and re-run."

# ------------------------------------------------------ build tools first
# An editable install from pyproject.toml needs pip 21.3+. Older pip fails with
# "File setup.py or setup.cfg not found", which is what sends people here.
step "Updating pip"
before=$("$VPY" -m pip --version | awk '{print $2}')
"$VPY" -m pip install --quiet --upgrade pip setuptools wheel \
    || die "Could not upgrade pip inside the virtualenv."
ok "pip $before -> $("$VPY" -m pip --version | awk '{print $2}')"

# --------------------------------------------------------------- packages
if [ -n "$EXTRAS" ] && [ "$(uname -s)" = "Linux" ] && ! command -v nvidia-smi >/dev/null 2>&1; then
    # pip defaults to the CUDA build on Linux, which is gigabytes of driver
    # libraries this machine cannot use. Ask for the CPU wheel instead.
    step "Installing PyTorch (CPU build - no NVIDIA GPU found)"
    "$VPY" -m pip install torch --index-url https://download.pytorch.org/whl/cpu \
        || warn "CPU wheel unavailable; falling back to the default build."
fi

step "Installing reaperlive"
spec="."
[ -n "$EXTRAS" ] && spec=".[$EXTRAS]"
if [ -n "$EXTRAS" ]; then
    info "This pulls in PyTorch - a couple of GB, and a few minutes."
else
    info "Lite install: no separation backend (run with --separator none)."
fi
"$VPY" -m pip install -e "$spec" || die "Install failed. The output above says why."
ok "Installed"

if [ "$ROFORMER" = "1" ]; then
    step "Installing the RoFormer / MDX backend"
    "$VPY" -m pip install -e ".[roformer]" || die "Could not install the roformer extra."
    ok "Installed"
fi

if [ "$DEV" = "1" ]; then
    step "Installing test tools"
    "$VPY" -m pip install --quiet -e ".[dev]"
    ok "Installed"
fi

# ------------------------------------------------------------------- tkinter
step "Checking the desktop window"
if "$VPY" -c "import tkinter" >/dev/null 2>&1; then
    ok "Tkinter is available - reaperlive-gui will run"
else
    warn "Tkinter is missing, so only the command line will work."
    case "$(uname -s)" in
        Darwin) info "brew install python-tk@$("$VPY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" ;;
        Linux)  info "sudo apt install python3-tk      # Fedora: sudo dnf install python3-tkinter" ;;
        *)      info "Reinstall Python from python.org, which bundles Tk." ;;
    esac
    info "Then re-run ./install.sh"
fi

# -------------------------------------------------------------------- check
if [ "$VERIFY" = "1" ]; then
    step "Checking it actually works"
    "$VPY" -m reaperlive --version >/dev/null || die "reaperlive will not start."
    if command -v ffmpeg >/dev/null 2>&1; then
        tmp=$(mktemp -d)
        trap 'rm -rf "$tmp"' EXIT
        "$VPY" - "$tmp" <<'PYCHECK' || die "The smoke test failed - see the output above."
import sys, numpy as np, soundfile as sf
from pathlib import Path

tmp = Path(sys.argv[1])
sr, bpm, bars = 44100, 120.0, 8
beat = 60.0 / bpm
audio = np.zeros(int((0.5 + bars * 4 * beat + 0.5) * sr))
for i in range(bars * 4):
    start = int((0.5 + i * beat) * sr)
    t = np.linspace(0, 0.2, int(0.2 * sr), endpoint=False)
    hit = np.sin(2 * np.pi * (55 if i % 4 == 0 else 260) * t) * np.exp(-25 * t)
    audio[start:start + len(hit)] += hit
sf.write(str(tmp / "check.wav"), audio / np.abs(audio).max() / 1.05, sr)

import contextlib, io

from reaperlive.cli import main
sink = io.StringIO()
with contextlib.redirect_stdout(sink):
    code = main([str(tmp / "check.wav"), "-o", str(tmp / "out"),
                 "-n", "Install Check", "--separator", "none", "-q"])
if code != 0:
    print(sink.getvalue())
    raise SystemExit("build failed")

project = tmp / "out" / "install-check" / "install-check.rpp"
if not project.exists():
    raise SystemExit("no project file was written")

from reaperlive.render.validate import validate_project
problems = validate_project(project.parent)
if problems:
    raise SystemExit("project did not validate: " + "; ".join(problems))

import json
bpm_found = json.loads((project.parent / "reaperlive.json").read_text())["bpm"]
if not 118 <= bpm_found <= 122:
    raise SystemExit(f"tempo detection is off: {bpm_found} BPM, expected ~120")
print(f"    built and validated a project at {bpm_found:.1f} BPM")
PYCHECK
        ok "End to end check passed"
    else
        warn "Skipped the audio check because ffmpeg is missing."
    fi
fi

# --------------------------------------------------------------------- done
if [ -f "$VENV_DIR/bin/activate" ]; then
    activate="source $VENV_DIR/bin/activate"
else
    activate="$VENV_DIR\\Scripts\\activate"
fi

printf "\n%sReady.%s\n\n" "$GREEN$BOLD" "$RESET"
printf "  %s\n" "$activate"
printf "  reaperlive-gui                                  %sthe window%s\n" "$DIM" "$RESET"
printf "  reaperlive ~/Music/song.mp3 -o ~/band            %sone song%s\n" "$DIM" "$RESET"
printf "  reaperlive \"https://youtu.be/...\" -t both        %sREAPER + Live%s\n" "$DIM" "$RESET"
printf "\n%sWithout activating, use %s/bin/reaperlive directly.%s\n\n" "$DIM" "$VENV_DIR" "$RESET"

if [ "$DEV" = "1" ]; then
    step "Running the test suite"
    "$VPY" -m pytest -q || die "Tests failed."
fi
