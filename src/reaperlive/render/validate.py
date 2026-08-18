"""Structural checks on the files we write.

Neither REAPER nor Live is available on the machine that generates the project,
so the writers verify their own output: balanced chunks, resolvable media paths,
a sane tempo map, well-formed MIDI. It catches the failures that would otherwise
surface as "the project will not open" on someone else's laptop.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional
from xml.etree import ElementTree as ET


@dataclass
class Node:
    name: str
    args: str = ""
    lines: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def find_all(self, name: str) -> Iterator["Node"]:
        for child in self.children:
            if child.name == name:
                yield child
            yield from child.find_all(name)

    def field(self, key: str) -> Optional[str]:
        for line in self.lines:
            if line.split(" ", 1)[0] == key:
                parts = line.split(" ", 1)
                return parts[1] if len(parts) > 1 else ""
        return None


def parse_rpp(text: str) -> Node:
    """Parse an .RPP into a chunk tree. Raises ValueError on malformed input."""
    root: Optional[Node] = None
    stack: list[Node] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<"):
            head = line[1:].strip()
            name, _, args = head.partition(" ")
            node = Node(name=name, args=args)
            if stack:
                stack[-1].children.append(node)
            elif root is None:
                root = node
            else:
                raise ValueError(f"line {number}: a second root chunk {name!r}")
            stack.append(node)
        elif line == ">":
            if not stack:
                raise ValueError(f"line {number}: closing '>' with nothing open")
            stack.pop()
        else:
            if not stack:
                raise ValueError(f"line {number}: content outside any chunk")
            stack[-1].lines.append(line)
    if stack:
        raise ValueError(f"unclosed chunk <{stack[-1].name}")
    if root is None:
        raise ValueError("empty project")
    return root


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'`":
        return value[1:-1]
    return value.split(" ")[0]


def validate_rpp(path: Path) -> list[str]:
    """Return a list of problems; empty means the project looks sound."""
    problems: list[str] = []
    root = parse_rpp(path.read_text(encoding="utf-8"))
    if root.name != "REAPER_PROJECT":
        problems.append(f"root chunk is <{root.name}, expected <REAPER_PROJECT")

    if root.field("TEMPO") is None:
        problems.append("no TEMPO line")

    tracks = list(root.find_all("TRACK"))
    if not tracks:
        problems.append("project has no tracks")

    for source in root.find_all("SOURCE"):
        if not source.args.startswith("WAVE"):
            continue
        raw = source.field("FILE")
        if raw is None:
            problems.append("a WAVE source has no FILE line")
            continue
        media = path.parent / _unquote(raw)
        if not media.exists():
            problems.append(f"missing media: {_unquote(raw)}")

    for env in root.find_all("TEMPOENVEX"):
        last = -1.0
        for line in env.lines:
            if not line.startswith("PT "):
                continue
            parts = line.split()
            when, bpm = float(parts[1]), float(parts[2])
            if when < last:
                problems.append(f"tempo markers out of order at {when}")
            if not 20.0 <= bpm <= 400.0:
                problems.append(f"implausible tempo {bpm} at {when}s")
            last = when

    for source in root.find_all("SOURCE"):
        if not source.args.startswith("MIDI"):
            continue
        problems.extend(_validate_midi_events(source.lines))

    return problems


def _validate_midi_events(lines: list[str]) -> list[str]:
    problems: list[str] = []
    events = [line for line in lines if line.startswith(("E ", "e "))]
    if not events:
        problems.append("MIDI source contains no events")
        return problems
    open_notes: set[tuple[int, int]] = set()
    for line in events:
        parts = line.split()
        try:
            delta = int(parts[1])
            payload = [int(token, 16) for token in parts[2:]]
        except (ValueError, IndexError):
            problems.append(f"unparseable MIDI event: {line!r}")
            continue
        if delta < 0:
            problems.append(f"negative MIDI delta: {line!r}")
        if not payload:
            problems.append(f"empty MIDI event: {line!r}")
            continue
        status = payload[0]
        kind, channel = status & 0xF0, status & 0x0F
        if kind == 0x90 and len(payload) >= 3:
            if payload[2] == 0:
                open_notes.discard((channel, payload[1]))
            else:
                open_notes.add((channel, payload[1]))
        elif kind == 0x80 and len(payload) >= 3:
            open_notes.discard((channel, payload[1]))
    if open_notes:
        problems.append(f"{len(open_notes)} MIDI note(s) never released")
    return problems


def validate_midi_file(path: Path) -> list[str]:
    """Parse a standard MIDI file back and sanity-check its chunks."""
    problems: list[str] = []
    data = path.read_bytes()
    if data[:4] != b"MThd":
        return [f"{path.name}: not a MIDI file"]
    length = struct.unpack(">I", data[4:8])[0]
    fmt, ntracks, division = struct.unpack(">HHH", data[8:8 + length][:6])
    if fmt not in (0, 1):
        problems.append(f"unexpected MIDI format {fmt}")
    if division <= 0:
        problems.append("bad MIDI division")

    offset = 8 + length
    seen = 0
    while offset < len(data):
        tag = data[offset:offset + 4]
        size = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        if tag != b"MTrk":
            problems.append(f"unexpected chunk {tag!r}")
            break
        body = data[offset + 8:offset + 8 + size]
        if len(body) != size:
            problems.append("truncated MIDI track")
        if not body.endswith(b"\xff\x2f\x00"):
            problems.append("MIDI track missing end-of-track")
        offset += 8 + size
        seen += 1
    if seen != ntracks:
        problems.append(f"header claims {ntracks} tracks, found {seen}")
    return problems


def validate_als(path: Path) -> list[str]:
    """Check the Live set is gzipped, parses, and carries the nodes Live needs."""
    problems: list[str] = []
    try:
        with gzip.open(path, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        return [f"{path.name}: not gzip ({exc})"]
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        return [f"{path.name}: malformed XML ({exc})"]

    if root.tag != "Ableton":
        problems.append(f"root element is <{root.tag}>, expected <Ableton>")
    if root.get("MajorVersion") is None:
        problems.append("missing MajorVersion")
    live = root.find("LiveSet")
    if live is None:
        return problems + ["no <LiveSet>"]
    if live.find("Tracks") is None:
        problems.append("no <Tracks>")
    if live.find("MasterTrack") is None:
        problems.append("no <MasterTrack>")
    if not live.findall(".//AudioClip"):
        problems.append("no audio clips")

    ids = [el.get("Id") for el in live.iter() if el.get("Id") is not None]
    if len(ids) != len(set(ids)):
        problems.append("duplicate element Ids")
    return problems


def validate_project(project_dir: Path) -> list[str]:
    """Validate every project file found in ``project_dir``."""
    problems: list[str] = []
    for rpp in sorted(project_dir.glob("*.rpp")):
        problems.extend(f"{rpp.name}: {issue}" for issue in validate_rpp(rpp))
    for als in sorted(project_dir.glob("*.als")):
        problems.extend(f"{als.name}: {issue}" for issue in validate_als(als))
    for mid in sorted(project_dir.glob("*.mid")):
        problems.extend(f"{mid.name}: {issue}" for issue in validate_midi_file(mid))
    return problems
