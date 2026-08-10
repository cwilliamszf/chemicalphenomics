"""Generate SYNTHETIC example raw-data export files to demo/smoke-test the pipeline.

This data is entirely fabricated -- it is NOT derived from any real
DanioVision recording or from the .trk files that motivated this pipeline
(those still can't be safely decoded; see daniovision_pipeline/trk_probe.py).
It exists only so the pipeline can be run and its output format inspected
before you have real EthoVision "Raw data" exports to point it at.

Usage: python generate_example_data.py [outdir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

OUTDIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "synthetic_raw"
N_WELLS = 5  # matches the 5 sample arenas (0-4) provided alongside this pipeline
FPS = 25.0
DURATION_S = 20 * 60  # 20 minute trial with a light phase then a dark phase
WELL_RADIUS_MM = 8.0

HEADER_TEMPLATE = """Number of header lines:,37
Time format,hh:mm:ss.ss
Data format,English (1.000,00)
Video frame rate,{fps}
Camera frame rate,{fps}
Frame rate (fps),{fps}
Track n.a.n. as,-
Trial name,Trial 1
Arena name,Arena {arena_number}
Subject,{well_id}
Start time,0:00:00.00
"""


def make_well_track(well_id: str, arena_number: int, group: str, rng: np.random.Generator) -> str:
    n = int(DURATION_S * FPS)
    t = np.arange(n) / FPS

    # "treated" fish are less active and hug the well wall more (thigmotaxis),
    # purely to give the demo something non-trivial to plot/test statistically.
    base_speed = 4.0 if group == "control" else 2.0
    bias_radius = 0.55 if group == "control" else 0.85

    theta = np.cumsum(rng.normal(0, 0.3, size=n))
    radius = np.clip(bias_radius * WELL_RADIUS_MM + rng.normal(0, 1.0, size=n).cumsum() * 0.02, 0.5, WELL_RADIUS_MM)
    x = radius * np.cos(theta)
    y = radius * np.sin(theta)

    speed = np.clip(base_speed + rng.normal(0, 1.5, size=n), 0, None)
    # zero out speed during "immobile" bouts so mobility/bout metrics are non-trivial
    immobile_mask = rng.random(n) < 0.15
    speed[immobile_mask] = 0.0
    dist = speed / FPS
    mobility = np.where(speed > 0.5, "Mobile", "Immobile")

    light_state = np.where(t < DURATION_S / 2, "Light", "Dark")

    lines = [HEADER_TEMPLATE.format(fps=FPS, arena_number=arena_number, well_id=well_id)]
    lines.append("Trial time,X center,Y center,Distance moved,Velocity,Movement,Light\n")
    lines.append("s,mm,mm,mm,mm/s,,\n")
    for i in range(n):
        lines.append(
            f"{t[i]:.2f},{x[i]:.3f},{y[i]:.3f},{dist[i]:.4f},{speed[i]:.3f},"
            f"{mobility[i]},{light_state[i]}\n"
        )
    return "".join(lines)


def main() -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    well_ids = ["A1", "A2", "A3", "A4", "A5"]
    groups = ["control", "control", "control", "treated", "treated"]

    groups_csv = ["well_id,group,notes"]
    for arena, (well_id, group) in enumerate(zip(well_ids, groups)):
        arena_number = arena + 1  # EthoVision's "Arena N" label is 1-based
        content = make_well_track(well_id, arena_number, group, rng)
        fname = OUTDIR / f"Raw data-Example-Trial 1-Arena {arena_number}.csv"
        fname.write_text(content)
        groups_csv.append(f"{well_id},{group},synthetic demo data")
        print(f"wrote {fname}")

    groups_path = OUTDIR.parent / "synthetic_groups.csv"
    groups_path.write_text("\n".join(groups_csv) + "\n")
    print(f"wrote {groups_path}")


if __name__ == "__main__":
    main()
