"""CLI: aggregate multiple plates'/days' already-computed pipeline outputs.

Run cli.py separately for each plate/day first, exactly as normal (own
--outdir per plate). Then:

  python -m daniovision_pipeline.aggregate_cli \
    --manifest plates_manifest.csv \
    --outdir aggregated_results

--manifest is a CSV of plate_id,outdir[,date] -- one row per plate, outdir
pointing at that plate's own cli.py --outdir (see
config/plates_manifest_template.csv). This does not re-read raw
.trk/.btn/export files, only the metrics cli.py already computed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .aggregate import aggregate


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest", type=Path, required=True,
        help="CSV: plate_id,outdir[,date] -- see config/plates_manifest_template.csv"
    )
    parser.add_argument("--outdir", type=Path, required=True, help="Output directory for aggregated results")
    parser.add_argument(
        "--periods", type=Path, default=None,
        help="Optional periods CSV (same shape as cli.py's --periods), used only to draw "
             "boundary lines/labels on the pooled whole-trial activity-over-time plot. "
             "Period-level aggregation itself is auto-discovered from each plate's own "
             "periods/<name>/ output folders -- you don't need this flag for that to work."
    )
    args = parser.parse_args()
    aggregate(args.manifest, args.outdir, args.periods)


if __name__ == "__main__":
    main()
