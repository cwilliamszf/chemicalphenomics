"""Superseded by ``ethovision_trk.py`` for real analysis -- see below.

This module intentionally does NOT decode the per-frame numeric trajectory;
it only reads the UTF-16 schema block (field names, trial start timestamp)
that sits before the numeric payload. It predates ``ethovision_trk.py``,
which reverse engineers and validates the full 58-byte-per-frame record
layout (see its module docstring for the byte layout and the evidence: it
was cross-checked against the manual header parse this project started
with, its own internal structural sanity checks (MergeState/Area/Elongation
ranges), and against the 5 sample arenas here, whose recovered X-pixel
ranges are cleanly non-overlapping and evenly spaced -- exactly what 5
side-by-side wells on one plate should look like).

Use ``ethovision_trk.py info FILE.trk`` for what this module gives you, and
``ethovision_trk.py convert`` to get an actual per-frame CSV. This module is
kept around only as a minimal, dependency-light sanity check (do these files
even look like EthoVision tracks, do several arenas share one trial). For
analysis you also still have the option of EthoVision's own "Raw data"
export via ``raw_export_parser`` -- useful for calibrating
``ethovision_trk.py``'s pixel coordinates to real-world cm/mm (its
``calibrate``/``validate`` subcommands) since the .trk file itself has no
physical scale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .well_mapping import parse_arena_index_from_filename

_KNOWN_FIELDS = [
    "SubjectId",
    "StartTime",
    "X Coordinate",
    "Y Coordinate",
    "Z Coordinate",
    "Area",
    "ChangedArea",
    "Elongation",
    "MergeState",
    "Enter Zone Id",
    "Hidden Zone Id",
]
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+")
_HEADER_SCAN_BYTES = 20000


@dataclass
class TrkFileInfo:
    path: Path
    file_size: int
    arena_index: int | None
    fields_present: list[str]
    trial_start_time: str | None


def probe_trk_file(path: str | Path) -> TrkFileInfo:
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(_HEADER_SCAN_BYTES)
    text = head.decode("utf-16-le", errors="ignore")
    fields = [name for name in _KNOWN_FIELDS if name in text]
    m = _TIMESTAMP_RE.search(text)
    return TrkFileInfo(
        path=path,
        file_size=path.stat().st_size,
        arena_index=parse_arena_index_from_filename(path.name),
        fields_present=fields,
        trial_start_time=m.group() if m else None,
    )


def probe_trk_folder(folder: str | Path) -> list[TrkFileInfo]:
    folder = Path(folder)
    return [probe_trk_file(p) for p in sorted(folder.glob("*.trk"))]


def summarize(infos: list[TrkFileInfo]) -> str:
    lines = [f"{'file':45s} {'arena':>5s} {'size':>10s}  start_time"]
    for info in infos:
        lines.append(
            f"{info.path.name:45s} "
            f"{str(info.arena_index):>5s} "
            f"{info.file_size:>10d}  "
            f"{info.trial_start_time}"
        )
    start_times = {i.trial_start_time for i in infos}
    if len(start_times) > 1:
        lines.append(
            "WARNING: files have different trial start times -- "
            "they may not all be from the same trial/recording."
        )
    sizes = {i.file_size for i in infos}
    if len(sizes) > 1:
        lines.append(
            "NOTE: file sizes differ -- trials may have run for different "
            "durations (or one arena lost tracking early)."
        )
    return "\n".join(lines)
