# Contour — Technical Design

**Status:** approved for implementation
**Date:** 2026-08-09
**Source spec:** `docs/SPEC.md` (CONTOUR_BUILD_SPEC.md v1.0)
**Scope decisions (from the human):** new standalone repo; build M1–M4 plus client source; **fixtures only — no real OSM/DEM download in this environment**

---

## 1. What this document adds to the build spec

The build spec says *what* to build and *why*. It deliberately leaves open the decisions that only survive contact with the code. This document closes them:

- how "fixtures only" changes the data story without gutting the acceptance test
- the exact module boundaries, types, and function signatures
- the algorithms the spec names but does not specify (ascent hysteresis, smoothing, diversity collapse, candidate generation)
- three places where the build spec contradicts itself, and the ruling for each

Everything not addressed here is governed by the build spec verbatim.

---

## 2. The central adaptation: fixtures only

### 2.1 What we lose, stated plainly

The build spec's single most important acceptance test is that routing Duboce & Market → Fell & Baker at effort 5 discovers **The Wiggle**, unprompted. Without the real NorCal OSM extract and real 3DEP DEM, that test cannot be run against real-world data in this environment. Any claim that "The Wiggle test passes" must carry that asterisk.

We are not going to paper over this. Instead we make the test as close to load-bearing as it can honestly be.

### 2.2 What we keep

The Wiggle test is fundamentally a test of **the routing objective**, not of OSM's data quality. It asks: *given a street network where a flat zig-zag exists alongside a shorter climb, does our objective function find the zig-zag?* That question is answerable against a smaller, hand-verified network — and answering it there is strictly more diagnostic than answering it against all of SF, because when it fails you can see why.

So the fixture is not a mock. It is a **small, real-geometry San Francisco street graph** with:

- **Real intersection coordinates** for the Wiggle corridor (Duboce Triangle, Lower Haight, the Panhandle) and the competing direct routes over Buena Vista and the Haight ridge
- **Real street names and OSM-shaped tags** (`highway`, `bicycle`, `oneway`, `surface`), including deliberate `highway=steps` ways and a Filbert-grade wall
- Enough surrounding grid (SoMa, the Mission, a Twin Peaks approach) that alternatives are genuinely plural and the ranker's diversity collapse has something to do

Stage A candidate generation runs a **real graph search** over this network. Stage B re-ranks with the real DEM sampler. The only thing that is synthetic is the extent of the map and the provenance of the elevation values.

### 2.3 The fixture DEM

`data/dem/sf_fixture_dem.tif` is a real Cloud-Optimized GeoTIFF at 10 m resolution over the SF bbox, generated deterministically by `scripts/make_fixture_dem.py` from a **committed table of real SF elevation control points** (Twin Peaks 280 m, Ferry Building 3 m, Ocean Beach 5 m, Alamo Square 71 m, Buena Vista summit 174 m, Duboce & Market 17 m, Panhandle 42 m, and ~60 more), interpolated with a smooth radial basis function and a documented seed.

Consequences, stated honestly:

- The M2 landmark accuracy test (±3 m at 10 known landmarks) validates **the sampling machinery**, not the elevation data — a landmark that is itself a control point is a tautology. So the landmark test samples at points **held out** from the control set, and the tolerance is widened with a comment explaining that the real gate is re-running it against 3DEP.

**Measured tolerance, replacing the guess.** Task 4 selected the interpolation parameters by leave-one-out cross-validation over the control points (never over the holdouts, which must stay independent). The winning fit — `smoothing=2.0, neighbors=20` — has a **median LOO error of 13.80 m** (105-point sweep: 14.43 m; re-measured at 118 points after fix round 2's additions below), which is *larger than the ±12 m tolerance originally written here*. That tolerance was invented without reference to what a sparse city-wide control-point table can support, and no amount of tuning could have reached it. The gate is therefore:

- **non-summit holdouts: ±25 m** (≈1.8× median LOO).
- **summit holdouts: ±40 m** (≈2.9× median LOO), absorbing the systematic peak undershoot.

**"Summit" is defined by principle, not by enumeration of whatever is currently failing.** A summit holdout is *a holdout that is a local terrain maximum with no control point at its peak*: **Twin Peaks, Bernal Heights, Corona Heights**. An earlier revision of this document named only Twin Peaks and Bernal — those were simply the two failing at that moment, which was fitting the rule to the result. Corona Heights qualifies on the stated reason and is therefore in the set. Measured misses: −20.5 m, −21.0 m, −39.1 m. All 10 holdouts pass.

**This gate catches gross breakage — a flipped axis, a units error, a broken sampler — and certifies nothing about accuracy.** The fixture DEM cannot certify accuracy at any tolerance. The 3DEP run is the accuracy gate and it has not been run.

### 2.3.1 Fixed (fix round 4): the DEM was only San Francisco where it was sampled

Task 4's independent review established that outside the well-sampled central corridor, this DEM was **not** the city:

| Location | Real | DEM before | DEM after |
|---|---:|---:|---:|
| McLaren Park summit | ~158–170 m | **0.0** | 156.9 |
| Excelsior (Mission @ Geneva) | ~70 m | **0.0** | 67.9 |
| Presidio, Inspiration Point (actual coord) | ~90 m | **0.0** | 87.1 |
| Bayview Hill summit | ~130 m | 12.4 | 130.0 |
| Open SF Bay water | 0 m | **33–50** | 0.0 |
| Portola Dr @ Woodside | ~90–130 m | 224.0 | 110.4 |
| Sutro Tower base | ~254 m | 291.5 | 254.0 |
| North Beach (Columbus @ Union) | ~15 m | **100.2** | 15.1 |

The mechanism: with no nearby control points the RBF dives negative and is clamped to 0, so whole neighbourhoods read as sea level — while over the Bay it overshoots into a 50 m hill on open water. The "7.4% in-hull clamping" figure recorded earlier is not an interpolation-quality nicety; it is entire districts reading flat. For a router whose objective *is* climbing, a McLaren Park route would report zero gain.

No holdout and no sanity point sat near the affected regions, which is exactly why it shipped — **the validation set determined what could be seen.** The last two rows of that table were not in the review: they were found by sampling the northeast, a quadrant the review had not covered, which shows the same defect with the sign flipped. For a climbing router an invented 85 m hill in North Beach is worse than a missing one in McLaren Park.

That lesson then repeated one level up. The re-review sampled 14 further locations chosen precisely because *neither* the review nor the fix had touched them, and found the defect once more: **Yerba Buena Island's natural hill (~90 m) reads 0.0 m** across its summit, the Coast Guard station and the causeway, because the only nearby control points are sea-level anchors. Three independent passes, each finding the same class of error in whatever ground the previous pass had not sampled.

**Ruling: parked, not fixed.** The fixture street graph's easternmost vertex is at longitude −122.3867 and contains zero ways on Yerba Buena or Treasure Island, so no route can reach the affected ground and no route metric can be influenced by it. It is recorded here rather than repaired because the honest cost of chasing it exceeds any effect it can have. **If the graph is ever extended east — the Bay Bridge path, Treasure Island — this hole becomes live and must be filled first.**

**Fix round 4** added 98 control points (118 → 216 control; the 10 holdouts untouched): district coverage across the southeast, the west, the Presidio, the Richmond and the northeast waterfront; true values for the over-inflated central massif; and a 20-point ring of `0.0 m` sea-level anchors over open Bay and Pacific water. Named summits are cited (Sutro Tower 834 ft, Edgehill 734 ft, Mount Olympus 553 ft, Bayview Hill 425 ft, McLaren Park 519 ft); every other new point is marked `estimated from surrounding terrain` at `confidence=low` and says so. In-hull clamping over the land hull fell 7.40% → 3.35%; total clamping 39.77% → 30.99%. The LOO sweep winner moved to `smoothing=0.0, neighbors=None` (a global fit is now well conditioned), dropping p90 LOO error 92.24 m → 72.71 m.

`DISTRICT_COVERAGE_SAMPLES` in the generator is the missing validation set: 27 locations across all four quadrants plus open water on both sides, gated in `make fixtures` and in the test suite. Its bands are wide on purpose — most of those locations now have a control point within a few hundred metres, so it is a regression guard, not evidence of accuracy. **This remains the clearest possible argument for the 3DEP gate**, which is still outstanding.

### 2.3.2 The Filbert result is a round-trip, not an accuracy measurement

Adding control points took the Filbert Hyde→Leavenworth block from **−6.3% (sloping the wrong way)** to **30.8%** against a real 31.5%. The first half of that is genuine and important: a sparse-control DEM had silently inverted one of the steepest streets in the United States, and no test could have caught it.

The second half must not be overclaimed. **Both endpoints are control points added in the same round, and the Leavenworth value was itself derived by applying a 31.5% grade to the Hyde anchor** — which is marked `confidence=low`. A 31.5% grade went in and 30.8% came back. That demonstrates the fit no longer smears street-scale relief away; it is *not* independent evidence that the grade is correct. The block is also transversely unstable, reading 20.1% / 30.8% / 34.2% across three adjacent latitudes.

**The deeper limitation, stated plainly.** With a sparse, city-wide control-point table, this DEM's *effective* resolution is far coarser than its 10 m grid — it is a smooth surface through sparse samples and cannot represent street-scale relief except where control points are locally dense. SPEC §4.1 warns that SF's relief happens over 50–150 m and that a router fed a DEM which smears that detail "will confidently produce routes over hills it can't see." It says this of SRTM at 30 m; our fixture DEM is smoother still where samples are sparse. **We have reproduced the exact trap the spec warns about**, as an unavoidable consequence of the fixtures-only constraint.

Two mitigations, neither of which makes the fixture DEM a substitute for real data:
1. **Targeted control points** wherever terrain structure is load-bearing for a test. Fix round 2 added 13: both ends of the Filbert and Lombard steep blocks (Hyde and Leavenworth), five points along the Duboce/Lower Haight Wiggle corridor, Buena Vista Park's east and west base, Corona Heights base, and two points along the Twin Peaks Boulevard approach. Real sourced values only; `role=control` only, never on top of a holdout. Verified, not assumed: before this fix, the DEM sampled the Filbert St block between Hyde and Leavenworth at **−6.3% — sloping the wrong way** (no control point anywhere near it said otherwise); after, it samples at **30.8%**, matching the real, cited 31.5% grade.
2. **Honest labelling** — every scorecard carries `data_source: "fixture"`, so a fixture run can never be mistaken for a real one.

The remedy is the 3DEP run. It remains an outstanding gate, not a completed one.
- `elevation.py` never knows which DEM it has. Swapping in the real 3DEP COG is a path change in `manifest.json`.

### 2.4 `make data` is written, not run

The real pipeline (osmium clip, 3DEP mosaic, COG conversion, manifest emission) is implemented as working, reviewed code in `scripts/` and wired to `make data`. It is not executed here. `make fixtures` is the target that runs in CI and in this environment. `manifest.json` records which of the two produced the current data, and every eval scorecard carries that field — so a scorecard produced from fixtures is self-labelling and can never be mistaken for a real-data run.

---

## 3. Contradictions in the build spec, and rulings

**3.1 Units in `explanation`.** §8.3 says "All units converted at the presentation layer; the API/state stay metric." §6's example response contains `"explanation": "Saves 190 ft of climbing for 0.4 miles and 3 minutes more."` — imperial, from the API.

*Ruling:* the numeric fields stay strictly metric. `explain.py` emits a structured `explanation_parts` object **and** a rendered `explanation` string; the request gains an optional `"units": "imperial" | "metric"` field defaulting to `"imperial"`. The client may ignore the string and render from the parts. This satisfies both requirements without a lie in either direction. Recorded in `DECISIONS.md`.

**3.2 `flat_equivalent_m` double-counts the k-table.** §3.2 defines `D_eq = d + RATIO·ascent + Σ steep_penalty`, and §3.3 defines `steep_penalty = ascent_seg · RATIO · k(grade)`. With `k = 0` below 4%, a flat route's `D_eq` is `d + 100·ascent` — fine. But the `RATIO·ascent` term is already the full climb cost, so the steep penalty is an *additional* multiplier, not a replacement. That is the intent (k is a surcharge, not a total), and it is consistent — but only if `k(≤4%) = 0` means "no surcharge", not "no climb cost". Implementers get this wrong.

*Ruling:* implement exactly as written, and pin it with an explicit unit test asserting that a 1 m climb at 3% costs 100 m-equivalent while a 1 m climb at 10% costs 350 m-equivalent (100 + 100·2.5). Name the terms `base_climb_cost_m` and `steep_surcharge_m` in code so the distinction is unmissable.

**3.3 Detour budget vs. the `soma_flat_short` eval.** §3.4 enforces the budget as `distance ≤ budget × distance_of_fastest`. At detent 5 that permits +50%. The `soma_flat_short` case asserts `distance_ratio_to_fastest_lt: 1.08` at detent 5. The budget filter alone cannot produce that — it permits a 1.49 ratio; something must make the ranker *prefer* the short route.

*Ruling:* this is not a contradiction, it is the point — `D_eq` does the work. On flat SoMa both routes have ~0 ascent, so `D_eq ≈ d`, and the shorter route wins on distance alone. The budget is a ceiling, never a target. Implementers who try to satisfy `soma_flat_short` by tightening the budget are fixing the wrong thing; the plan says so explicitly in that task.

---

## 4. Module design

### 4.1 Dependency direction

```
constants.py ──> effort.py ──┐
                             ├──> ranker.py ──> api.py
elevation.py ────────────────┤        │
engines/ (Protocol) ─────────┘        └──> explain.py
```

`effort.py` and `explain.py` are pure — no I/O, no rasterio, no network. `elevation.py` owns all DEM access. `engines/` owns all routing-engine access. `ranker.py` orchestrates and is the only module that knows about both. `api.py` is a thin HTTP shell over `ranker.py`.

This is what makes Stage B independently testable without a routing engine in the loop, which the build spec calls out as the reason the two-stage design wins.

### 4.2 Core types (`contour/types.py`)

```python
LonLat = tuple[float, float]          # (lon, lat) — GeoJSON order, always

**Collection fields are `tuple`, never `list`.** `@dataclass(frozen=True)` blocks attribute *rebinding* only — a `list` field remains mutable in place, so a downstream module could reorder a candidate's geometry after another module had already scored it, and nothing would raise. These objects cross module boundaries between Stage A, Stage B, and the API layer; the immutability has to be real. Where a field must map keys, use `Mapping`, not `dict`.

```python
@dataclass(frozen=True)
class RouteCandidate:                 # Stage A output
    geometry: tuple[LonLat, ...]
    engine_distance_m: float
    engine_duration_s: float
    way_tags: tuple[Mapping[str, str], ...]  # parallel to edges, len == len(geometry) - 1
    source: str                       # "fixture" | "graphhopper" | "valhalla"
    slope_weight: float               # which Stage A weight produced this

@dataclass(frozen=True)
class ProfilePoint:
    dist_m: float
    ele_m: float

@dataclass(frozen=True)
class ElevationProfile:
    points: tuple[ProfilePoint, ...]
    ascent_m: float
    descent_m: float
    max_grade_pct: float

@dataclass(frozen=True)
class GradeSegment:
    start_idx: int
    end_idx: int
    grade_pct: float
    klass: str                        # flat | gentle | moderate | steep | wall  (serialized as "class")

@dataclass(frozen=True)
class SteepSection:
    start_dist_m: float
    end_dist_m: float
    grade_pct: float
    street: str | None
    ascent_m: float

@dataclass(frozen=True)
class ScoredRoute:                    # Stage B output
    candidate: RouteCandidate
    profile: ElevationProfile
    grade_segments: tuple[GradeSegment, ...]
    steep_sections: tuple[SteepSection, ...]
    distance_m: float                 # recomputed geodesic, not the engine's
    flat_equivalent_m: float
    effort_score: float
```

`distance_m` is recomputed from geometry rather than trusted from the engine, for the same reason the DEM is sampled independently: Stage B's numbers must be defensible on their own.

### 4.3 `effort.py` — pure functions

```python
def grade_k(grade_pct: float, table: KTable) -> float
def base_climb_cost_m(ascent_m: float, ratio: float) -> float
def steep_surcharge_m(ascent_m: float, grade_pct: float, table: KTable, k_scale: float) -> float
def flat_equivalent_m(distance_m: float, segments: Sequence[SegmentStat], model: EffortModel) -> float
def mechanical_work_j(distance_m: float, ascent_m: float, params: PhysicsParams) -> float
def derive_climb_equiv_ratio(params: PhysicsParams) -> float
```

`derive_climb_equiv_ratio` exists to make §3.1's "≈100" auditable: it computes `m·g / (C_rr·m·g + ½·ρ·CdA·v²)` and a test asserts the default `CLIMB_EQUIV_RATIO` is within 5% of it. If someone changes the rider mass, the test tells them the ratio moved.

The model is loaded from `contour/models/effort-v1.json`, versioned, with `model_version` echoed in every API response so eval scorecards can diff model versions — the build spec requires this.

### 4.4 `elevation.py` — the algorithms the spec leaves open

**Resampling.** Geodesic interpolation along the polyline at `PROFILE_SAMPLE_M` (10 m), using pyproj's `Geod` for distance and forward-azimuth interpolation. Not planar — SF spans enough longitude that planar interpolation drifts.

**Sampling.** `rasterio.sample` with **bilinear** interpolation, not nearest. Nearest-neighbour on a 10 m grid sampled at 10 m spacing produces a stair-step artefact that reads as alternating 0% and 8% grades.

**Smoothing.** A **Savitzky-Golay filter, window 9 samples (90 m), polynomial order 2**. Justification, which goes in `DECISIONS.md`: a moving average flattens genuine hill crests, which is exactly the error that makes a router think it can cross Nob Hill cheaply. Savitzky-Golay preserves peak amplitude while removing high-frequency sampling noise. 90 m is chosen to sit just under the ~150 m length of the shortest real SF grade change (a single block), so real blocks survive and sub-block noise does not.

**Ascent with hysteresis.** Per-sample thresholding is wrong — 40 consecutive +0.9 m steps would sum to zero ascent under a naive `if delta > MIN_RISE_M` filter, and to +36 m under no filter. The correct algorithm is peak-valley detection:

```
direction = UNDETERMINED          # we do not know which way we are going yet
anchor    = ele[0]                # last confirmed turning point
extreme   = ele[0]                # running extreme since the anchor

for each sample:
    if direction is UNDETERMINED:
        extreme_hi = max(extreme_hi, ele); extreme_lo = min(extreme_lo, ele)
        if ele >= anchor + MIN_RISE_M:  direction = UP;   extreme = ele
        if ele <= anchor - MIN_RISE_M:  direction = DOWN; extreme = ele
        # until one of those fires, nothing is committed — the series has
        # not yet moved far enough to have a direction at all

    if direction is UP:    extreme = max(extreme, ele)
                           if ele <= extreme - MIN_RISE_M:
                               commit (extreme - anchor) to ascent
                               anchor = extreme; extreme = ele; direction = DOWN

    if direction is DOWN:  extreme = min(extreme, ele)
                           if ele >= extreme + MIN_RISE_M:
                               commit (anchor - extreme) to descent
                               anchor = extreme; extreme = ele; direction = UP

commit the final open leg from anchor to extreme
```

**The initial phase is load-bearing and an earlier revision of this document omitted it.** Without an `UNDETERMINED` state the algorithm must assume a direction from the first sample pair, and a series that opens with sampling noise then locks to the wrong direction — which makes the flat-road-with-±0.4 m-noise case report spurious ascent. Task 6's implementer found this by testing rather than by reading, and added the phase.

**The reversal boundary is inclusive (`>=` / `<=`).** A reversal of exactly `MIN_RISE_M` commits. This is a choice, not a derivation — 1.0 m is not sub-metre noise, so it should count — and it is pinned by a test at exactly `MIN_RISE_M` and at `MIN_RISE_M - 0.01` so a later refactor cannot move it silently.

This ignores sub-metre wiggle while correctly accumulating a long shallow climb. Unit tests pin both failure modes.

**Grade.** Computed over a **3-sample (30 m) centred window**, not sample-to-sample, because a 10 m baseline on 10 m data amplifies noise into phantom double-digit grades. `max_grade_pct` uses the same windowed series, so the honesty warnings are not driven by a single bad pixel.

### 4.5 `engines/` — the Protocol and the fixture engine

```python
class RoutingEngine(Protocol):
    name: str
    def candidates(self, origin: LonLat, destination: LonLat, *,
                   n: int, slope_weights: Sequence[float],
                   avoid_stairs: bool) -> list[RouteCandidate]: ...
    def health(self) -> EngineHealth: ...
```

Three implementations:

- **`fixture.py`** — the one that runs here. Loads `data/fixtures/sf_graph.geojson`, builds an adjacency structure, and runs A* with edge weight `length · (1 + slope_weight · max(0, grade)²)`. Diversity comes from running the search once per `slope_weight` **plus** iterative penalty injection (Yen-style plateau avoidance: after each path, multiply the weight of its edges by 1.6 and re-search) until `n` distinct paths are found or the search space is exhausted. This is real routing, not a lookup table.
- **`graphhopper.py`** — HTTP adapter against a self-hosted GraphHopper, written against the version and custom-model schema verified from current official docs (see `.research/API_FACTS.md`). Container config committed; not run here.
- **`valhalla.py`** — same Protocol, `use_hills` mapped from `slope_weight`. Behind a flag, as the spec requires.

`avoid_stairs` is enforced at the **edge-filter level inside every engine**, not in the ranker. A stairway must never be in a candidate in the first place — filtering it post-hoc would let it consume a candidate slot and would make `no_stairs` pass for the wrong reason.

### 4.6 `ranker.py` — Stage B

Ordered pipeline, each step a separately tested function:

1. **Score** every candidate: resample → sample DEM → smooth → profile → grade segments → steep sections → `D_eq`.
2. **Identify the fastest** candidate by `engine_duration_s` (the only place duration is authoritative).
3. **Budget filter:** drop candidates with `distance_m > DETOUR_BUDGET[pref] × fastest.distance_m`. Never drop the fastest itself, even if it somehow violates its own budget.
4. **Diversity collapse:** snap each candidate's 10 m samples to a 20 m grid, compute pairwise Jaccard over the resulting cell sets; when `J > DIVERSITY_OVERLAP_MAX` (0.70), keep the lower `effort_score` and discard the other. Grid-cell Jaccard is chosen over Fréchet or Hausdorff distance because it is O(n) per pair, is symmetric, and matches the "shares >70% of geometry" phrasing directly.
5. **Rank** by `effort_score` ascending.
6. **Label:** lowest `effort_score` → `Gentlest`; lowest `engine_duration_s` → `Fastest`; the remaining one → `Balanced`. When one route holds two labels or only one survives → `Only route`.
7. **Warn:** if `min(ascent_m)` across *all scored candidates before filtering* exceeds `UNAVOIDABLE_CLIMB_M` (64 m, from the spec's own example), emit `UNAVOIDABLE_CLIMB` with that minimum. Computing it pre-filter matters — the budget filter could otherwise hide the fact that every route climbs.

### 4.7 `explain.py` — deterministic templates

Template-based, no LLM, as the spec demands. Input is a `ScoredRoute` plus the fastest route; output is `ExplanationParts` (metric numbers + a verb: `saves` / `costs` / `matches`) and a rendered string. Rounding is to the nearest 10 ft / 0.1 mi / 1 min so the sentence reads like a human wrote it. The "no gentle route exists" copy is a separate template keyed off the warning, not a special case inside the comparison template.

---

## 5. Eval harness

`evals/run_eval.py` executes `golden_routes.yaml` against the API. Assertion vocabulary, each a small pure predicate registered in a dict:

`ascent_m_lt` · `ascent_m_lt_ratio_of_fastest` · `max_grade_pct_lt` · `passes_near` · `never_passes_near` · `distance_ratio_to_fastest_lt` · `excludes_bbox` · `warning_present` · `route_returned` · `no_way_tag` · `prefers_way_tag` · `error_code`

`effort_preference` accepts a scalar or a list; a list runs the case once per value and requires all to pass (the `filbert_wall_never` case needs this).

The scorecard is written to `evals/results/<timestamp>.json` in the §7.2 shape, plus a `data_source: "fixture" | "real"` field. The four regression gates from §7 are implemented as a `--gate` mode that exits non-zero, and CI runs it.

The 22-case golden set covers the 7 named cases plus the 15 required additions: Sunset↔Richmond, Mission↔Dogpatch, Presidio access, Potrero Hill descent, Bayview flats, and three out-of-bbox cases asserting `OUT_OF_SERVICE_AREA`.

**Honesty constraint on the fixture set:** every golden case must be routable on the fixture graph, or it must be marked `requires: real_data` and reported as SKIPPED rather than PASSED. A scorecard that counts skips as passes is worse than no scorecard.

---

## 6. Client design

Expo + TypeScript strict + expo-router, five screens per §8.1. Zustand for UI state, TanStack Query for server state.

The elevation profile is the hero component and is hand-rolled on `react-native-svg`: a `<Path>` per grade class so colours come from the shared ramp, a tap handler that maps x → `dist_m` → map coordinate, and VoiceOver labels describing each steep section in words.

The grade ramp lives in exactly one file (`theme/grade.ts`) and is consumed by the map polyline, the profile chart, and the steep-section chips — the spec requires consistency across all three, and three copies of a colour table will diverge.

Units convert at the presentation layer only, via a `useUnits()` hook. No `any`; strict mode on.

MapLibre requires a custom dev client, which means Expo Go is off the table from day one — the build spec says to plan for this, so the dev-client config is part of the first client task, not a later discovery.

**What cannot be verified here:** M5's DoD is "demonstrably satisfied on a physical iPhone". There is no device and no App Store access in this environment. The client is written, typechecked, and unit-tested with React Native Testing Library; the device gate stays open and is reported as such.

---

## 7. Testing strategy

| Layer | Tool | Gate |
|---|---|---|
| Pure logic (`effort`, `explain`) | pytest, table-driven | 100% branch coverage — they are pure, there is no excuse |
| `elevation` | pytest against the fixture COG | Held-out landmark accuracy; hysteresis failure modes pinned |
| `engines/fixture` | pytest on the graph fixture | Stairs excluded; wall excluded; alternatives distinct |
| `ranker` | pytest with a stub engine | Budget, diversity, labelling, warnings — no engine, no DEM |
| `api` | pytest + httpx | Response validates against a committed JSON Schema derived from §6; every error code exercised |
| Golden routes | `make eval` | 4 regression gates |
| Client | vitest + RNTL | Card list, profile chart, unit conversion |

`make verify` = lint (ruff) + typecheck (mypy strict, tsc) + unit tests + `make eval` from M3 onward. Red `make verify` blocks commit, per §9.1.

---

## 8. Repo layout

Exactly §5.1, with three additions:

```
contour/
├── scripts/                    # make_fixture_dem.py, make_fixture_graph.py, fetch_3dep.py, clip_osm.py
├── data/fixtures/              # sf_graph.geojson, elevation_control_points.csv  (committed, small)
└── .research/API_FACTS.md      # verified library facts, with source URLs
```

---

## 9. Open risks

| Risk | Standing |
|---|---|
| Wiggle test proves the objective, not the map | Accepted and labelled. Real-data run is the outstanding gate. |
| Fixture graph author bias — building a graph where the Wiggle wins by construction | Mitigated: the graph is authored from real street geometry by a subagent that is **not told which route should win**, and the competing direct routes are required to be shorter. |
| Fixture DEM control points are themselves approximate | Accepted. Landmark test uses held-out points and a widened tolerance, and says why. |
| GraphHopper adapter unrunnable here | Written against verified docs, container config committed, marked untested. |
| Client device gate | Open. Reported, not claimed. |
