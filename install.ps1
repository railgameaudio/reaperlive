<#
.SYNOPSIS
    One-shot setup for reaperlive. Safe to re-run.

.DESCRIPTION
    Finds a suitable Python, checks for ffmpeg, builds a virtualenv, brings pip
    up to date, installs the package, and then builds a throwaway project end to
    end to prove the whole chain works.

.EXAMPLE
    .\install.ps1
    Full install, with stem separation.

.EXAMPLE
    .\install.ps1 -Lite
    Analysis only. Skips PyTorch, about 2 GB smaller.

.EXAMPLE
    .\install.ps1 -Dev
    Also installs pytest and runs the test suite.

.NOTES
    If Windows refuses to run this, it is the execution policy, not the script:
        powershell -ExecutionPolicy Bypass -File .\install.ps1
    or just double-click install.bat, which does that for you.
#>
[CmdletBinding()]
param(
    [switch]$Lite,
    [switch]$Dev,
    [switch]$WithRoformer,
    [switch]$Yes,
    [switch]$NoVerify,
    [string]$Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# $IsWindows is automatic in PowerShell 6+, but does not exist in Windows
# PowerShell 5.1 - and under StrictMode, reading an undefined variable throws.
# 5.1 only ever runs on Windows, so the fallback is safe.
if (-not (Test-Path variable:IsWindows)) { $IsWindows = $true }

$MinVersion = [version]'3.10'
$VenvDir = '.venv'

Set-Location -LiteralPath $PSScriptRoot

# ----------------------------------------------------------------- output
function Write-Step($text) { Write-Host ''; Write-Host '==> ' -NoNewline -ForegroundColor Cyan; Write-Host $text }
function Write-Info($text) { Write-Host "    $text" }
function Write-Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Stop-WithError($text) {
    Write-Host ''
    Write-Host 'error: ' -NoNewline -ForegroundColor Red
    Write-Host $text
    Write-Host ''
    exit 1
}

function Confirm-Action($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) { return $false }
    $reply = Read-Host "    $question [Y/n]"
    return ($reply -notmatch '^[nN]')
}

# Native commands do not throw on failure, so every call is checked by hand.
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$FailureMessage)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Stop-WithError $FailureMessage }
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Host 'reaperlive setup' -ForegroundColor White

# ------------------------------------------------------------ interpreter
Write-Step 'Looking for a suitable Python'

function Get-PythonVersion {
    param([string]$Exe, [string[]]$Prefix = @())
    try {
        $output = & $Exe @Prefix -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $output) { return $null }
        return [version]($output | Select-Object -First 1)
    } catch {
        return $null
    }
}

# Each candidate is an argument list: the Windows launcher needs "py -3.12".
$candidates = New-Object System.Collections.ArrayList
if ($Python) {
    [void]$candidates.Add(@($Python))
} else {
    if ($IsWindows -and (Test-Command 'py')) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') { [void]$candidates.Add(@('py', "-$v")) }
    }
    foreach ($n in 'python3.13', 'python3.12', 'python3.11', 'python3.10', 'python3', 'python') {
        [void]$candidates.Add(@($n))
    }
}

$pyExe = $null; $pyPrefix = @(); $pyVersion = $null
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    # Guard the empty-slice case: $a[1..0] on a one-element array reverses it.
    $prefix = if ($candidate.Count -gt 1) { $candidate[1..($candidate.Count - 1)] } else { @() }
    if (-not (Test-Command $exe)) { continue }
    $version = Get-PythonVersion -Exe $exe -Prefix $prefix
    if ($version -and $version -ge $MinVersion) {
        $pyExe = $exe; $pyPrefix = $prefix; $pyVersion = $version
        break
    }
}

if (-not $pyExe) {
    if ($Python) {
        Stop-WithError "$Python is not Python $MinVersion or newer."
    }
    Write-Host ''
    Write-Host 'error: ' -NoNewline -ForegroundColor Red
    Write-Host "reaperlive needs Python $MinVersion or newer, and none was found."
    Write-Host ''
    if ($IsWindows) {
        Write-Host '    winget install Python.Python.3.12'
        Write-Host '    Then open a new terminal and run .\install.ps1 again.'
    } else {
        Write-Host '    brew install python@3.12    # or your package manager'
    }
    Write-Host '    Or download it from https://www.python.org/downloads/'
    Write-Host ''
    exit 1
}
Write-Ok "Python $pyVersion  ($((Get-Command $pyExe).Source))"

# ---------------------------------------------------------------- ffmpeg
Write-Step 'Checking ffmpeg'
if (Test-Command 'ffmpeg') {
    Write-Ok (((& ffmpeg -version 2>$null) | Select-Object -First 1) -replace '(.{1,60}).*', '$1')
} else {
    Write-Warn 'ffmpeg is not installed. reaperlive needs it to decode audio.'
    $installed = $false
    if ($IsWindows -and (Test-Command 'winget')) {
        if (Confirm-Action 'Install it now with winget?') {
            & winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
            $installed = ($LASTEXITCODE -eq 0)
            if ($installed) {
                # winget updates the machine PATH, not this process's copy.
                $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                            [Environment]::GetEnvironmentVariable('Path', 'User')
                if (-not (Test-Command 'ffmpeg')) {
                    Write-Warn 'Installed, but not on PATH yet - open a new terminal before your first run.'
                }
            }
        }
    } elseif (Test-Command 'brew') {
        if (Confirm-Action 'Install it now with Homebrew?') {
            & brew install ffmpeg; $installed = ($LASTEXITCODE -eq 0)
        }
    }
    if (-not $installed) {
        Write-Warn 'Carrying on without it - install ffmpeg before your first run:'
        if ($IsWindows) { Write-Info 'winget install Gyan.FFmpeg' } else { Write-Info 'brew install ffmpeg' }
    }
}

# ------------------------------------------------------------------ venv
Write-Step "Setting up $VenvDir"

function Get-VenvPython($dir) {
    foreach ($relative in 'Scripts\python.exe', 'bin/python') {
        $candidate = Join-Path $dir $relative
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

$wanted = "$($pyVersion.Major).$($pyVersion.Minor)"
if (Test-Path -LiteralPath $VenvDir) {
    $existingPython = Get-VenvPython $VenvDir
    $existing = if ($existingPython) {
        (& $existingPython -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null)
    } else { $null }
    if (-not $existing) { $existing = 'broken' }

    if ($existing -ne $wanted) {
        Write-Warn "Existing venv is Python $existing, but we are using $wanted."
        if (Confirm-Action 'Replace it?') {
            Remove-Item -LiteralPath $VenvDir -Recurse -Force
            Invoke-Checked $pyExe ($pyPrefix + @('-m', 'venv', $VenvDir)) 'Could not create a virtualenv.'
            Write-Ok "Rebuilt on Python $wanted"
        } else {
            Stop-WithError "Remove $VenvDir by hand, or re-run with -Python matching it."
        }
    } else {
        Write-Ok 'Reusing the existing environment'
    }
} else {
    Invoke-Checked $pyExe ($pyPrefix + @('-m', 'venv', $VenvDir)) 'Could not create a virtualenv.'
    Write-Ok 'Created'
}

$VenvPython = Get-VenvPython $VenvDir
if (-not $VenvPython) { Stop-WithError "The virtualenv looks broken - delete $VenvDir and re-run." }

# ------------------------------------------------------------ build tools
# An editable install from pyproject.toml needs pip 21.3+. Older pip fails with
# "File setup.py or setup.cfg not found", which is what sends people here.
Write-Step 'Updating pip'
$before = ((& $VenvPython -m pip --version) -split ' ')[1]
Invoke-Checked $VenvPython @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip', 'setuptools', 'wheel') `
    'Could not upgrade pip inside the virtualenv.'
$after = ((& $VenvPython -m pip --version) -split ' ')[1]
Write-Ok "pip $before -> $after"

# --------------------------------------------------------------- packages
Write-Step 'Installing reaperlive'
$spec = if ($Lite) { '.' } else { '.[demucs]' }
if ($Lite) {
    Write-Info 'Lite install: no separation backend (run with --separator none).'
} else {
    Write-Info 'This pulls in PyTorch - a couple of GB, and a few minutes.'
}
Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-e', $spec) 'Install failed. The output above says why.'
Write-Ok 'Installed'

if ($WithRoformer) {
    Write-Step 'Installing the RoFormer / MDX backend'
    Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-e', '.[roformer]') 'Could not install the roformer extra.'
    Write-Ok 'Installed'
}

if ($Dev) {
    Write-Step 'Installing test tools'
    Invoke-Checked $VenvPython @('-m', 'pip', 'install', '--quiet', '-e', '.[dev]') 'Could not install the dev extra.'
    Write-Ok 'Installed'
}

# ---------------------------------------------------------------- tkinter
Write-Step 'Checking the desktop window'
& $VenvPython -c 'import tkinter' 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok 'Tkinter is available - reaperlive-gui will run'
} else {
    Write-Warn 'Tkinter is missing, so only the command line will work.'
    if ($IsWindows) {
        Write-Info 'Re-run the Python installer and tick "tcl/tk and IDLE", then re-run this script.'
    } else {
        Write-Info 'macOS: brew install python-tk    Linux: sudo apt install python3-tk'
    }
}

# ------------------------------------------------------------------ check
if (-not $NoVerify) {
    Write-Step 'Checking it actually works'
    & $VenvPython -m reaperlive --version *> $null
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'reaperlive will not start.' }

    if (Test-Command 'ffmpeg') {
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("reaperlive-check-" + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp -Force | Out-Null
        $checkScript = Join-Path $tmp 'check.py'
        @'
import contextlib, io, json, sys
import numpy as np
import soundfile as sf
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

found = json.loads((project.parent / "reaperlive.json").read_text())["bpm"]
if not 118 <= found <= 122:
    raise SystemExit(f"tempo detection is off: {found} BPM, expected ~120")
print(f"    built and validated a project at {found:.1f} BPM")
'@ | Set-Content -LiteralPath $checkScript -Encoding ASCII  # no BOM on 5.1

        & $VenvPython $checkScript $tmp
        $checkCode = $LASTEXITCODE
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        if ($checkCode -ne 0) { Stop-WithError 'The smoke test failed - see the output above.' }
        Write-Ok 'End to end check passed'
    } else {
        Write-Warn 'Skipped the audio check because ffmpeg is missing.'
    }
}

# ------------------------------------------------------------------- done
$activate = if ($IsWindows) { ".\$VenvDir\Scripts\Activate.ps1" } else { "source $VenvDir/bin/activate" }

Write-Host ''
Write-Host 'Ready.' -ForegroundColor Green
Write-Host ''
Write-Host "  $activate"
Write-Host '  reaperlive-gui' -NoNewline
Write-Host '                                  the window' -ForegroundColor DarkGray
$example = if ($IsWindows) { '  reaperlive C:\Music\song.mp3 -o C:\band  ' } else { '  reaperlive ~/Music/song.mp3 -o ~/band     ' }
Write-Host $example -NoNewline
Write-Host '      one song' -ForegroundColor DarkGray
Write-Host '  reaperlive "https://youtu.be/..." -t both' -NoNewline
Write-Host '        REAPER + Live' -ForegroundColor DarkGray
Write-Host ''

if ($Dev) {
    Write-Step 'Running the test suite'
    & $VenvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'Tests failed.' }
}
