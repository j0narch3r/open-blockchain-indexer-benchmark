#!/usr/bin/env python3
"""Leave-one-out cross-validation sweep for the fixture DEM's RBF parameters.

Selects `smoothing`/`neighbors` for `scripts/make_fixture_dem.py` by
holding out each of the 105 `control` points in turn, fitting on the
remaining 104, and measuring prediction error — deliberately never
touching the 10 `holdout` points, which exist to be an independent check
on whatever this sweep picks (design doc §2.3; see task-4 fix round 1 for
the full reasoning).

Prints the full sweep table (every combination -> median/p90 LOO error)
and the selected winner, so the choice in `make_fixture_dem.py`'s
`SMOOTHING`/`NEIGHBORS` constants is auditable against this output rather
than asserted. Deterministic (no RNG) — rerunning reproduces the same
table.

Run:
    cd services/api && uv run python ../../scripts/cv_sweep.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from make_fixture_dem import (  # noqa: E402
    DEFAULT_CSV_PATH,
    load_control_points,
    run_loo_sweep,
    select_best,
)


def main() -> None:
    points = load_control_points(DEFAULT_CSV_PATH)
    control_points = [p for p in points if p.role == "control"]
    print(f"Leave-one-out sweep over {len(control_points)} control points")
    print(f"{'smoothing':>10} {'neighbors':>10} {'median_loo_m':>13} {'p90_loo_m':>10}")

    results = run_loo_sweep(control_points)
    for r in results:
        neighbors_str = "global" if r.neighbors is None else str(r.neighbors)
        print(
            f"{r.smoothing:>10} {neighbors_str:>10} "
            f"{r.median_loo_error_m:>13.2f} {r.p90_loo_error_m:>10.2f}"
        )

    winner = select_best(results)
    neighbors_str = "global" if winner.neighbors is None else str(winner.neighbors)
    print()
    print(
        f"Winner: smoothing={winner.smoothing}, neighbors={neighbors_str} "
        f"(median={winner.median_loo_error_m:.2f} m, p90={winner.p90_loo_error_m:.2f} m)"
    )


if __name__ == "__main__":
    main()
