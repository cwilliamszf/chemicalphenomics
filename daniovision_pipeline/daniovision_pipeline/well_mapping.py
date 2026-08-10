"""Map raw data files to plate wells, and wells to experimental groups.

EthoVision numbers "arenas" in the order they were defined in Trial Setup for
the multi-well grid. There is no universal, publicly documented arena-to-well
convention -- it depends entirely on how the arena grid was drawn for a given
project. ``config/plate_layout_24well.csv`` ships a row-major default
(arena 0 = A1, arena 1 = A2, ... arena 23 = D6, for a 4-row x 6-column plate)
because that is how EthoVision's "Track multiple arenas" wizard lays out a
grid by default. **Verify this against your own Trial Setup > Arena Settings
screen (or the arena numbers overlaid on the video) before trusting results**
-- if arenas were drawn column-first, mirrored, or in a custom order, edit
the CSV to match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Matches the internal EthoVision per-arena file naming convention seen in
# raw project folders, e.g. "Track_filet0000a0004o0000_0001.trk"
# t = trial index, a = arena index, o = object/track index within the arena.
_TRK_NAME_RE = re.compile(r"t(?P<trial>\d+)a(?P<arena>\d+)o(?P<object>\d+)", re.IGNORECASE)

# Matches common "raw data export" filenames, e.g. "...-Arena 4.xlsx",
# "...Arena_04.csv", "...arena4.csv"
_ARENA_WORD_RE = re.compile(r"arena[ _-]?0*(?P<arena>\d+)", re.IGNORECASE)


def parse_arena_index_from_filename(filename: str) -> int | None:
    """Best-effort extraction of a 0-based arena index from a filename.

    Tries the internal ``t####a####o####`` convention first, then falls back
    to a looser "Arena N" pattern used by many raw-data export filenames.
    Returns None if no arena index could be determined.
    """
    name = Path(filename).name
    m = _TRK_NAME_RE.search(name)
    if m:
        return int(m.group("arena"))
    m = _ARENA_WORD_RE.search(name)
    if m:
        # "Arena N" in the EthoVision UI is usually 1-based; internal
        # indices are 0-based. Assume 1-based here and convert.
        return int(m.group("arena")) - 1
    return None


@dataclass
class PlateLayout:
    """arena_index (0-based) <-> well_id lookup for one plate."""

    table: pd.DataFrame  # columns: arena_index, well_id, row, col

    @classmethod
    def from_csv(cls, path: str | Path) -> "PlateLayout":
        table = pd.read_csv(path)
        required = {"arena_index", "well_id"}
        missing = required - set(table.columns)
        if missing:
            raise ValueError(f"Plate layout CSV {path} is missing columns: {missing}")
        return cls(table=table)

    def well_for_arena(self, arena_index: int) -> str | None:
        row = self.table.loc[self.table["arena_index"] == arena_index]
        if row.empty:
            return None
        return str(row.iloc[0]["well_id"])

    def arena_for_well(self, well_id: str) -> int | None:
        row = self.table.loc[self.table["well_id"] == well_id]
        if row.empty:
            return None
        return int(row.iloc[0]["arena_index"])


def load_groups(path: str | Path) -> pd.DataFrame:
    """Load the well_id -> group assignment CSV (see config/groups_template.csv)."""
    groups = pd.read_csv(path)
    required = {"well_id", "group"}
    missing = required - set(groups.columns)
    if missing:
        raise ValueError(f"Groups CSV {path} is missing columns: {missing}")
    groups = groups.copy()
    groups["well_id"] = groups["well_id"].astype(str).str.strip()
    groups["group"] = groups["group"].astype(str).str.strip()
    unassigned = groups.loc[(groups["group"] == "") | (groups["group"].str.lower() == "nan")]
    if not unassigned.empty:
        wells = ", ".join(unassigned["well_id"])
        raise ValueError(
            f"Groups CSV {path} has no group assigned for well(s): {wells}. "
            "Fill in every well before running the pipeline (or remove unused well rows)."
        )
    return groups


def build_file_well_group_table(
    raw_files: list[Path],
    plate_layout: PlateLayout,
    groups: pd.DataFrame,
) -> pd.DataFrame:
    """Resolve each raw data file to a well_id and group, or flag it as unresolved."""
    rows = []
    for f in raw_files:
        arena_index = parse_arena_index_from_filename(f.name)
        well_id = plate_layout.well_for_arena(arena_index) if arena_index is not None else None
        group = None
        if well_id is not None:
            match = groups.loc[groups["well_id"] == well_id]
            if not match.empty:
                group = match.iloc[0]["group"]
        rows.append(
            {
                "file": str(f),
                "arena_index": arena_index,
                "well_id": well_id,
                "group": group,
            }
        )
    table = pd.DataFrame(rows)
    unresolved = table[table["well_id"].isna()]
    if not unresolved.empty:
        names = ", ".join(Path(p).name for p in unresolved["file"])
        raise ValueError(
            "Could not determine the well for the following file(s) from their "
            f"filename: {names}. Rename them to include an arena index "
            "(e.g. '...a0004...' or '...Arena 4...'), or map them manually."
        )
    ungrouped = table[table["group"].isna()]
    if not ungrouped.empty:
        wells = ", ".join(ungrouped["well_id"])
        raise ValueError(
            f"No group assignment found for well(s): {wells}. Check your groups CSV."
        )
    return table
