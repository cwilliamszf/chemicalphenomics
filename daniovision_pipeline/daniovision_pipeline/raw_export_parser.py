"""Parse EthoVision / DanioVision "Raw data" per-track exports (CSV or Excel).

Why this instead of the .trk files
-----------------------------------
The ``.trk`` files inside an EthoVision project's raw data folder
(``Track_filet0000a0004o0000_0001.trk``) are the software's internal,
undocumented binary track database, not an interchange format. Inspecting
the samples confirms they DO contain a full per-frame schema (SubjectId,
StartTime, X/Y/Z Coordinate, Area, ChangedArea, Elongation, MergeState,
Enter/Hidden Zone Id) but the numeric payload is packed in a proprietary,
publicly undocumented layout -- there is no way to reverse engineer the
byte layout with certainty from samples alone, and guessing wrong would
silently produce plausible-looking but wrong distance/velocity numbers.
See ``trk_probe.py`` for what can be safely read from these files.

The safe, accurate, and officially supported path is EthoVision's own
"Export > Raw data" feature (per-arena Excel/CSV), which is what this
module reads. In EthoVision/DanioVision: Analysis tab -> Statistics ->
pick "Raw data" as export type -> select one track per arena -> Export.
That produces one file per well with a small property header followed by
a per-frame data table -- exactly what feeds this parser.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

# Canonical internal column name -> accepted header aliases (case-insensitive
# substring match against the exported column name).
COLUMN_ALIASES: dict[str, list[str]] = {
    "time_s": ["trial time", "recording time"],
    "x_mm": ["x center", "x-center", "x nose"],
    "y_mm": ["y center", "y-center", "y nose"],
    "distance_mm": ["distance moved"],
    "velocity_mms": ["velocity"],
    "mobility_state": ["movement", "activity state", "mobility state"],
    "in_zone": ["in zone"],
    "light_state": ["light", "stimulus", "trial control unit state"],
}

_MISSING_TOKENS = {"-", "", "na", "n/a", "nan", "null", "none"}


def _find_header_row(lines: list[list[str]]) -> int:
    """Locate the row that contains the real column headers.

    EthoVision raw-data exports start with a property block (often
    beginning with a literal "Number of header lines" row) before the
    real header row, which always contains a time column.
    """
    for i, row in enumerate(lines):
        lowered = [str(c).strip().lower() for c in row]
        if any("trial time" in c or "recording time" in c for c in lowered):
            return i
    raise ValueError(
        "Could not find a header row containing a time column "
        "('Trial time' / 'Recording time'). Is this really an EthoVision "
        "raw data export?"
    )


def _map_columns(columns: list[str]) -> dict[str, str]:
    """Map raw exported column names to canonical internal names."""
    mapping: dict[str, str] = {}
    for col in columns:
        col_l = str(col).strip().lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if canonical in mapping.values():
                continue
            if any(alias in col_l for alias in aliases):
                mapping[col] = canonical
                break
    return mapping


def _coerce_numeric(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.where(~cleaned.str.lower().isin(_MISSING_TOKENS), other=pd.NA)
    return pd.to_numeric(cleaned, errors="coerce")


def load_raw_track(path: str | Path) -> pd.DataFrame:
    """Load one EthoVision raw-data export file into a tidy per-frame DataFrame.

    Returns a DataFrame with (a subset of, depending on what was exported)
    the canonical columns: time_s, x_mm, y_mm, distance_mm, velocity_mms,
    mobility_state, in_zone, light_state.
    """
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        raw = pd.read_excel(path, header=None, dtype=str)
        lines = raw.values.tolist()
    else:
        # Read row-by-row rather than via pandas' whole-file parser: the
        # property header block and the per-frame data table legitimately
        # have different numbers of columns in real EthoVision exports.
        with open(path, newline="") as fh:
            lines = list(csv.reader(fh))

    header_idx = _find_header_row(lines)
    header = [str(c) for c in lines[header_idx]]

    data_start = header_idx + 1
    # Some exports insert a units row (e.g. "s", "mm", "mm/s") right after
    # the header; detect and skip it if the first data cell isn't numeric.
    if data_start < len(lines) and lines[data_start]:
        first_cell = str(lines[data_start][0]).strip()
        try:
            float(first_cell)
        except ValueError:
            data_start += 1

    data_rows = [
        row[: len(header)] + [""] * (len(header) - len(row))
        for row in lines[data_start:]
        if row and any(cell.strip() for cell in row)
    ]
    body = pd.DataFrame(data_rows, columns=header)

    col_map = _map_columns(header)
    if "time_s" not in col_map.values():
        raise ValueError(f"{path}: no time column found among {header}")

    out = pd.DataFrame(index=body.index)
    for orig_col, canonical in col_map.items():
        if canonical in {"mobility_state", "in_zone", "light_state"}:
            out[canonical] = body[orig_col].astype(str).str.strip()
        else:
            out[canonical] = _coerce_numeric(body[orig_col])

    out = out.dropna(subset=["time_s"]).reset_index(drop=True)
    return out


def load_raw_tracks(paths: list[str | Path]) -> dict[str, pd.DataFrame]:
    """Load several raw-data export files, keyed by their filename."""
    return {Path(p).name: load_raw_track(p) for p in paths}
