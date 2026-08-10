"""Safe, metadata-only inspection of raw EthoVision ``.trk`` files.

This intentionally does NOT decode the per-frame numeric trajectory. The
byte layout of the numeric payload is proprietary and undocumented; the
schema block at the start of the file is plain enough to read reliably
(it stores field names and a trial start timestamp as UTF-16 text), but
guessing the binary layout of the data that follows would risk producing
silently wrong position/distance/velocity numbers. Use this module for
sanity-checking a raw data folder (file sizes match, same trial start
time across arenas, expected fields present) -- not for analysis. For
analysis, export "Raw data" from EthoVision and use ``raw_export_parser``.
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
