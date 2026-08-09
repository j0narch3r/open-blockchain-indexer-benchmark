# CONTOUR — Build Spec v1.0
### An elevation-optimized bicycle routing app for San Francisco
**Intended reader: an autonomous coding agent (Claude Code). Human review at three checkpoints only.**

---

## 0. TL;DR for the agent

Build a React Native (Expo) app plus a small Python routing service that answers one question better than anything else on the market:

> *"Given where I am and where I'm going, what is the least-effort route — where effort is dominated by climbing, not distance — and how much extra distance does that cost me?"*

Scope is San Francisco city limits only. The differentiator is **a tunable effort/detour dial and a transparent effort model**, not a binary "avoid hills" toggle.

**The single acceptance test that matters most:** routing from `Duboce & Market` to `Fell & Baker` (the Panhandle) at low hill tolerance must return **The Wiggle** — the historic zig-zag through Duboce Triangle / Lower Haight that SF cyclists have used for a century to cross the city with ~0 net climb. If the engine does not discover The Wiggle unprompted, the routing objective is wrong. Do not proceed past Milestone 3 until it does.

---

## 1. Why this is worth building (competitive reality — read before designing)

Do not assume greenfield. This partially exists:

| Product | What it does | The gap |
|---|---|---|
| Apple Maps | "Avoid Hills" toggle on cycling directions; elevation chart; live in SF since iOS 14 | Binary toggle, opaque objective, no control over how much detour you'll accept, no explanation |
| Google Maps | Bike routes + elevation profile (desktop-first) | Optimizes time; elevation is display, not objective |
| Valhalla (open source) | `use_hills` costing parameter, 0.0–1.0 | A comfort factor, not a minimize-gain objective; not a consumer app |
| GraphHopper (open source) | Custom models with `average_slope` / `max_slope` encoded values + `distance_influence` | Genuinely expressive, but it's a routing engine, not a product |
| BRouter | Extremely deep hill-cost profiles (`uphillcost`, cutoffs, buffers, "valley mode") | Enthusiast tooling, Android/offline, brutal UX |
| Komoot / Ride with GPS | Great elevation profiles, route planning | Planning tools for rides, not A→B urban navigation with effort as the objective |
| flattestroute.com | Flattest-route finder | Web, road-trip oriented, not a bike navigator |

**The defensible wedge:** everyone else treats elevation as either a display artifact or a boolean. Nobody exposes the actual tradeoff — *"this route saves you 180 ft of climbing and costs you 0.6 miles and 4 minutes"* — as the primary interaction. That sentence is the product.

**Also true and worth internalizing:** Apple's flat route in SF still crosses real grade, because there are places (Twin Peaks, Bernal, Pac Heights) where the terrain simply wins. Contour must be honest about that. "There is no flat way to do this; here is the gentlest" is a valid, high-trust answer.

---

## 2. Product definition

### 2.1 Non-negotiable principles
1. **Effort is the objective function, time is a readout.** Never sort routes by ETA.
2. **Always show the trade.** Every alternative displays Δclimb, Δdistance, Δtime versus the fastest route.
3. **Never lie about a wall.** If max grade > 8% anywhere, say so before the user leaves.
4. **A flat route that adds 40% distance is usually wrong.** Detour must be bounded and user-controlled.

### 2.2 MVP scope (v1)
- Origin/destination entry (search + "use my location" + long-press on map)
- Route computation returning **3 ranked alternatives**: Gentlest / Balanced / Fastest
- Effort slider (5 detents) that re-ranks and re-requests
- Elevation profile chart with grade-colored segments and steep-section markers
- Route detail: total ascent, total descent, max grade, "flat-equivalent distance", steepest block callout
- Map with route polyline colored by grade
- Follow-along mode: user location on route, off-route detection, "you're off route — recompute?" (**no turn-by-turn voice nav in v1**)
- Save a route as a favorite (local only)

### 2.3 Explicitly OUT of scope for v1
Accounts/auth. Cloud sync. Voice turn-by-turn. Android release (build it, don't ship it). Anywhere outside SF. Social/sharing. E-bike modes. Weather/wind. Traffic. Strava import. Offline maps (tile format chosen to make this easy later, but not implemented).

### 2.4 User stories (acceptance-testable)
- **US-1** As a commuter, I enter a destination and see three routes labeled by effort, with climb in feet shown before I tap anything.
- **US-2** As a rider who hates hills, I set the slider to "Flattest" and get a route with the minimum achievable ascent within a 35% distance budget.
- **US-3** As a rider, I can see *where* the climbing happens on a profile chart and tap a spike to pan the map to that block.
- **US-4** As a rider, when no gentle route exists, I get an explicit warning naming the unavoidable climb and its grade.
- **US-5** As a rider mid-route, I can see my position on the route and my remaining climb.

---

## 3. The effort model (this is the actual IP — implement it deliberately)

### 3.1 Physics baseline
Mechanical work over a route segment, ignoring braking losses:

```
W = m·g·Δh_gain  +  C_rr·m·g·d  +  ½·ρ·CdA·v²·d
```

Defaults (document them, make them constants in one file):
- `m` = 85 kg (rider + bike), `g` = 9.81
- `C_rr` = 0.005 (pavement, city tires)
- `ρ` = 1.225, `CdA` = 0.40 m² (upright commuter)
- `v_flat` = 15 km/h (4.17 m/s)

At those values, resistive force on the flat ≈ 8.3 N, so **1 m of climbing ≈ 100 m of flat riding** in energy terms. This ratio (`CLIMB_EQUIV_RATIO`, default 100) is the core tuning constant. Expose it; calibrate it in evals; do not hardcode it in five places.

### 3.2 Flat-equivalent distance (the headline metric)
```
D_eq = d_total + CLIMB_EQUIV_RATIO · ascent_total + Σ steep_penalty(segment)
```
Descent contributes **zero** recovery (you don't get the energy back, and in a city you brake). Do not credit it.

### 3.3 Steepness superlinearity
Energy-per-meter-climbed is not constant. Above roughly 6% you lose the ability to spin, above ~10% many riders stand, above ~15% many walk. Penalize accordingly:

```
steep_penalty(seg) = ascent_seg · CLIMB_EQUIV_RATIO · k(grade)

k(grade):  grade ≤ 4%   → 0.0
           4–6%          → 0.3
           6–9%          → 1.0
           9–12%         → 2.5
           12–15%        → 5.0
           > 15%         → 12.0   (treat as near-dismount)
```
These are a starting hypothesis. Milestone 6 calibrates them against the golden set. Store as a single JSON config, versioned, so evals can diff model versions.

### 3.4 The user-facing slider → engine parameters
Five detents. The slider is a **detour budget**, which is the honest way to frame it:

| Detent | Label | Copy | Detour budget vs fastest | Engine setting |
|---|---|---|---|---|
| 1 | Direct | "Get me there" | +5% | slope penalties ~off |
| 2 | Mild | "A little extra is fine" | +15% | k-table × 0.5 |
| 3 | Balanced (default) | "Trade distance for climbing" | +25% | k-table × 1.0 |
| 4 | Gentle | "I really don't want to climb" | +35% | k-table × 2.0 |
| 5 | Flattest | "Flattest possible, within reason" | +50% hard cap | k-table × 4.0 |

Detour budget is enforced **post-hoc in the ranker**, not inside the engine: reject candidates whose distance exceeds `budget × distance_of_fastest_route`. This keeps the engine dumb and the policy testable.

### 3.5 Two-stage routing architecture (important)
Do not try to make one engine call produce the perfect answer.

**Stage A — Candidate generation.** Ask the routing engine for **N=6–10 diverse candidates** (alternative routes with high path-diversity settings, plus 2–3 requests at different slope-penalty weights).

**Stage B — Re-rank.** For every candidate: resample its geometry at 10 m spacing, sample the high-resolution DEM, smooth, compute true ascent/descent/grade histogram, compute `D_eq`, apply the detour budget filter, then rank. Return the top 3 with diversity enforcement (candidates sharing >70% of geometry collapse into one).

This two-stage design is why the product can beat Apple's toggle: the ranker is where the honesty and the explanation live, and it's independently testable without a routing engine in the loop.

---

## 4. Data & elevation (the part most likely to sink this project)

### 4.1 The elevation trap — read this twice
**SRTM (~30 m) elevation is not good enough for San Francisco.** Street-scale relief in SF happens over 50–150 m distances; 30 m data smears the top of Nob Hill and can miss a 12% block entirely. A router fed SRTM will confidently produce routes over hills it can't see.

**Required:** USGS **3DEP** data for the SF bbox — 1/3 arc-second (~10 m) at minimum, 1 m lidar-derived DEM preferred where available. Build the pipeline so the DEM source is swappable and the eval harness can compare DEM versions.

### 4.2 Bounding box
```
SF city limits (with margin):  W -122.5350  S 37.7000  E -122.3500  N 37.8350
```
Reject requests with either endpoint outside this box, with a clear error (`OUT_OF_SERVICE_AREA`) and copy the client shows nicely.

### 4.3 Pipeline (all containerized, all reproducible via `make data`)
1. Download OSM extract: Geofabrik `norcal-latest.osm.pbf` → clip to bbox with `osmium extract`.
2. Download 3DEP DEM tiles for bbox → mosaic → reproject to EPSG:4326 → write **Cloud-Optimized GeoTIFF** at `data/dem/sf_dem.tif`.
3. Generate **Skadi-format** elevation tiles from the DEM for the routing engine's consumption (Valhalla consumes Skadi natively; GraphHopper has a Skadi elevation provider — **verify this against the current GraphHopper release before committing to it; if unavailable, fall back to pre-tagging `ele` on OSM nodes or implementing a custom `ElevationProvider`**).
4. Build the routing graph.
5. Emit `data/manifest.json` with source URLs, download dates, checksums, and bbox. Every eval result references a manifest hash.

The API service samples elevation from the **COG directly via rasterio**, independent of whatever the routing engine used. This intentional redundancy means Stage B's metrics are trustworthy even if the engine's internal elevation is coarse.

### 4.4 SF-specific hazards the router must handle
Encode these as explicit test cases:
- **Stairways** (`highway=steps`) — SF has hundreds. Must be excluded from bike routes by default, and never used as a "flat" shortcut.
- **Extreme grades** — Filbert St between Hyde and Leavenworth is ~31.5%. Any route including it at any slider setting is a bug.
- **Market St** — motor vehicle restrictions; verify bike access tags are respected.
- **Freeway ramps / I-80 approaches** — must never appear.
- **The Wiggle** — must be discovered (see §7).
- **Golden Gate Park / Panhandle paths** — should be preferred; verify `bicycle=designated` paths are used.
- **Cable car / streetcar tracks** — nice-to-have penalty, v2.

---

## 5. Architecture & stack

### 5.1 Repo layout (monorepo)
```
contour/
├── CLAUDE.md                  # agent operating instructions (see §9)
├── Makefile                   # data | dev | test | eval | verify | ship
├── docker-compose.yml
├── docs/
│   ├── DECISIONS.md           # append-only decision log — agent MUST append
│   └── SPEC.md                # this file, checked in
├── data/                      # gitignored except manifest.json
├── services/
│   ├── routing-engine/        # Dockerfile + config for GraphHopper (and Valhalla)
│   └── api/                   # Python FastAPI — the brain
│       ├── contour/
│       │   ├── effort.py      # §3 model. Pure functions. No I/O.
│       │   ├── elevation.py   # DEM sampling, smoothing, profile generation
│       │   ├── engines/       # graphhopper.py, valhalla.py — behind one Protocol
│       │   ├── ranker.py      # Stage B
│       │   ├── explain.py     # generates the human-readable trade sentences
│       │   └── api.py         # FastAPI routes
│       └── tests/
├── apps/
│   └── mobile/                # Expo React Native app
└── evals/
    ├── golden_routes.yaml     # §7
    ├── run_eval.py
    └── results/               # committed scorecards, one JSON per run
```

### 5.2 Technology decisions

| Layer | Choice | Rationale |
|---|---|---|
| Routing engine | **GraphHopper (self-hosted, Docker)**, custom models | `average_slope` / `max_slope` encoded values + `distance_influence` give direct, declarative control over the slope/distance tradeoff — closest fit to §3. Verify the current version's custom-model syntax from official docs before writing any model JSON. |
| Second engine | **Valhalla (self-hosted)** behind the same interface | `use_hills` gives a cheap sanity comparison; also the fallback if GraphHopper elevation integration fights back. Wire it, keep it behind a flag. |
| API service | **Python 3.12 + FastAPI + uvicorn** | rasterio/numpy/shapely make DEM sampling and profile math trivial; this is the geospatial-heavy half. |
| Map rendering | **MapLibre GL Native** via `@maplibre/maplibre-react-native` | Open, no vendor lock, styling portable. Requires an Expo **custom dev client** — Expo Go will not work. Plan for this from day one. |
| Tiles | **Protomaps `.pmtiles`, SF extract**, served from the API container | A single file for one city. Cheap, self-hosted, and the natural path to offline in v2. |
| Geocoding | Behind a `GeocoderProtocol`. Default: hosted geocoding API (Stadia Maps or similar) with a free tier | Do not build geocoding. Do not hammer public Nominatim. |
| Mobile framework | **Expo (latest SDK) + TypeScript strict + expo-router** | |
| State/data | **Zustand** (UI state) + **TanStack Query** (server state) | |
| Charts | `react-native-svg`, hand-rolled profile component | Off-the-shelf chart libs fight grade-coloring and tap-to-pan. |
| Location | `expo-location` | |
| Testing | `pytest` + `pytest-cov` (api), `vitest`/`jest` + React Native Testing Library (mobile), `maestro` (e2e smoke) | |
| Deploy | Docker Compose on a single Hetzner/Fly box | One city, one box. Do not build Kubernetes. |

**Version discipline:** the agent must pin exact versions in lockfiles and record them in `docs/DECISIONS.md`. For any library API used (GraphHopper custom-model schema, MapLibre RN props, Expo config), **fetch and read the current official docs rather than relying on memory** — several of these libraries have changed their APIs meaningfully.

---

## 6. API contract

Base: `/v1`. All distances **meters**, elevations **meters**, internally. Convert to imperial at the client edge only.

### `POST /v1/route`
```jsonc
{
  "origin":      { "lat": 37.7695, "lon": -122.4290 },
  "destination": { "lat": 37.7749, "lon": -122.4194 },
  "effort_preference": 3,          // 1..5, see §3.4
  "bike_profile": "commuter",      // commuter | road | mtb  (v1: commuter only)
  "avoid_stairs": true,            // always true in v1
  "max_alternatives": 3
}
```

**200 response**
```jsonc
{
  "request_id": "…",
  "model_version": "effort-v1.2",
  "data_manifest": "sha256:…",
  "routes": [
    {
      "id": "r1",
      "label": "Gentlest",              // Gentlest | Balanced | Fastest | Only route
      "geometry": "<encoded polyline6>",
      "distance_m": 4820,
      "duration_s": 1290,
      "ascent_m": 22,
      "descent_m": 41,
      "max_grade_pct": 4.8,
      "flat_equivalent_m": 7020,
      "effort_score": 7020,
      "grade_segments": [               // for map coloring
        { "start_idx": 0, "end_idx": 34, "grade_pct": 1.2, "class": "flat" }
      ],
      "elevation_profile": [            // resampled at 10 m
        { "dist_m": 0, "ele_m": 12.4 }
      ],
      "steep_sections": [
        { "start_dist_m": 1200, "end_dist_m": 1380,
          "grade_pct": 9.4, "street": "Duboce Ave", "ascent_m": 17 }
      ],
      "comparison_to_fastest": {
        "delta_distance_m": 640,
        "delta_duration_s": 180,
        "delta_ascent_m": -58
      },
      "explanation": "Saves 190 ft of climbing for 0.4 miles and 3 minutes more."
    }
  ],
  "warnings": [
    { "code": "UNAVOIDABLE_CLIMB",
      "message": "Every route to this destination climbs at least 210 ft.",
      "detail": { "min_ascent_m": 64 } }
  ]
}
```

**Error codes:** `OUT_OF_SERVICE_AREA`, `NO_ROUTE_FOUND`, `ORIGIN_UNSNAPPABLE`, `ENGINE_UNAVAILABLE`, `INVALID_REQUEST`. Every one needs client copy written; no raw error strings reach the UI.

### Other endpoints
- `GET /v1/geocode?q=…&limit=5` — SF-biased, returns `{name, address, lat, lon}`
- `GET /v1/reverse?lat=&lon=`
- `GET /v1/elevation/profile` — polyline in, profile out (used by client for redraws, and by evals)
- `GET /v1/health` — engine reachability, DEM readable, manifest hash, model version
- `GET /tiles/…` — pmtiles range-serving

**Performance budget:** p95 `/v1/route` ≤ 1200 ms warm, including all N candidates and re-ranking. If it exceeds this, cache candidate generation keyed on rounded coordinates (5-decimal) — do not degrade the ranker.

---

## 7. The eval harness (the fitness function — build this at Milestone 3, before polish)

`evals/golden_routes.yaml` defines named SF routes with **assertions**, not exact expected geometry (geometry will drift with OSM updates).

### 7.1 Required golden cases

```yaml
- id: the_wiggle
  description: The canonical SF flat-crossing. Non-negotiable.
  origin: { lat: 37.7695, lon: -122.4290, name: "Duboce & Market" }
  destination: { lat: 37.7738, lon: -122.4419, name: "Fell & Baker (Panhandle)" }
  effort_preference: 5
  assert:
    - ascent_m_lt: 25
    - max_grade_pct_lt: 6
    - passes_near: [ { lat: 37.7712, lon: -122.4308, radius_m: 120 },   # Sanchez/Duboce
                     { lat: 37.7723, lon: -122.4348, radius_m: 150 } ]  # Waller/Steiner area
    - distance_ratio_to_fastest_lt: 1.35

- id: marina_to_noe
  description: Cross-city with a real ridge in the way; gentlest should route around, not over, Pacific Heights.
  origin: { lat: 37.8000, lon: -122.4360, name: "Chestnut & Fillmore" }
  destination: { lat: 37.7513, lon: -122.4337, name: "24th & Castro" }
  effort_preference: 5
  assert:
    - ascent_m_lt_ratio_of_fastest: 0.6
    - max_grade_pct_lt: 9
    - excludes_bbox: [ -122.4400, 37.7880, -122.4260, 37.7960 ]  # Pacific Heights crest

- id: embarcadero_to_twin_peaks
  description: Climb is physically unavoidable. Test honesty, not avoidance.
  origin: { lat: 37.7955, lon: -122.3937 }
  destination: { lat: 37.7544, lon: -122.4477 }
  effort_preference: 5
  assert:
    - warning_present: UNAVOIDABLE_CLIMB
    - route_returned: true
    - max_grade_pct_lt: 12          # gentlest approach, not the wall

- id: filbert_wall_never
  description: Filbert St (Hyde–Leavenworth) is ~31.5%. Must never appear at any setting.
  origin: { lat: 37.8022, lon: -122.4180 }
  destination: { lat: 37.8020, lon: -122.4120 }
  effort_preference: [1, 2, 3, 4, 5]
  assert:
    - never_passes_near: [ { lat: 37.8021, lon: -122.4160, radius_m: 60 } ]
    - max_grade_pct_lt: 20

- id: no_stairs
  description: Stairways must never be routed onto.
  origin: { lat: 37.7519, lon: -122.4180 }   # Bernal Heights vicinity
  destination: { lat: 37.7420, lon: -122.4110 }
  effort_preference: 5
  assert:
    - no_way_tag: { highway: steps }

- id: soma_flat_short
  description: Flat grid — gentlest and fastest should be nearly identical. Guard against gratuitous detouring.
  origin: { lat: 37.7820, lon: -122.4050 }
  destination: { lat: 37.7760, lon: -122.3960 }
  effort_preference: 5
  assert:
    - distance_ratio_to_fastest_lt: 1.08
    - ascent_m_lt: 12

- id: gg_park_paths
  description: Should prefer designated park paths over Fulton/Lincoln arterials.
  origin: { lat: 37.7694, lon: -122.4862 }
  destination: { lat: 37.7702, lon: -122.4540 }
  effort_preference: 3
  assert:
    - prefers_way_tag: { bicycle: designated }
```

Add at least **15 more** cases covering: Sunset↔Richmond, Mission↔Dogpatch, Presidio access, Potrero Hill descent handling, Bayview flats, and three deliberate near-boundary cases just outside the bbox that must return `OUT_OF_SERVICE_AREA`.

### 7.2 Scorecard
`make eval` writes `evals/results/<timestamp>.json`:
```jsonc
{ "manifest": "sha256:…", "model_version": "effort-v1.2",
  "pass": 21, "fail": 2, "cases": [ … ],
  "aggregate": { "median_ascent_reduction_pct": 47,
                 "median_distance_penalty_pct": 14,
                 "p95_latency_ms": 940 } }
```

**Regression gates (CI blocks merge if violated):**
- `the_wiggle`, `filbert_wall_never`, `no_stairs` must **always** pass. No exceptions, no flags.
- Overall pass rate must not decrease versus the last committed scorecard.
- `median_ascent_reduction_pct` must not drop by more than 3 points.
- p95 latency must not exceed 1200 ms.

This scorecard is how the agent knows it is winning. Tune the effort model against it; do not tune by eyeballing maps.

---

## 8. Client spec

### 8.1 Screens
1. **Plan** — map (MapLibre), origin/destination fields, "use current location", long-press to drop a pin, recent destinations (local storage).
2. **Results** — bottom sheet with 3 route cards. Each card: label, climb in feet (large), distance, time, and the comparison sentence. The effort slider lives above the cards and re-requests on release (debounced 300 ms). Tapping a card highlights that polyline.
3. **Route detail** — full elevation profile (grade-colored, tappable → pans map to that block), steep-section list, warnings, "Start".
4. **Ride** — follow mode: user dot, route ahead, remaining distance + **remaining climb** (the differentiating readout), off-route detection at 50 m for 15 s → offer recompute. No voice.
5. **Settings** — units (imperial default for US), rider weight (feeds effort model), effort slider default.

### 8.2 Visual language
The elevation profile is the hero component, not the map. Grade color ramp, consistent everywhere (map polyline + profile chart + steep-section chips):

```
≤3%   #2E7D32 (green)      3–6%  #9E9D24 (olive)
6–9%  #EF6C00 (orange)     9–12% #D84315 (deep orange)
>12%  #B71C1C (red)
```
Descents render in muted blue-grey (`#546E7A`) — informative, not alarming.

Before building any UI, **read `/mnt/skills/public/frontend-design/SKILL.md`** and apply it. Avoid default-template look: this app should feel like a topographic instrument, not a generic maps clone.

### 8.3 Client rules
- All units converted at the presentation layer; the API/state stay metric.
- Never block the map on route computation — skeleton the cards.
- Offline/error states designed, not improvised.
- No `any` in TypeScript. Strict mode on.

---

## 9. Agent operating instructions (`CLAUDE.md` content)

Write these into `CLAUDE.md` at repo root and follow them.

### 9.1 Loop
For every ticket: **read spec section → write failing test → implement → run `make verify` → commit → append to `docs/DECISIONS.md` → next ticket.**

`make verify` must run: lint, typecheck, unit tests, and (from Milestone 3 onward) `make eval`. **Do not commit with `make verify` red.** Do not disable, skip, or loosen a test to make it pass — if a test is wrong, fix it in a separate commit with a written justification in `DECISIONS.md`.

### 9.2 Branch/commit discipline
- One branch per milestone: `m1-scaffold`, `m2-elevation`, …
- Conventional commits. Small commits.
- PR per milestone with a body containing: what changed, eval scorecard delta, open risks.

### 9.3 When to stop and ask (hard stops)
Stop and write `docs/BLOCKED.md`, then halt, if:
- A paid API key is required that isn't already in `.env`
- A decision would exceed **$50/month** in recurring cost
- The Wiggle test cannot be made to pass after **three** distinct approaches
- A dependency requires a license incompatible with commercial use
- Anything requires publishing to an app store, registering a domain, or making a payment

### 9.4 Guardrails
- **Never commit secrets.** `.env` is gitignored; `.env.example` is committed with empty values.
- **Never call paid APIs from tests.** Tests use fixtures in `services/api/tests/fixtures/`.
- Rate-limit all outbound data downloads; cache aggressively; never re-download an unchanged extract.
- Do not add a dependency without recording why in `DECISIONS.md`.
- Do not expand scope. If an idea is good but out of scope, write it to `docs/BACKLOG.md` and move on.

### 9.5 Verify before you trust your memory
For GraphHopper custom-model syntax, Valhalla costing options, MapLibre React Native props, and Expo config plugins: **fetch the current official documentation** before writing code against them. These APIs have changed. A confident wrong call here costs a whole milestone.

---

## 10. Milestones

Each milestone is a PR. Human checkpoints are marked 🛑.

### M1 — Scaffold & contracts
- Monorepo, Makefile, docker-compose, CI (GitHub Actions), `CLAUDE.md`, `DECISIONS.md`
- FastAPI service with `/v1/health` and `/v1/route` returning a **hardcoded fixture** matching §6 exactly
- Expo app with custom dev client, MapLibre rendering an SF basemap, hitting the fixture endpoint
- **DoD:** `make dev` brings up API + app; app displays a fake route on a real map; `make verify` green.

### M2 — Data pipeline & elevation
- `make data` downloads/clips OSM, builds 3DEP DEM COG, emits manifest
- `elevation.py`: DEM sampling, 10 m resampling, smoothing (document the smoothing window and justify it — raw DEM sampling produces phantom grade noise)
- Unit tests: sampled elevation at 10 known SF landmarks within ±3 m of published values (Twin Peaks summit, Ferry Building, Ocean Beach, Alamo Square, etc.)
- **DoD:** given any SF polyline, produce an ascent figure that a human spot-check agrees with. 🛑 **Checkpoint 1: human reviews elevation accuracy.**

### M3 — Routing engine + effort model + evals
- GraphHopper container with SF graph and elevation wired in
- Engine adapter behind a Protocol; candidate generation (N=6–10)
- `effort.py` (§3) with exhaustive unit tests — pure functions, no I/O, easy to test
- `ranker.py` (Stage B) with detour-budget filtering and diversity collapse
- Full eval harness + all golden cases from §7
- **DoD:** ≥85% of golden cases pass; **`the_wiggle`, `filbert_wall_never`, `no_stairs` pass**. 🛑 **Checkpoint 2: human rides or inspects three generated routes.**

### M4 — Real API
- Replace the fixture: real `/v1/route`, geocoding, reverse geocoding, warnings, error taxonomy
- `explain.py` generating the comparison sentences (template-based, not an LLM call — deterministic and testable)
- Caching, latency budget met
- **DoD:** p95 ≤ 1200 ms; all error codes exercised by tests.

### M5 — Client build-out
- All five screens, the profile chart component, grade coloring, follow mode, settings
- e2e smoke via Maestro: search → route → detail → start
- **DoD:** US-1…US-5 all demonstrably satisfied on a physical iPhone.

### M6 — Calibration & honesty pass
- Tune `CLIMB_EQUIV_RATIO` and the `k(grade)` table against the golden set; commit before/after scorecards
- Verify all warning paths; write final copy for every error and warning
- Accessibility: dynamic type, contrast on the grade ramp, VoiceOver labels on the profile chart
- **DoD:** ≥95% golden pass rate; scorecard committed. 🛑 **Checkpoint 3: human sign-off before TestFlight.**

### M7 — Ship-ready (do not execute without human)
- TestFlight config, icon/splash, privacy manifest (location usage strings), crash reporting
- `docs/RUNBOOK.md`: deploy, rotate keys, refresh OSM/DEM data, roll back

---

## 11. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Coarse DEM produces wrong grades | Fatal — the product is wrong, not just bad | 3DEP 10 m minimum, landmark accuracy tests in M2, human checkpoint |
| Router "discovers" flat routes through stairways or private paths | Embarrassing, unsafe | Explicit exclusion tests; `no_stairs` is a hard gate |
| Flattest routes become absurd detours | Users bounce | Detour budget enforced in ranker; `soma_flat_short` guards against gratuitous detour |
| GraphHopper elevation integration fights back | Milestone slip | Valhalla adapter behind the same Protocol; either engine can satisfy Stage A |
| Grade noise from DEM sampling creates phantom climbs | Inflated ascent numbers, lost trust | Smoothing window tuned in M2 and documented; ascent computed with a minimum-rise threshold (ignore <1 m wiggles) |
| Apple ships a better hill slider | Wedge narrows | The differentiator is the explicit trade and the tunable budget, not the toggle. Lean into transparency. |
| Scope creep into nav/social/accounts | Never ships | §2.3 is binding; ideas go to BACKLOG.md |

---

## 12. Definition of done (v1)

Contour v1 is done when, on a physical iPhone in San Francisco:
1. A rider enters a destination and sees three routes ranked by effort with climb shown in feet.
2. The Wiggle appears, unprompted, for the Duboce→Panhandle query at the Flattest setting.
3. Every route displays how much climb it saves and what that costs in distance and time.
4. Twin Peaks returns a route plus an honest unavoidable-climb warning.
5. `make verify` and `make eval` are green, with a committed scorecard at ≥95% golden pass rate.
6. No secrets in the repo, no manual steps outside `make data && make dev`.

---

## Appendix A — Reference constants (single source of truth: `services/api/contour/constants.py`)
```python
SF_BBOX = (-122.5350, 37.7000, -122.3500, 37.8350)
RIDER_MASS_KG = 85.0
GRAVITY = 9.81
C_RR = 0.005
AIR_DENSITY = 1.225
CDA_M2 = 0.40
V_FLAT_MPS = 4.17
CLIMB_EQUIV_RATIO = 100.0          # meters of flat riding per meter climbed
MIN_RISE_M = 1.0                   # ignore sub-meter noise when summing ascent
PROFILE_SAMPLE_M = 10.0
GRADE_K_TABLE = [(4, 0.0), (6, 0.3), (9, 1.0), (12, 2.5), (15, 5.0), (999, 12.0)]
DETOUR_BUDGET = {1: 1.05, 2: 1.15, 3: 1.25, 4: 1.35, 5: 1.50}
K_SCALE = {1: 0.0, 2: 0.5, 3: 1.0, 4: 2.0, 5: 4.0}
DIVERSITY_OVERLAP_MAX = 0.70
```

## Appendix B — First five tickets, verbatim
1. `M1-01` Initialize monorepo, Makefile targets (`data dev test eval verify`), CI workflow, `CLAUDE.md`, `DECISIONS.md`, `.env.example`. Verify: `make verify` green on empty test suite.
2. `M1-02` FastAPI service, `/v1/health`, `/v1/route` returning the §6 fixture. Verify: pytest asserts response validates against a committed JSON Schema derived from §6.
3. `M1-03` Expo app + custom dev client + MapLibre rendering SF with Protomaps pmtiles. Verify: e2e smoke renders map, no red screen, runs on device.
4. `M1-04` Client fetches `/v1/route`, renders fixture polyline + three static cards. Verify: RNTL test on the card list.
5. `M2-01` `make data`: osmium clip to SF bbox + 3DEP mosaic → COG + `manifest.json`. Verify: manifest checksums stable across two runs; DEM opens in rasterio; bbox assertion passes.
