"""Reference constants — the single source of truth for Contour's numeric
model. See `docs/SPEC.md` Appendix A ("Reference constants") and the
Global Constraints doc for the m1-m5 plan; every task implicitly inherits
these exact values.

Nothing in this module has I/O or depends on any other Contour module —
it sits at the root of the dependency graph (design doc §4.1).
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# --- Service area ----------------------------------------------------------

# SPEC.md §4.2 "Bounding box". Order is (W, S, E, N) — i.e. (min lon, min
# lat, max lon, max lat). Coordinate order within each pair is (lon, lat),
# per Global Constraints.
SF_BBOX: Final[tuple[float, float, float, float]] = (-122.5350, 37.7000, -122.3500, 37.8350)

# --- Physics baseline (SPEC.md §3.1) ---------------------------------------

RIDER_MASS_KG: Final[float] = 85.0  # SPEC.md §3.1 — rider + bike
GRAVITY: Final[float] = 9.81  # SPEC.md §3.1
C_RR: Final[float] = 0.005  # SPEC.md §3.1 — pavement, city tires
AIR_DENSITY: Final[float] = 1.225  # SPEC.md §3.1 — rho
CDA_M2: Final[float] = 0.40  # SPEC.md §3.1 — upright commuter
V_FLAT_MPS: Final[float] = 4.17  # SPEC.md §3.1 — 15 km/h

# --- Effort model (SPEC.md §3.1-3.3) ---------------------------------------

# SPEC.md §3.1 — meters of flat riding per meter climbed. The core tuning
# constant; `derive_climb_equiv_ratio` (effort.py, Task 3) makes the "~100"
# auditable against the physics constants above.
CLIMB_EQUIV_RATIO: Final[float] = 100.0

# SPEC.md §4.4 "Ascent with hysteresis" — ignore sub-meter noise when
# summing ascent via peak-valley detection.
MIN_RISE_M: Final[float] = 1.0

# SPEC.md §3.5 / §4.4 — geodesic resampling spacing for route geometry.
PROFILE_SAMPLE_M: Final[float] = 10.0

# SPEC.md §3.3 "Steepness superlinearity" — k(grade) lookup table, sorted
# ascending by grade threshold: (grade_pct_upper_bound, k).
GRADE_K_TABLE: Final[tuple[tuple[float, float], ...]] = (
    (4, 0.0),
    (6, 0.3),
    (9, 1.0),
    (12, 2.5),
    (15, 5.0),
    (999, 12.0),
)

# SPEC.md §3.4 "The user-facing slider → engine parameters" — detour
# budget (distance ratio vs. the fastest candidate) by slider detent 1-5.
DETOUR_BUDGET: Final[Mapping[int, float]] = MappingProxyType(
    {1: 1.05, 2: 1.15, 3: 1.25, 4: 1.35, 5: 1.50}
)

# SPEC.md §3.4 — k-table multiplier by slider detent 1-5.
K_SCALE: Final[Mapping[int, float]] = MappingProxyType({1: 0.0, 2: 0.5, 3: 1.0, 4: 2.0, 5: 4.0})

# SPEC.md §3.5 / design doc §4.6 "Diversity collapse" — Jaccard overlap
# threshold above which two candidates are considered the same route.
DIVERSITY_OVERLAP_MAX: Final[float] = 0.70

# --- Additions introduced in Task 2 (m1-m5 plan, task-2-brief.md Step 3) --

# design doc §4.6 "Warn" step / SPEC.md §12 DoD #4 (Twin Peaks example:
# "climbs at least 210 ft" == ~64 m) — threshold for the UNAVOIDABLE_CLIMB
# warning, computed as min(ascent_m) across all scored candidates.
UNAVOIDABLE_CLIMB_M: Final[float] = 64.0

# design doc §4.4 "Smoothing" — Savitzky-Golay filter window, in samples
# (9 samples * PROFILE_SAMPLE_M = 90 m).
SMOOTH_WINDOW_SAMPLES: Final[int] = 9

# design doc §4.4 "Smoothing" — Savitzky-Golay filter polynomial order.
SMOOTH_POLY_ORDER: Final[int] = 2

# design doc §4.4 "Grade" — centred window (samples) used to compute grade,
# rather than sample-to-sample, to avoid amplifying DEM noise.
GRADE_WINDOW_SAMPLES: Final[int] = 3

# design doc §4.6 "Diversity collapse" — grid cell size (metres) that
# candidate geometry is snapped to before computing pairwise Jaccard
# overlap.
DIVERSITY_GRID_M: Final[float] = 20.0

# SPEC.md §3.5 "Stage A — Candidate generation" — N candidates requested
# from the routing engine per route request (spec range is 6-10; 8 chosen
# as the task-2 brief's exact value).
CANDIDATE_COUNT: Final[int] = 8
