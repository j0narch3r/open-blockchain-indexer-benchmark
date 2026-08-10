# Decisions

Append-only decision log. One entry per task: what was decided, why, alternatives rejected.

---

## Task 1: Monorepo scaffold, Makefile, CI

**Decided:** Used `uv` (0.8.17) with `[dependency-groups] dev = [...]` (PEP 735) in
`services/api/pyproject.toml` for dev-only dependencies (pytest, pytest-cov, ruff, mypy,
types-pyyaml), rather than `[project.optional-dependencies]`.
**Why:** This is `uv`'s current recommended mechanism for dev dependencies and keeps them out
of the published dependency metadata. No new third-party dependency was added by this choice.

**Decided:** `services/api/pyproject.toml` uses `hatchling` as the build backend
(`packages = ["contour"]`).
**Why:** Needed a PEP 517 backend so `uv sync` can install the `contour` package in editable
mode and `import contour` works from `services/api/tests/`. Hatchling is uv's documented
default recommendation and adds no extra runtime dependency (build-time only).

**Decided:** `make verify` runs, in order: `ruff check .`, `ruff format --check .`, `mypy .`,
`pytest`, each as its own `cd services/api && ...` recipe line.
**Why:** Matches the brief exactly (four checks). Running each as a separate line means `make`
aborts (non-zero exit) at the first failing check, giving a fast, unambiguous red signal — no
special error handling needed.

**Decided:** `make eval` is a no-op stub (prints "eval harness lands in Task 14", exits 0) and
is deliberately **not** invoked by `make verify`.
**Why:** Per task resolution — eval harness doesn't exist until Task 14; wiring it into verify
now would make `make verify` depend on unbuilt code. CLAUDE.md §9.1 already notes `make verify`
gains `make eval` "from Milestone 3 onward" — that wiring is a future task's job, not this one's.

**Decided:** `make data` and `make fixtures` are also stub targets in this task (they print an
informational message and exit 0) rather than shelling out to fixture/download scripts.
**Why:** The scripts they would call (`scripts/make_fixture_dem.py`, `scripts/fetch_3dep.py`,
etc.) don't exist yet — they're introduced in later tasks per the implementation plan's file
structure. Stubbing avoids inventing scope this task wasn't asked to build. `make dev` and
`make ship` are written for their eventual real commands (`uvicorn contour.api:app`, a deploy
step) even though they'll error today, since nothing exercises them as part of `make verify`.

**Decided:** `docker-compose.yml` declares `api` and `routing-engine` services with `build:`
contexts but no Dockerfiles exist yet in `services/api/` or `services/routing-engine/`.
**Why:** Task 1's file list doesn't include a Dockerfile, and per the environment notes,
docker compose is not executed in this task. The file documents the intended topology (per
SPEC §5.1) so later tasks extend a known shape instead of inventing a new one.

**Decided:** `.github/workflows/ci.yml` lives at `contour/.github/workflows/ci.yml` (not the
repo root's `.github/`), with `defaults.run.working-directory: contour` so its `make verify`
step runs from the right place.
**Why:** Per the task's explicit resolution: the brief requires this path, even though GitHub
Actions only discovers workflows under the *repository* root's `.github/workflows/`. This means
the workflow will not actually run on GitHub as committed here — it is scaffolding for a
`contour`-rooted repo (e.g. once `contour/` is extracted to its own repo), not a functioning
CI trigger in the current monorepo layout. Flagged for whoever manages the eventual repo split.

**Decided:** `contour/.gitignore` (relative to `contour/`) ignores `data/**` except
`data/manifest.json` and `data/fixtures/**`, plus `.env`, `__pycache__/`, `.venv/`,
`node_modules/`, and `.superpowers/`, per the brief verbatim.
**Why:** As specified. Note `docs/superpowers/` (design doc + plan, already present) is a
different path and is **not** matched by the `.superpowers/` ignore pattern — it stays tracked,
per resolution to leave it alone.

**Alternatives rejected:** Wiring `make eval` into `make verify` now (rejected — eval harness
doesn't exist, would break the gate for every task before Task 14). Using `pip`/`venv` instead
of `uv` (rejected — task environment notes specify `uv`). Putting dev deps in
`[project.optional-dependencies]` (rejected — `uv`'s dependency-groups is the more current,
narrower-scoped mechanism and keeps dev tooling out of the installable package's metadata).

---

## Task 2: Constants, core types, versioned effort model

**Decided:** `constants.py` is the single authoritative Python-side source of truth for the
k-table, k-scale, and detour budget. `contour/models/effort-v1.json` is a second, independent
copy (required so the effort model is versioned and diffable per SPEC.md §3.3/§7). A test
(`tests/test_types.py::test_effort_v1_json_agrees_with_constants` and
`test_load_effort_model_default_version`) reads the JSON directly and via `load_effort_model()`
and asserts both agree with `constants.py`, keyed to the "effort-v1" version specifically.
**Why:** Two sources of truth silently diverging is the exact failure mode the task brief warns
about; a corrupted route metric from a stale JSON copy would be very hard to notice downstream.
`load_effort_model()` itself does **not** enforce this agreement (it just parses whatever JSON
is on disk) — future model versions (e.g. `effort-v2.json` from Milestone 6 calibration) are
*expected* to diverge from the v1 defaults, so baking an equality check into the loader would be
wrong. The agreement check is a test pinned to "effort-v1" only.
**Verified live:** the guard was proven, not just written — I temporarily changed
`detour_budget["3"]` in `effort-v1.json` from `1.25` to `1.99`, reran `pytest tests/test_types.py`,
confirmed both agreement-related tests failed with a clear diff, then restored the file byte-for-
byte (`diff` confirmed identical) and reran the full suite green.

**Decided:** `load_effort_model(version: str = "effort-v1") -> EffortModel` lives in
`contour/types.py`, not a new `contour/models.py`.
**Why:** The task-2 brief's Step 6 requires a `contour/models/` *directory* holding
`effort-v1.json` (and future `effort-vN.json` files). A same-named `contour/models.py` module
next to a `contour/models/` package directory is a real Python footgun — depending on import
order and packaging, one can shadow or collide with the other. `types.py` already needed to
import nothing beyond `json`/`pathlib`/stdlib to load the model, so keeping the loader there
avoids the collision entirely without adding a file. **Flagged for Task 11**, whose brief
resolution mentions `effort.py`'s eventual home for this loader "if that file exists" — it does
not yet; when Task 3 creates `effort.py`, it may choose to re-export `load_effort_model` from
there for API ergonomics, but the implementation should stay in `types.py` to avoid
`effort.py` (pure functions per design doc §4.1) importing I/O.

**Decided:** `EffortModel.k_scale` and `EffortModel.detour_budget` are typed `Mapping[int, float]`
(per task resolution #3) but constructed from `types.MappingProxyType` at both `constants.py` and
`load_effort_model()`, rather than plain `dict`.
**Why:** `Mapping` is the read-only *view* type mypy strict expects for immutable collections
(resolution #5); `MappingProxyType` is the concrete read-only *value* — using it means the
"frozen" contract of these dataclasses is real at runtime, not just a mypy-time fiction (a plain
`dict` assigned to a `Mapping`-typed frozen dataclass field is still mutable via the original
reference). No new dependency — `types.MappingProxyType` is stdlib.

**Decided:** `ContourError.__init__(self, code: str, message: str, detail: Mapping[str, Any] | None
= None) -> None` on the base class; each subclass (`OutOfServiceArea`, `NoRouteFound`,
`OriginUnsnappable`, `EngineUnavailable`, `InvalidRequest`) overrides `__init__(self, message: str,
detail: ... = None) -> None` and hardcodes its `code` in the `super().__init__()` call — subclass
constructors do not accept `code` as a parameter.
**Why:** The Interfaces line's signature `ContourError(code, message, detail)` describes the base
class; per Step 5, subclasses must "hardcod[e] its exact code string" rather than accept it as an
argument, so a call site can never accidentally pass the wrong code string for
`OutOfServiceArea`, etc. `detail` defaults to `None` (normalized to `{}` internally) purely for
caller convenience; the brief's literal `detail: dict` is not optional in spirit, only in the
constructor's ergonomics.

**Decided:** `GradeSegment.klass: str` (not `class`), exactly per task resolution #1. Comment in
`types.py` explains why; no serialization logic (e.g. a pydantic `alias="class"`) is added in
this task — that's explicitly deferred to whichever later task adds the pydantic API-response
boundary.
**Why:** Matches the resolution verbatim; adding serialization logic now would be scope creep
into a module (`api.py` / pydantic schemas) this task does not own.

**Decided:** No new third-party dependency was added. `errors.py`, `types.py`, and `constants.py`
use only the stdlib (`dataclasses`, `json`, `pathlib`, `types.MappingProxyType`,
`collections.abc.Mapping`, `typing.Final`/`Any`).
**Why:** Nothing in Task 2's scope needs pydantic, JSON Schema validation, or any parsing library
beyond `json.loads`; the brief's own "validates it against the constants" requirement is met by a
test, not a runtime schema validator, so pulling in e.g. `jsonschema` was unnecessary and would
have needed its own `DECISIONS.md` justification for zero benefit.

**Alternatives rejected:** Making `load_effort_model()` raise if the loaded JSON disagrees with
`constants.py` (rejected — would break by design once a genuinely different `effort-v2.json`
ships; the agreement check belongs in a test pinned to v1, not in the loader). A `contour/models.py`
module for the loader (rejected — name collision with the `contour/models/` data directory
required by Step 6). Encoding `k_scale`/`detour_budget` JSON keys as integers directly (rejected —
JSON object keys must be strings per the JSON spec; the loader converts `"1"` → `1` on load, and
the agreement test does the same conversion before comparing to `constants.py`). Adding
`GRADE_COLORS` / grade-class-threshold constants to `constants.py` speculatively for later tasks
(rejected — not in the Step 3 list of exact values to add, and the grade color ramp is
client-side per design doc §6 (`theme/grade.ts`); out of this task's scope per Global
Constraints' "do not expand scope").

---

## Task 2, fix round 1: real immutability + structural model validation

**Decided:** Every collection-typed field in `types.py`'s dataclasses is now `tuple[X, ...]`
(never `list`) or `tuple[Mapping[str, str], ...]` (never `list[dict[str, str]]`), matching
design doc §4.2 as corrected in commit `d7ba35b`: `RouteCandidate.geometry`,
`RouteCandidate.way_tags`, `ElevationProfile.points`, `ScoredRoute.grade_segments`,
`ScoredRoute.steep_sections`. `SegmentStat` has no collection fields and needed no change;
`EffortModel.k_table`/`k_scale`/`detour_budget` were already `tuple`/`Mapping` from Task 2 and
needed no change either.
**Why:** `@dataclass(frozen=True)` blocks attribute *rebinding* only — a `list` field stays
mutable in place, so a downstream module could reorder or append to a candidate's geometry after
another module had already scored it, and nothing would raise. These objects cross the Stage
A / Stage B / API module boundaries the design doc §4.1 dependency diagram describes, so the
immutability has to be real, not just documented intent. Caught by Task 2's own review; the
design doc itself was wrong (it specified `list[...]`) and has been fixed upstream rather than
worked around here.
**Verified:** added `tuple.append(...)` (expect `AttributeError`) and `tuple[0] = ...` (expect
`TypeError`) assertions for every affected field
(`test_route_candidate_collections_reject_in_place_mutation`, and inline in
`test_elevation_profile_is_frozen_and_has_exact_fields` /
`test_scored_route_is_frozen_and_composes_the_others`). Python does not enforce dataclass field
type annotations at runtime, so these tests exercise the actual tuple values the codebase is
expected to construct (per §4.2's corrected type hints), not the annotation itself — that is the
limit of what a frozen dataclass with no `__post_init__` isinstance check can guarantee, and
adding such a check was judged unnecessary scope beyond what the finding asked for.

**Decided:** `load_effort_model()` now calls a new `validate_effort_model(model: EffortModel) ->
None` before returning, which raises `ValueError` (naming the offending field) if: `model_version`
is empty; `climb_equiv_ratio <= 0`; `k_table` is empty or not sorted strictly ascending by grade;
`k_scale` or `detour_budget` do not have exactly the keys `{1,2,3,4,5}`; or any `detour_budget`
value is `< 1.0`. `validate_effort_model` is exported from `types.py` so it can be driven directly
against malformed in-memory `EffortModel` instances in tests, independent of the filesystem.
**Why:** The task-2 brief's Step 6 asked for "a loader that validates it against the constants";
Task 2's original reasoning — that baking v1-value-equality into the loader would break by design
once a genuinely different `effort-v2.json` ships — was judged correct and is preserved. The fix
resolves the tension by validating *structural* invariants that must hold for any model version
(shape, sign, monotonicity, key completeness) in the loader itself, while leaving *value*-equality
against `constants.py` to the version-pinned test added in the original Task 2 commit. A budget
`< 1.0` is specifically guarded because the ranker's budget filter (design doc §4.6 step 3) drops
candidates whose distance exceeds `budget × fastest.distance_m` — a sub-1.0 budget would reject the
fastest route against itself, which no valid model should ever specify.
**Verified:** one test per invariant (`test_validate_effort_model_rejects_*`), each constructing a
`_valid_model()` baseline via `dataclasses.replace` and violating exactly one field, asserting
`pytest.raises(ValueError, match=<field name>)`; plus
`test_validate_effort_model_accepts_a_valid_model` proving the valid case does not raise. All nine
tests pass; `load_effort_model()` on the committed `effort-v1.json` continues to pass validation
(`test_load_effort_model_default_version` still green), confirming the new check doesn't reject
the real model.

**Alternatives rejected:** Adding a `__post_init__` `isinstance` check to every frozen dataclass to
reject `list` arguments at construction time (rejected — not what either finding asked for, and
would add runtime overhead + boilerplate to every dataclass in the module for a guarantee the type
system already communicates via the corrected §4.2 annotations; flagged here in case a future task
wants stricter enforcement). Checking model-version equality to `constants.py` inside
`validate_effort_model` (rejected — explicitly the thing fix round 1 says not to undo; would make
`effort-v2.json` unloadable by construction).

---

## Task 3: FastAPI service, `/v1/health`, schema-validated fixture `/v1/route`

**Decided:** `tests/schema/route_response.schema.json` (JSON Schema draft 2020-12) was hand-derived
from SPEC.md §6 field-for-field, with `additionalProperties: false` at every object level (top
level, `route`, `grade_segment`, `profile_point`, `steep_section`, `comparison_to_fastest`,
`warning`). `contour/schemas.py` (pydantic v2, `extra="forbid"` everywhere) is a second,
independent encoding of the same contract; `tests/test_api_fixture.py::test_committed_fixture_file_validates_against_schema`
and `test_route_returns_schema_valid_fixture` prove the pydantic models, the hand-written JSON
Schema, and the committed fixture all agree.
**Why:** Per the brief, this schema is the binding contract for Task 15 (real routing, must not
change the shape) and Task 18 (TypeScript codegen for the mobile client) — deriving it by hand
from §6 rather than auto-generating it from the pydantic models keeps it as an independent check
on the models, not a restatement of whatever they happen to say. `additionalProperties: false`
was chosen deliberately strict: a stray/renamed field is exactly the kind of drift Task 15 could
introduce by accident while wiring in real data, and the schema should catch it immediately
rather than silently accept extra keys.

**Decided:** `contour.schemas.GradeSegmentOut` keeps the Python attribute name `klass` (matching
`contour.types.GradeSegment`) but declares `Field(alias="class")`, with
`model_config = ConfigDict(populate_by_name=True, extra="forbid")` on every response model that
uses aliasing. FastAPI's default `response_model_by_alias=True` then serializes the wire field as
`"class"`. `test_route_response_emits_class_not_klass` asserts the emitted JSON has `"class"` and
not `"klass"` on every `grade_segments[]` entry, calling the real `/v1/route` endpoint through
`TestClient` rather than testing the pydantic model in isolation.
**Why:** Exactly the task's resolution #2 and its stated rationale — this is "the kind of thing
that silently ships wrong" if only unit-tested against the model directly; testing through the
actual HTTP response closes the gap between "the model can serialize correctly" and "the endpoint
actually does."

**Decided:** `RouteRequest.units: Literal["imperial", "metric"] = "imperial"` was added per task
resolution #1. It is request-only — no response field reads it in this task, since the fixture's
`explanation` string is static. `RouteOut`, `elevation_profile`, `steep_sections`, etc. all stay
strictly metric per SPEC.md §8.3, matching the resolution's "only the rendered `explanation`
string honours it" instruction; Task 15's real `explain.py` is the one that will actually branch
on `units` when generating that string.
**Why:** Matches the resolution verbatim; recorded here rather than re-litigated, per the task's
explicit instruction.

**Decided:** `GET /v1/health` computes `status` dynamically — `"ok"` iff both
`engine_reachable and dem_readable` are truthy, else `"degraded"` — rather than hardcoding `"ok"`.
`engine_reachable = False` and `dem_readable = None` are returned honestly (nothing in this task
wires up a routing engine or DEM reader); `manifest_hash = None` for the same reason (no
`data/manifest.json` exists yet — `make data`/`make fixtures` are still stubs per Task 1).
`model_version` comes from the real `load_effort_model().model_version` (`"effort-v1"`), not the
SPEC.md §6 example's illustrative `"effort-v1.2"` string, since that version file doesn't exist.
**Why:** Task resolution #5 — "do not fake them true." Computing `status` from the two honest
booleans (rather than a third independently-hardcoded field) means it can't silently drift from
them once Task 15 wires the engine/DEM in for real; the health check would flip to `"ok"`
automatically the moment both dependencies genuinely become reachable, with no code change needed
here.

**Decided:** The committed fixture (`tests/fixtures/route_fixture.json`) uses the exact
origin/destination SPEC.md §6 already specifies in its own worked example — Duboce & Market
(37.7695, -122.4290) to 37.7749, -122.4194 — and reuses that example's numeric values
(`distance_m: 4820`, `ascent_m: 22`, `descent_m: 41`, etc.) and its `UNAVOIDABLE_CLIMB` warning
verbatim, including `detail.min_ascent_m: 64`, which matches `constants.UNAVOIDABLE_CLIMB_M`
exactly. `geometry` is a real (not placeholder) polyline6-encoded string, hand-encoded from a
plausible sequence of coordinates along that corridor; `elevation_profile`'s start elevation
(17.0 m) matches the design doc's committed Duboce & Market control point rather than the SPEC
example's illustrative `12.4`. The fixture contains exactly one route (`label: "Gentlest"`), not
three, since nothing in this task ranks alternatives — Task 15 is what produces genuinely
distinct Gentlest/Balanced/Fastest candidates.
**Why:** Task resolution #4 ("a realistic SF route... the §6 example uses Duboce & Market to a
nearby destination") plus "It must validate against your own schema; that is the point of the
task" — reusing SPEC's own worked numbers keeps the fixture traceably tied to the authoritative
example rather than inventing parallel data, while the real polyline encoding and the
design-doc-consistent elevation value keep it honestly "realistic" instead of a schema-shaped
stub.

**Decided:** The `ContourError` → HTTP status mapping (`OUT_OF_SERVICE_AREA` / `INVALID_REQUEST` /
`NO_ROUTE_FOUND` / `ORIGIN_UNSNAPPABLE` → 422, `ENGINE_UNAVAILABLE` → 503) lives in a single
`_ERROR_STATUS` dict in `api.py`, consumed by one `@app.exception_handler(ContourError)` that
returns the flat `{"code", "message", "detail"}` body. A second handler,
`@app.exception_handler(RequestValidationError)`, gives FastAPI's own body-validation failures
(e.g. `effort_preference` out of `1..5`) the same flat shape, tagged `INVALID_REQUEST`, instead of
FastAPI's default `{"detail": [...]}` envelope.
**Why:** Task resolution #3 covers `ContourError` explicitly; the `RequestValidationError` handler
is an extension of the same requirement — the brief's stated goal ("no raw error strings reach the
UI", "not nested under FastAPI's default `detail` envelope") is a property of every non-2xx
response this service returns, and a malformed request body is the single most likely way a client
hits a non-`ContourError` failure. Leaving FastAPI's default envelope in place for that one path
would mean the mobile client (Task 18) has to special-case it. Verified live via
`test_route_invalid_effort_preference_rejected_with_flat_body` and by calling the endpoint
directly and printing the body during self-review, not just asserting `status_code`.

**Decided:** Added `jsonschema` (dev-only) and its stub package `types-jsonschema` (dev-only) to
`[dependency-groups] dev` in `services/api/pyproject.toml`.
**Why:** Explicitly authorized by the task ("Add `jsonschema` as a dev dependency... record it in
`docs/DECISIONS.md`"); needed to run `jsonschema.validate(...)` against the hand-derived schema in
tests. `types-jsonschema` was needed separately because `mypy --strict` rejects untyped imports
(`import-untyped`) without it; not a new runtime dependency, dev/type-checking only.

**Decided:** Added a `[tool.pytest.ini_options] filterwarnings` entry ignoring
`starlette.exceptions.StarletteDeprecationWarning`.
**Why:** `starlette` 1.6.0's `TestClient` emits a deprecation warning at import time nagging to
install a very new, separately-named `httpx2` package in place of `httpx` (which is already a
pinned `services/api` dependency, used nowhere near its deprecation surface here). Adopting
`httpx2` — an unfamiliar, newly-published package — mid-task to silence a notice, without the
usual scrutiny a new dependency deserves, was judged worse than a one-line, narrowly-scoped
`filterwarnings` entry; this is not a new dependency and doesn't touch `httpx` itself. Flagged here
for whoever eventually evaluates `httpx2` deliberately, rather than by default. Verified `pytest`
now runs with zero warnings (`42 passed` with no `warnings summary` section).

**Alternatives rejected:** Auto-generating `route_response.schema.json` from the pydantic models
via `model_json_schema()` (rejected — would make the "contract" merely a mirror of whatever the
models say, defeating the purpose of an independent hand-derived check; the brief's Step 1 says
"by hand" explicitly). Loading the fixture from `contour/tests/fixtures/` inside the package
(rejected — task resolution #4's exact path is `services/api/tests/fixtures/route_fixture.json`;
`api.py` resolves it via `Path(__file__).resolve().parents[1]`, i.e. it deliberately reaches
*out* of the package into the test tree, which is unusual but is what the resolution asks for —
Task 15 is expected to change this when the fixture stops being the response source). Hardcoding
`GET /v1/health`'s `status` to `"ok"` (rejected outright — task resolution #5 forbids faking
`engine_reachable`/`dem_readable` true, and a hardcoded-`"ok"` status next to two honest `false`
fields would be misleading in the same spirit even though the resolution doesn't literally mention
`status`).

---

## Task 3, fix round 1: three-route fixture; fixture as package data

Coordinator-directed corrections to two of Task 3's own resolutions (plan updated at `ef3d8d0`).

**Decided:** The committed fixture (now `contour/fixtures/route_fixture.json`, see below) has
**three** routes — `id: r1/r2/r3`, `label: Gentlest/Balanced/Fastest` — instead of one. Values are
mutually consistent by construction: `ascent_m` (22 / 48 / 80), `distance_m` (4820 / 4460 / 4180),
`flat_equivalent_m` (7020 / 10260 / 15930), and `effort_score` (same as `flat_equivalent_m`) all
strictly order Gentlest < Balanced < Fastest, i.e. Gentlest is cheapest in effort and longest in
distance, Fastest the reverse. `comparison_to_fastest` on every route (including Fastest's own,
all-zero) is computed as `route.value - fastest.value` for `distance_m`/`duration_s`/`ascent_m`,
and `explanation` is a plain-English rendering of that same delta (Gentlest keeps the original
"Saves 190 ft... 0.4 miles... 3 minutes more." from the SPEC.md §6 worked example; Balanced is a
freshly-computed "Saves 105 ft of climbing for 0.2 miles and 1 minute more."; Fastest, having
nothing to compare against itself, describes the route instead of a saving). `grade_segments`'
steepest listed segment equals each route's `max_grade_pct` exactly (Gentlest 4.8 / Balanced 6.9 /
Fastest 9.4, with grade classes `gentle`/`moderate`/`steep` matching the Global Constraints ramp),
and each route's `elevation_profile` was constructed leg-by-leg so summed positive/negative
elevation deltas equal its stated `ascent_m`/`descent_m` (not hysteresis-adjusted — no code in
this task computes ascent via the peak/valley algorithm design doc §4.4 describes; `effort.py`
doesn't exist yet — so "coherent" here means the simple sum of rises/falls matches, which is what
a naive chart-from-points client would show). `steep_sections` only exist on Balanced and Fastest
(Gentlest's max grade of 4.8% is "gentle," not locally steep, so an empty list is the honest
answer, not a placeholder). The `UNAVOIDABLE_CLIMB` warning's `detail.min_ascent_m` was corrected
from the SPEC.md §6 example's `64` to `22` (Gentlest's own ascent, the true minimum across the
three returned routes) — the original single-route fixture's `64` was left over from copying the
SPEC example verbatim and was never true of the data it sat next to; the message text now says "72
ft" (`22 m × 3.28084`) to match.
**Why:** Coordinator finding — Appendix B ticket M1-04 ("renders fixture polyline + three static
cards") and SPEC.md §2.2 ("three ranked alternatives... is the MVP's core interaction") establish
that one route cannot exercise what M1 exists to prove, and Task 20's client tests need three
cards to render. The internal-consistency work (matching `comparison_to_fastest` arithmetic,
`grade_segments`/`max_grade_pct` agreement, `elevation_profile` summing to `ascent_m`/`descent_m`,
and fixing the stale `min_ascent_m: 64`) goes beyond "just add two more routes" because a
three-route fixture that merely satisfies the JSON Schema but contradicts itself internally (e.g.
a `steep_sections` entry steeper than the route's own `max_grade_pct`, which the original
single-route fixture actually had — `9.4` inside a `max_grade_pct: 4.8` route) is exactly the kind
of "noise" a real client-rendering test would surface as a bug report against Task 20, not Task 3.
**Verified:** `test_fixture_has_exactly_three_routes_with_expected_labels`,
`test_gentlest_and_fastest_are_the_extremes`, `test_comparison_to_fastest_is_arithmetically_correct`
(computes the expected delta independently and compares, plus asserts Fastest's own comparison is
all zeros), `test_grade_segments_max_matches_route_max_grade_pct`, and
`test_elevation_profile_rise_and_fall_match_ascent_and_descent` (sums signed deltas and checks
against `ascent_m`/`descent_m` within `pytest.approx(..., abs=0.5)`) — all five new, all green,
all reading the committed fixture data itself (not a copy), so they fail immediately if a future
hand-edit reintroduces drift.

**Decided:** The fixture moved from `services/api/tests/fixtures/route_fixture.json` to
`services/api/contour/fixtures/route_fixture.json` — inside the installable package — and `api.py`
now loads it via `importlib.resources.files("contour") / "fixtures" / "route_fixture.json"`
(module-level `FIXTURE_RESOURCE`, exported) instead of a `Path(__file__).resolve().parents[1] /
"tests" / ...` filesystem path. `tests/conftest.py` gained a `fixture_data` pytest fixture that
reads the *same* `contour.api.FIXTURE_RESOURCE`, and every test that previously read the fixture
off a separate `tests/fixtures/` copy now uses that fixture — there is exactly one copy of
`route_fixture.json` in the repository. The old `services/api/tests/fixtures/` directory was
removed (now empty).
**Why:** Coordinator finding — production code (`api.py`) reading from the test tree works only
in an editable install and breaks under a built wheel or any real install, since `tests/` is never
packaged. `importlib.resources` is the standard-library-correct way to read package data
regardless of how the package is distributed (source tree, wheel, zipped egg); a path relative to
`__file__` shares the same "breaks when zipped" failure mode `importlib.resources` exists to
avoid. Pointing tests at the identical resource (rather than duplicating the JSON) means the two
copies this task started with (and the coordinator's fix explicitly called out as bad — "one
copy, not two that can drift") no longer exist as two copies to begin with.
**Verified, not just asserted:** built an actual wheel (`uv build --wheel`) and listed its
contents — `contour/fixtures/route_fixture.json` (4162 bytes) is present, packaged automatically
by hatchling's `packages = ["contour"]` wheel target with **no config changes needed**. Then
installed that wheel into a throwaway venv (no editable install, no source tree on `sys.path`) and
ran `importlib.resources.files("contour") / "fixtures" / "route_fixture.json"` against it
directly — it read back the three route labels correctly, proving the resource resolves under a
real install, not just `uv run` from the repo.
**Checked, per the coordinator's explicit ask, whether the same packaging gap applies to
`contour/models/*.json`:** it does not. The same `uv build --wheel` run also packaged
`contour/models/effort-v1.json` (362 bytes) with zero extra configuration — Task 2's implementer's
"packaging was unverified" flag (`DECISIONS.md`, Task 2 entry) turns out to have been unfounded:
hatchling's `packages = ["contour"]` wheel mode includes every file under the package directory,
code or data, by default, with no `[tool.hatch.build.targets.wheel.force-include]` or artifacts
config required. No packaging config was changed for either file — there was no gap to close.

**Alternatives rejected:** Keeping the fixture in `tests/` and having `api.py` read it via
`importlib.resources` anyway by adding `tests/` as a namespace package (rejected — would still
leave test data as the literal source of a production response, just with extra indirection; the
coordinator's ask was to move the data, not just change how it's read). Generating the three
routes' numbers from an actual (if simplified) physics/ranking calculation instead of hand-picked
mutually-consistent constants (rejected — that calculation is `effort.py`/`ranker.py`'s job
(Task 11/Task 13), which do not exist yet; hand-picking numbers that satisfy the same *invariants*
those modules will eventually enforce is the correct scope for a fixture task).

---

## Task 4: Fixture DEM generator and data manifest

**Decided:** `data/fixtures/elevation_control_points.csv` is the prior agent's validated draft
(`elevation_control_points.draft.csv`), copied in verbatim — 115 rows, 105 `control` / 10
`holdout`, columns `name,lat,lon,ele_m,role,source,confidence`. This task did not re-author or
edit any row.
**Why:** Per the task's explicit instruction ("do not invent your own points"); the file was
already validated (bbox membership, elevation range, exactly 10 holdouts, no duplicate
coordinates — reconfirmed here by `load_control_points()`'s own structural checks, which the
committed file passes as-is).

**Decided:** `scripts/make_fixture_dem.py` (outside `services/api`, per design doc §8's repo
layout) does the following, in order: (1) `load_control_points()` reads and structurally
validates the CSV — bbox membership, `ele_m` in `[0, 290]`, exactly 10 holdout rows, no duplicate
`(lat, lon)` pairs — raising `ControlPointValidationError` naming the first violation rather than
repairing anything; (2) `build_dem()` filters to `role == "control"` rows only before calling
`interpolate_dem()`; (3) `interpolate_dem()` projects control points and the output grid to a
local equirectangular metre frame (see below) and fits
`scipy.interpolate.RBFInterpolator(kernel="thin_plate_spline", smoothing=0.5)` — the brief's exact
parameters, unchanged; (4) the raw interpolated surface is clamped to `[0, 300]` m, with
low/high clamp counts tracked separately; (5) `write_cog()` writes the array via GDAL's `COG`
driver (tiled 512x512, `deflate`, internal overviews); (6) `write_manifest()` emits
`data/manifest.json`.
**Why:** Matches brief Steps 1-5, adjusted by the task's five resolutions (below).

**Decided:** Control points and the output grid are projected from (lon, lat) degrees to a local
equirectangular metre frame — `x_m = (lon - west) * meters_per_degree_lon`,
`y_m = (lat - south) * meters_per_degree_lat`, with `meters_per_degree_lon` scaled by
`cos(mean_lat)` — before being handed to `RBFInterpolator`, rather than fitting directly in raw
(lon, lat) degree-space.
**Why:** A degree of longitude at SF's latitude (~37.77°N) is about 21% shorter in metres than a
degree of latitude (`cos(37.77°) ≈ 0.79`). Fitting the RBF directly in degree-space would make its
implicit Euclidean distance metric anisotropic — the interpolator's smoothing radius would reach
~26% farther east-west than the same "distance" reaches north-south, with no physical
justification. Projecting to metres first makes "smooth" mean the same thing in both directions,
which matters directly for whether the Wiggle corridor comes out as a genuine saddle rather than
an accidentally lopsided one. No new dependency — plain `numpy`/`math`.
**Verified:** the row/column axis convention (row 0 = north edge, column 0 = west edge, matching
both the grid's own coordinate generation and the `Affine` transform passed to `write_cog`) is
spelled out in an inline comment in `interpolate_dem()` specifically because a flipped axis here
is the exact silent-failure mode task-4's resolution #4 warns about — it would pass every
structural test (CRS, dtype, bounds, no-nodata, COG layout) and still produce a mirrored city. The
four-point sanity check (below) is what actually catches it.

**Decided (resolution #1, determinism):** `build_dem()` returns a `DemBuildResult` dataclass
carrying both `file_sha256` (the written `.tif`'s raw bytes) and `data_sha256` (the raw
`float32` array's bytes, independent of any GeoTIFF header/tag/overview encoding). Both are
asserted equal across two separate `build_dem()` calls in
`test_fixture_dem_is_deterministic`. No RNG is used anywhere in the module.
**Why:** Per the task's resolution — "if two runs differ, find out why and fix the cause; do not
paper over it by excluding bytes from the hash." Hashing the array separately from the file
proves the *interpolation* is deterministic even if some future writer change made file bytes
vary (e.g. a different GDAL build embedding a timestamp) — that would show up as
`data_sha256` still matching while `file_sha256` diverges, which is a more diagnostic signal than
one hash going right or wrong together.
**Verified live:** built two files 1.1 s apart with a throwaway script before writing any
production code — byte-identical (`sha256` equal) despite the wall-clock gap, confirming GDAL's
`COG` driver embeds no timestamp here. `make fixtures` was then run twice in this task and
produced identical `dem_data_sha256`/`dem_file_sha256` both times (only `generated_at` and the
derived `manifest_sha256` differed, as expected — `generated_at` is real wall-clock time and is
the one deliberately time-varying field in the manifest).

**Decided (resolution #2, nodata):** `nodata=-9999` is set as raster metadata, but the RBF surface
is evaluated over every pixel of the grid (a global interpolant defined everywhere), so no pixel
is ever *written* as nodata. `test_fixture_dem_has_zero_nodata_pixels` asserts
`(arr == NODATA).sum() == 0` against the real committed DEM, not just a claim.
**Why:** Exactly the resolution's ask — a DEM with holes would make routes silently unscoreable.

**Decided (resolution #3, clamping — reported, not just applied):** `ClampStats` tracks
`clamped_low`/`clamped_high`/`total_pixels` separately and `main()` prints both the count and the
percentage. On the committed `data/dem/sf_fixture_dem.tif`: **1,004,086 of 2,446,884 pixels
(41.0%) were clamped** — 998,262 low (below 0 m), 5,824 high (above 300 m).
**Why this number is what it is, investigated rather than waved off:** a `Delaunay`-based
convex-hull check (run as a one-off diagnostic, not committed) shows only 39.6% of the grid falls
inside the convex hull of the 105 control points at all — `SF_BBOX` includes large stretches of
open SF Bay, Pacific Ocean, and bbox margin beyond the peninsula that intentionally have zero
control points (there is no SF elevation to put there). Of the pixels *outside* the hull, 63.2%
were clamped (nearly all low — the TPS trends toward negative/underwater far from any land point,
which clamps to sea level, a physically reasonable answer for water even though it is
extrapolation, not interpolation). Of the pixels *inside* the hull — nominally "should have real
land data nearby" — 7.2% (64,686 of 970,057) were still clamped (64,410 low, 5,824+ high),
consistent with `smoothing=0.5` letting the surface swing past `[0, 300]` between sparse or
conflicting nearby points rather than being pinned exactly at them.
**Consequence flagged for Task 5:** sampling the 10 held-out landmarks against the committed DEM
gives errors of 33.5 m (Twin Peaks summit) and 21.1 m (Bernal Heights summit) — both outside the
design doc §2.3's stated ±12 m widened tolerance — while the other 8 holdouts are within 12 m
(several within 1 m). This is `smoothing=0.5` (the brief's specified value, kept as-is — not this
task's parameter to change) systematically flattening sharp, relatively isolated summits toward
their (lower) surrounding points. Recorded here rather than fixed unilaterally because Step 4 of
the brief fixes `smoothing=0.5` explicitly and is not listed among this task's five ambiguity
resolutions; Task 5 (or a follow-up) will need to either widen the summit tolerance further,
special-case peak holdouts, or revisit the smoothing parameter with its own justification.
**Not fixed by adding more control points:** this task's brief says not to re-author the control
point table; adding water-boundary anchor points to tame the extrapolation would be exactly that.

**Decided (resolution #5, COG validity):** Used GDAL's native `COG` driver
(`rasterio.open(..., driver="COG", blocksize=512, compress="deflate", overview_resampling="average")`)
rather than adding the `rio-cogeo` package. Verified structurally, not just by assuming the flag
name is enough: `ds.profile["tiled"] is True`, `ds.block_shapes == [(512, 512)]`,
`ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"`, and `len(ds.overviews(1)) > 0` are all asserted
in `test_fixture_dem_is_a_valid_cog_structure` against the real committed file.
**Why:** `rio-cogeo` is not installed in this environment and is not already a project dependency;
its main value over the raw `COG` driver is a standalone *validator* CLI/function
(`cog_validate`), but GDAL's `COG` driver is itself the reference implementation the COG spec is
built around (correct tile layout, overviews written before the full-resolution IFD, correct
ghost-area header) — using it to *write* the file already guarantees the structure `rio-cogeo`
would otherwise be checking for. Confirmed the driver is present in this environment's GDAL build
(3.12.4) before relying on it, rather than assuming. Adding a new dependency whose only benefit
here would be re-deriving a guarantee the writer already provides was judged not worth the
`docs/DECISIONS.md` justification it would need under "do not add a dependency without recording
the reason."

**Decided (resolution #4, shape sanity check):** `sample_and_report_sanity()` in the script (and a
matching parametrized test) samples the finished DEM at Twin Peaks (37.7559, -122.4476), SoMa /
5th & Folsom (37.7800, -122.4050), Ocean Beach (37.7609, -122.5105), and Duboce & Market (37.7695,
-122.4290). Actual sampled values on the committed DEM: Twin Peaks 247.5 m (expected 230-290),
SoMa 10.1 m (expected 0-20), Ocean Beach 3.8 m (expected 0-15), Duboce & Market 30.4 m. The
Duboce & Market upper bound was widened from the resolution's literal "roughly 30" to 35 m in both
the script's own reporting and the test, since the nearest control points to that exact coordinate
range from 24 m (Panhandle) to 40 m (Church & Market) with the 30 m Duboce/Market point closest —
30.4 m is the RBF smoothing that point slightly toward its higher neighbor, not a sign of a
flipped axis or wrong data (a real flipped-axis bug would land at 150+ m or below sea level here,
not 0.4 m over a rounded verbal guideline).
**Why:** Exactly the resolution's ask — this is the check that would catch a mis-signed longitude
or transposed row/column axis, which passes every other test in this suite.

**Decided:** `resolution_m=10.0` grid dimensions are computed from `SF_BBOX`, not hardcoded —
`width = round((east - west) / dx_deg)`, `height = round((north - south) / dy_deg)`, where
`dx_deg`/`dy_deg` come from the same equirectangular metre conversion above. On `SF_BBOX` this
yields a 1628 x 1503 grid (2,446,884 pixels).
**Why:** `SF_BBOX` is `constants.py`'s single source of truth (Global Constraints); hardcoding grid
dimensions would silently desync from it if the bbox ever changed.

**Decided:** Added two `[[tool.mypy.overrides]]` entries to `services/api/pyproject.toml`:
`rasterio.*` and `make_fixture_dem`, both `ignore_missing_imports = true`.
**Why:** `rasterio` ships no `py.typed` marker and no `types-rasterio` stub package exists on
PyPI (checked directly, not from memory) — this is the first module in the codebase to import
`rasterio` (Task 1 declared it as a dependency; nothing used it until now), so the gap hadn't
surfaced yet. `make_fixture_dem` is imported by `tests/test_fixture_dem.py` via a runtime
`sys.path.insert` (the module lives in `scripts/`, outside `services/api`, per design doc §8's
repo layout) — mypy's project root has no static view of a module reached that way, and there is
no way to give it one without moving the generator into the `services/api` tree, which the repo
layout deliberately does not do. Both overrides are scoped to the single named module, not a
blanket `ignore_missing_imports`, so no other missing-stub gap is silently masked. Not a new
dependency.

**Decided:** `scripts/` is not covered by `make verify`'s `ruff check`, `ruff format --check`, or
`mypy` (all three run `cd services/api && ...`, and `scripts/` is a sibling directory). Ran all
three manually against `scripts/make_fixture_dem.py` during this task (clean after one line-length
fix) but did not add a fifth `make verify` recipe line to cover it going forward.
**Why:** `make verify`'s four checks are pinned exactly as Task 1 specified ("Matches the brief
exactly (four checks)"); adding a fifth check that runs a different tool invocation over a
different directory is a real scope decision (what should CI do if `scripts/` and `services/api`
disagree on ruff config, e.g.) that belongs to whoever owns the `scripts/` directory's long-term
convention, not something to slip in as a side effect of this task. Flagged here so a future task
can decide deliberately. One concrete gap already observed doing this: `scripts/make_fixture_dem.py`
also triggers a `scipy.interpolate` "missing library stubs" mypy note when checked with
`--strict` directly (no `types-scipy-stubs`/`scipy-stubs` installed) — not fixed here for the same
reason the `rasterio`/`make_fixture_dem` overrides above were scoped narrowly to what
`make verify` actually runs.

**Decided:** `Makefile`'s `fixtures` target now runs
`cd services/api && uv run python ../../scripts/make_fixture_dem.py` (previously a stub echo).
**Why:** Brief Step 6. The fixture *graph* generator (also implied by `make fixtures`'s docstring
in design doc §2.4) is a later task's job — this target does not yet produce everything `make
data`'s eventual real pipeline will; comment updated to say so rather than implying completeness.

**Alternatives rejected:** Fitting the RBF in raw (lon, lat) degree-space (rejected — anisotropic
distance metric, see above). Adding `rio-cogeo` as a dependency to validate the COG (rejected —
GDAL's `COG` driver already provides the guarantee it would check for; see above). Silently
clamping without counting/reporting (rejected — explicitly the resolution's ask; 41% clamped is
exactly the kind of number that needs to be visible, not buried). Tightening `smoothing` to fix
the Twin Peaks/Bernal Heights holdout error (rejected for this task — the brief fixes
`smoothing=0.5` explicitly as a Step 4 parameter, not listed as one of the five ambiguities this
task was asked to resolve; changing it unilaterally would be re-litigating a settled instruction,
not resolving an underspecified one). Adding synthetic water-boundary control points to reduce
extrapolation (rejected — the control-point table is explicitly not this task's to re-author).

---

## Task 4, fix round 1: select `smoothing`/`neighbors` by leave-one-out cross-validation

Coordinator-directed correction: replace the brief's guessed `smoothing=0.5` with a measured
value, selected against the 105 control points (never the holdouts) by leave-one-out
cross-validation (LOO CV).

**Decided:** Added `loo_errors()`, `run_loo_sweep()`, and `select_best()` to
`scripts/make_fixture_dem.py`, plus a standalone `scripts/cv_sweep.py` CLI that prints the full
sweep table and the winner. For each of 16 `(smoothing, neighbors)` combinations — the sweep grid
`smoothing ∈ {0, 0.1, 0.5, 2.0}` × `neighbors ∈ {None, 10, 20, 40}`, exactly as specified — every
one of the 105 control points is held out in turn, `RBFInterpolator` is refit on the other 104,
and the held-out point's error is recorded; the combination is scored by median and p90 (90th
percentile) absolute LOO error. `SMOOTHING`/`NEIGHBORS` in `make_fixture_dem.py` are now literals
equal to `select_best(run_loo_sweep(control_points))`'s output, and
`test_interpolation_parameters_match_cv_sweep_winner` asserts that equality directly against the
committed CSV so the constants cannot silently drift from the measurement that justifies them.
**Why:** Per the coordinator's explicit process — parameters must be selected against the control
points, never tuned against the 10 holdouts, which exist to be the one independent check left
(design doc §2.3). The full sweep table:

| smoothing | neighbors | median LOO error (m) | p90 LOO error (m) |
|---:|---:|---:|---:|
| 0.0 | global | 14.55 | 97.15 |
| 0.0 | 10 | 15.49 | 95.59 |
| 0.0 | 20 | 14.43 | 92.23 |
| 0.0 | 40 | 14.98 | 97.24 |
| 0.1 | global | 14.55 | 97.15 |
| 0.1 | 10 | 15.49 | 95.59 |
| 0.1 | 20 | 14.43 | 92.23 |
| 0.1 | 40 | 14.98 | 97.24 |
| 0.5 | global | 14.55 | 97.15 |
| 0.5 | 10 | 15.49 | 95.59 |
| 0.5 | 20 | 14.43 | 92.23 |
| 0.5 | 40 | 14.98 | 97.24 |
| 2.0 | global | 14.55 | 97.15 |
| 2.0 | 10 | 15.49 | 95.59 |
| **2.0** | **20** | **14.43** | **92.22** |
| 2.0 | 40 | 14.98 | 97.23 |

Winner (bold): **`smoothing=2.0`, `neighbors=20`** — `select_best`'s literal argmin (lowest median,
p90 as tie-break).

**Investigated, not just accepted at face value:** `smoothing` has essentially *zero* measurable
effect at any tested value — all four values give byte-for-byte identical median LOO error at a
given `neighbors`, and the p90 values differ only by 0.01 m (92.22 vs 92.23 for `neighbors=20`;
otherwise exactly equal), which is floating-point noise, not a real distinction. Checked why: this
module fits `RBFInterpolator` in the local-metres coordinate frame chosen in the original Task 4
(see that section above) for isotropic correctness, where control-point pairwise distances run
94 m to 13.5 km. The thin-plate-spline kernel `phi(r) = r² log(r)` evaluates to roughly 4×10⁴ at
the minimum pairwise distance and 1.9×10⁸ at the median — an additive `smoothing` term of 0-2
sitting on the matrix diagonal is negligible against values of that magnitude, regardless of which
of the four swept values is used. `smoothing=2.0` is therefore not a meaningfully "better" choice
than 0/0.1/0.5 — it's what the argmin mechanically returns from a set of values that are, for
practical purposes, indistinguishable at this scale. Recorded here rather than hand-picking a
"nicer-looking" value from the tie, since the whole point of this fix round is a
measurement-driven, auditable choice, not a judgment call dressed up as one.
`neighbors=20` is the real signal — it beats `neighbors=None` (global) on both median (14.43 m vs
14.55 m) and p90 (92.22-92.23 m vs 97.15 m), and beats `neighbors=10`/`40` on both metrics too.

**Consequence — the summit undershoot is not fixed:** rebuilt `data/dem/sf_fixture_dem.tif` with
the new parameters and resampled all 10 holdouts. Twin Peaks summit: 248.0 m sampled vs. 281.0 m
actual (-33.0 m, was -33.5 m). Bernal Heights summit: 114.6 m sampled vs. 135.6 m actual (-21.0 m,
was -21.1 m). Both still fail design doc §2.3's ±12 m tolerance; the other 8 of 10 holdouts remain
within it (unchanged to within ~0.3 m of the previous build). **This is the legitimate
"unachievable" result the coordinator asked to see if the data supported it, not a tuning
failure**: a LOO sweep over the exact grid specified, scored the way specified, on the correct
(control-only) data, still leaves two isolated summit holdouts more than 20-33 m off. Both are
genuinely hard cases — Twin Peaks summit's nearest control points are on its own slopes (Twin
Peaks south peak 275.5 m at ~500 m away, Twin Peaks Blvd switchback 230 m, Burnett Ave 210 m —
all already *below* the summit itself), so any smooth interpolator fit through slope points has to
undershoot a genuine local maximum with no data placed at its top; the same shape applies to
Bernal Heights (Bernal Heights Blvd north/south sides at 120/110 m, both well below the 135.6 m
summit). Reported to the coordinator as-is rather than force-fit; the tolerance call is theirs to
make.

**Consequence — in-hull clamping got slightly worse, not better:** the coordinator's hypothesis
was that `neighbors` (a local fit) would reduce in-hull oscillation. Measured directly: in-hull
clamped pixels went from 70,510 of 970,057 (7.27%, old global `smoothing=0.5` config) to 74,368 of
970,057 (**7.67%**, new `smoothing=2.0, neighbors=20` config) — worse, not better. Total clamped
pixels also rose slightly, from 41.04% to 42.27%. `ClampStats` gained `in_hull_clamped`/
`in_hull_pixels` fields (computed via a `scipy.spatial.Delaunay` hull test against the control
points, no new dependency — `scipy` is already a project dependency) so this figure is reported by
`main()` and stored in `manifest.json` on every future build, not just measured once here.
**Why the hypothesis didn't pan out, investigated:** `neighbors=20` selects the 20 nearest control
points per query location and fits a local thin-plate spline among just those — for a query point
near the edge of a locally sparse cluster (several exist in the SF control set: Golden Gate Park's
interior, the Presidio, the Bayview/Candlestick area), 20 nearest neighbors can still span a wide
area with large elevation swings (e.g. a park-floor point and a nearby hill crest both within the
20 nearest), which does not obviously reduce oscillation versus a global fit — it just changes
*which* points can pull a given location's estimate away from `[0, 300]`. This is left as an open
question for whoever revisits interpolation strategy next, not resolved here.

**Decided:** `interpolate_dem()` now passes `neighbors=NEIGHBORS` to `RBFInterpolator`; `main()` and
`write_manifest()` additionally report/record `in_hull_clamped_pixel_count`,
`in_hull_pixel_count`, `interpolation_smoothing`, and `interpolation_neighbors`.
**Why:** Makes the fix-round parameters and their in-hull clamp consequence visible on every
future build, not just in this decision log entry.

**Decided:** Two of `test_fixture_dem.py`'s five `build_dem()` calls
(`test_fixture_dem_is_deterministic`, `test_holdout_points_are_excluded_from_interpolation`) now
pass `resolution_m=50.0` instead of the production 10 m default.
**Why:** `neighbors=20` makes a full 10 m/2.4M-pixel grid evaluation take ~40 s (a local RBF fit
per query point is intrinsically more expensive than the old global fit's single linear solve,
~8 s at the same resolution). With five `build_dem()` calls in the test file, an all-10 m test
suite would take ~3.5 minutes just for this file. Determinism and holdout-exclusion are both
properties of the fit/hash code path, not of grid resolution — a coarser grid exercises the same
logic in a fraction of the time (0.9 s at 50 m). The `dem_path` session fixture (used by the
CRS/bbox, nodata, clamp-bounds, COG-structure, and sanity-check tests) stays at the real 10 m
production resolution, built once per test session. Full-resolution (10 m) determinism is proven
separately and is not weakened by this change: two full `make fixtures` runs after this fix
produced identical `dem_data_sha256`/`dem_file_sha256`, recorded in task-4-report.md's fix-round
section.

**Alternatives rejected:** Hand-picking a smoothing value from the tie based on "which looks more
principled" (rejected — defeats the purpose of a measurement-driven choice; `2.0` is reported
as-is, with the tie itself documented as the real finding). Widening the ±12 m holdout tolerance
unilaterally to make the two summit holdouts pass (rejected — explicitly the coordinator's
instruction: report the achievable figure, let them make the tolerance call). Reducing the
production DEM's resolution to speed up tests (rejected — `data/dem/sf_fixture_dem.tif` stays at
the brief's specified 10 m; only two *test-only* `build_dem()` calls in `tmp_path` were changed,
not the production default or the `dem_path` fixture other tests rely on for structural realism).
Trying additional `neighbors` values beyond `{10, 20, 40}` or `smoothing` values beyond
`{0, 0.1, 0.5, 2.0}` to chase a materially better score (rejected — out of scope for this fix
round; the coordinator specified this exact grid, "at least" these values, and the four-value
smoothing tie plus the summit undershoot surviving the winning config are themselves the
reportable finding, not a reason to keep searching without being asked to).

---

## Task 4, fix round 2: targeted control points for street-scale relief; tolerance ruling

Coordinator ruling and follow-on finding. Two parts: (1) the coordinator's ruling on the ±12 m
holdout tolerance, encoded as data, not inferred; (2) the substantive fix — the fixture DEM's 105
control points spread across ~100 km² gave it an *effective* resolution far coarser than its 10 m
grid, so it could not represent street-scale relief (SPEC §4.1's named failure mode, which SPEC
itself describes for 30 m SRTM — our fixture DEM, being smoother than SRTM in practice, reproduced
the exact trap by necessity of the fixtures-only constraint). Mitigated with 13 new, real,
individually-sourced control points at specific places SF's street-scale relief is known to
matter.

**Stated plainly, as instructed:** the fixture DEM cannot represent street-scale relief except
where control points are locally dense. This is not a bug introduced by this task — it is the
SPEC §4.1 trap ("a router fed a DEM which smears [50-150 m relief] will confidently produce
routes over hills it can't see") reproduced by the fixtures-only environment constraint, since a
sparse, city-wide control-point table interpolated with a smooth RBF has no way to know about
relief between its sample points. The mitigation applied here — targeted control points at known
steep blocks and low corridors — narrows the trap to wherever nobody has yet added a targeted
point; it does not close it. **The real remedy is the 3DEP run, which remains an outstanding
gate** (design doc §2.4).

**Decided (tolerance ruling, encoded as named data):** Added `HOLDOUT_TOLERANCE_M = 12.0`,
`SUMMIT_HOLDOUT_TOLERANCE_M = 35.0`, and `SUMMIT_HOLDOUT_NAMES = frozenset({"Twin Peaks summit
(Eureka Peak)", "Bernal Heights summit"})` to `scripts/make_fixture_dem.py`, plus a
`HoldoutResult`/`sample_and_report_holdouts()` pair that scores each of the 10 holdouts against
the right tolerance and a `main()` print block reporting the full table on every build.
`test_summit_holdout_set_is_exactly_two_named_points` pins the named set itself (a silent edit
there would silently change the ruling without anyone noticing);
`test_holdout_tolerance_assignment_matches_the_ruling` proves the *mechanism* — every holdout in
`SUMMIT_HOLDOUT_NAMES` gets 35 m, every other one gets 12 m — deliberately as a mechanism test,
not a pass/fail gate on the actual sampled errors (see below for why).
**Why the coordinator's number, not a re-derived one:** their reasoning — "you cannot hit a
tolerance tighter than the model's demonstrated accuracy," pointing at this task's own measured
14.43 m median LOO error as the reason ±12 m was never achievable for the hardest points — is a
conclusion from evidence this task produced, not a new guess; nothing to re-verify independently,
so it's encoded as given, by name, per their explicit "do not infer 'is a summit' from the data"
instruction.

**Decided (13 new control points, `role=control`, never `holdout`):** Added, each individually
sourced with an honest `source`/`confidence` (same standard as the rest of the table — see
`elevation_control_points.notes.md`'s methodology):

| Point | lat, lon | ele_m | source | confidence |
|---|---|---:|---|---|
| Filbert St & Hyde St | 37.8002, -122.4193 | 96.0 | derived: comparable to sourced Lombard & Hyde (325 ft) one block north, same ridge | low |
| Filbert St & Leavenworth St | 37.8002, -122.4179 | 56.4 | derived: Filbert & Hyde estimate minus 31.5% grade (Lonely Planet/Secret SF) x 412.5 ft sourced block length | medium |
| Lombard St & Hyde St (crooked block top) | 37.8017, -122.4193 | 99.1 | multiple travel sources (mikesroadtrip.com, roadsideamerica.com): 325 ft at crooked block top | medium |
| Lombard St & Leavenworth St (crooked block bottom) | 37.8017, -122.4179 | 68.6 | derived: 325 ft top minus widely-cited 100 ft vertical drop over the switchbacks | medium |
| Duboce Ave & Church St | 37.7695, -122.4291 | 27.0 | estimated from surrounding terrain; coordinates from Wikipedia "Duboce and Church station" | low |
| Duboce Ave & Sanchez St | 37.7697, -122.4303 | 29.0 | estimated from surrounding terrain | low |
| Pierce St & Haight St | 37.7717, -122.4340 | 35.0 | estimated from surrounding terrain: Wiggle corridor gentle climb | low |
| Scott St & Fell St | 37.7720, -122.4370 | 42.0 | estimated from surrounding terrain: Wiggle corridor approaching Panhandle | low |
| Buena Vista Ave East at Duboce (park east base) | 37.7690, -122.4380 | 50.0 | estimated from surrounding terrain: park east base | low |
| Buena Vista Ave West near Haight (park west base) | 37.7676, -122.4430 | 65.0 | estimated from surrounding terrain: park west base | low |
| Corona Heights base (16th St & Flint St) | 37.7621, -122.4381 | 91.0 | base of hill cited ~300 ft (Roadtrippers/Apple Maps aggregation) | medium |
| Twin Peaks Blvd near Christmas Tree Point | 37.7568, -122.4468 | 260.6 | SF Standard: ~70 ft below Eureka Peak's 925 ft summit | medium |
| Twin Peaks Blvd upper south switchback | 37.7538, -122.4478 | 248.0 | estimated from surrounding terrain: upper approach to south peak | low |

**Skipped Steiner & Waller** from the coordinator's list — it already exists as a control point
(`Wiggle corridor floor (Waller and Steiner)`, 28.0 m, `estimated from surrounding terrain`,
`low`, committed in Task 4's original pass). Adding a second row at or near the same coordinates
would either duplicate it (failing the CSV's own duplicate-coordinate check) or place a
near-duplicate a few metres away for no reason.

**Sourcing method for the well-cited pair (Filbert/Lombard):** Lombard Street's crooked block
(Hyde-Leavenworth) is independently and consistently cited across multiple travel sources at 325
ft elevation at its top, with a widely-cited 100 ft vertical drop over the switchback descent —
used as-is (Hyde end 325 ft = 99.1 m, Leavenworth end 225 ft = 68.6 m). Filbert Street's 31.5%
grade over the same Hyde-Leavenworth block (412.5 ft straight-line, matching Lombard's own cited
block length one block south on the same ridge) is independently, consistently cited (Lonely
Planet, Secret SF, and others agree on 31.5%, tied SF's steepest). No independent citable absolute
elevation was found for Filbert & Hyde specifically (ordinary street corners rarely have a
surveyed spot elevation, same limitation the original notes.md describes for most of the table);
it was estimated as comparable to the sourced Lombard & Hyde point one block north on the same
ridge crest (confidence: low, honestly, not dressed up as more certain than it is), and Filbert &
Leavenworth was then *derived* from that estimate using the independently-sourced 31.5% grade fact
(confidence: medium, since the *delta* is well-sourced even though the absolute anchor isn't).
Coordinates for Filbert & Hyde were cross-checked against an independent web search result
(37.800208, -122.419334) that landed within ~50 m of a hand-computed estimate (using Vallejo &
Jones' committed 37.7998,-122.4177 anchor, Lombard's own cited 412.5 ft block width converted to
degrees at this latitude, and standard SF block-count reasoning) — the two independent methods
agreeing to within one grid cell's width was treated as adequate corroboration for placement
(not for the elevation values, which come from the citations above).
**Verified — not asserted — that this closes the geometric trap:** sampled the finished DEM at
Filbert & Hyde (37.8002, -122.4193) and Filbert & Leavenworth (37.8002, -122.4179) before and
after this fix. **Before** (105-point CSV, committed at `efa5a6a`): Hyde = 92.9 m, Leavenworth =
100.6 m — the DEM showed this block **sloping the wrong way** (-6.3% — Leavenworth reading
*higher* than Hyde), not merely "gentle," because no control point anywhere near it said
otherwise. **After** (118-point CSV): Hyde = 95.1 m, Leavenworth = 57.2 m, a **30.8% grade** over
the same 123 m — matching the real, cited 31.5% to within the RBF's own smoothing (the raster
samples 10 m off the exact control-point coordinates, so exact reproduction of the control values
was never expected). This is direct, measured proof that a targeted control point changes what the
DEM can represent at a specific place, not an assumption.

**Decided:** Re-ran `run_loo_sweep`/`select_best` (`scripts/cv_sweep.py`) against the expanded
118-point control set. Same winner as the 105-point sweep:

| smoothing | neighbors | median LOO error (m) | p90 LOO error (m) |
|---:|---:|---:|---:|
| 0.0 | global | 15.21 | 97.58 |
| 0.0 | 10 | 16.79 | 97.14 |
| 0.0 | 20 | 13.80 | 92.24 |
| 0.0 | 40 | 14.66 | 97.88 |
| 0.1 | global | 15.21 | 97.58 |
| 0.1 | 10 | 16.79 | 97.14 |
| 0.1 | 20 | 13.80 | 92.24 |
| 0.1 | 40 | 14.66 | 97.88 |
| 0.5 | global | 15.21 | 97.58 |
| 0.5 | 10 | 16.79 | 97.14 |
| 0.5 | 20 | 13.80 | 92.24 |
| 0.5 | 40 | 14.66 | 97.88 |
| 2.0 | global | 15.21 | 97.58 |
| 2.0 | 10 | 16.78 | 97.14 |
| **2.0** | **20** | **13.80** | **92.24** |
| 2.0 | 40 | 14.66 | 97.88 |

**Winner: `smoothing=2.0, neighbors=20`, unchanged.** Median LOO error improved slightly (14.43 m
→ 13.80 m) with 13 more control points; the near-tie among smoothing values persists (at
`neighbors=20`, the four smoothing values now differ by a few thousandths of a metre — see raw
values in this task's report — floating-point noise, same finding as fix round 1, now confirmed a
second time at a different point count). No code change needed —
`test_interpolation_parameters_match_cv_sweep_winner` re-verified this against the live CSV rather
than assumed it.

**Reported (in-hull clamping, resolution #3 follow-up):** in-hull clamped pixels fell from 74,368
of 970,057 (7.67%, fix round 1's config) to **71,746 of 970,057 (7.40%)** — a small improvement,
plausibly because 13 more real control points densify and better-condition the convex hull the
"in-hull" test is measured against, rather than because of any parameter change (parameters are
unchanged). Total clamped pixels also fell, 42.27% → **39.77%** (more of the bbox now falls
inside a denser hull, so less of it is "no nearby data, extrapolate toward sea level").

**Important honest finding — NOT quietly resolved, flagged for the coordinator:** adding the 13
new points changed which holdouts pass the ±12 m tolerance. **Twin Peaks summit's error improved
substantially** (-33.0 m → **-20.5 m**) because the new Twin Peaks Blvd/Christmas Tree Point
control point (260.6 m) is now in its local `neighbors=20` neighborhood — comfortably within the
new ±35 m summit tolerance. Bernal Heights is unchanged (-21.0 m, still within ±35 m; no new
points were added near it). **But Corona Heights summit — not a named summit exception, currently
gated at ±12 m — regressed from -5.9 m to -39.1 m, and Fort Mason regressed from -11.7 m to
-13.6 m** (a small miss, just over the line). Investigated the mechanism, not just observed it: a
nearest-neighbor check at the time of writing shows three of the new points (`Buena Vista Ave West
near Haight` 65 m, `Buena Vista Ave East at Duboce` 50 m, `Corona Heights base` 91 m) now fall
within Corona Heights summit's 20 nearest control points, displacing higher points (`Tank Hill`
198 m, `Burnett Ave near Twin Peaks` 210 m) that used to be in that neighborhood — with
`neighbors=20`'s local fit, three new nearby *low* points can out-vote the higher ones that used
to carry the local estimate, even though the total control-point count only went up. This is a
real property of local RBF interpolation, not a data error: the added points are real, sourced,
and reasonably placed (Corona Heights base's placement was cross-checked in the same manner as
Filbert & Hyde above). **Not fixed here.** Per the coordinator's explicit instruction — "do not
adjust any value to produce a particular outcome" — neither the new points nor the LOO-selected
parameters were altered to make this pass; the gating test
(`test_holdout_tolerance_assignment_matches_the_ruling`) was scoped to check tolerance
*assignment* correctness rather than hard-fail on the current pass/fail table, specifically so
this finding could surface honestly instead of being hidden by either loosening a test or reverting
real data. **This needs the coordinator's call**: whether Corona Heights summit should join the
named summit exception set (it is, after all, also a holdout summit with no control point at its
true peak — the same structural reason Twin Peaks and Bernal Heights get ±35 m), whether
`neighbors=20`'s sensitivity to local point density is itself a reason to revisit the winning
config despite the LOO sweep, or something else. Not decided unilaterally here.

**Alternatives rejected:** Repositioning or removing the three points that caused the Corona
Heights regression (rejected — would be "adjusting a value to produce a particular outcome,"
exactly what was ruled out; the points are real, sourced, and reasonably placed). Adding Corona
Heights to `SUMMIT_HOLDOUT_NAMES` unilaterally (rejected — the coordinator's instruction named
exactly two summits and said not to infer the set from the data; expanding it myself the moment a
third holdout became inconvenient would be exactly that). Hard-gating
`test_holdout_tolerance_assignment_matches_the_ruling` on all 10 holdouts passing (rejected — would
either force a red `make verify` against a hard global constraint, or force a same-session
loosening of a test not yet committed to hide a real finding; scoped the test to the tolerance
*mechanism* instead and reported the actual numbers here and in the task report for a human
decision). Re-tuning `neighbors` specifically to fix Corona Heights (rejected — would defeat the
entire point of selecting parameters by LOO cross-validation rather than by outcome).

---

## Task 4, fix round 3 (final): the Filbert finding, the Corona Heights fragility, and the closing tolerance ruling

### The most important result in this task

**Before fix round 2, the fixture DEM sampled Filbert Street between Hyde and Leavenworth — one
of the steepest streets in the United States, real-world grade 31.5% — at −6.3%: sloping the
wrong way.** Not "smoothed to gentle." Inverted. A router built on that DEM would have offered
that block as a mild downhill shortcut, with total confidence, because nothing in the DEM knew the
hill existed — no assertion anywhere in the test suite could have caught it, because nothing in
that area was ever checked against ground truth until this task specifically went looking. After
adding a real, sourced control point at each end of that block, the same DEM samples the same two
points at 30.8%, against the real, cited 31.5%.

This is not a corner case. It is **SPEC §4.1's named failure mode, reproduced exactly**: "a router
fed a DEM which smears [50-150 m relief] will confidently produce routes over hills it can't see."
SPEC says this about 30 m SRTM. A sparse-control synthetic DEM interpolated across ~100 km² from
~100 points is smoother than that in the gaps between samples — this fixture reproduced the trap
by construction, not by accident, the moment fixtures-only was chosen over a real 3DEP run. The
fix (targeted control points) only closes the trap at the specific places someone thought to add a
point. **It does not, and cannot, close it everywhere.** The 3DEP run is the actual remedy and
remains outstanding (design doc §2.4). Anyone tempted to treat this fixture DEM as "good enough"
for a real accuracy claim should read this paragraph first.

### A genuine fragility, for whoever runs the real 3DEP data next

Fix round 2 added three new real, sourced, reasonably-placed control points — `Buena Vista Ave
West near Haight` (65 m), `Buena Vista Ave East at Duboce` (50 m), and `Corona Heights base`
(91 m) — none of them anywhere near an existing error. The result: **Corona Heights summit's
holdout error moved from −5.9 m to −39.1 m, a 33.2 m swing, purely from three new low-elevation
points entering its local 20-nearest-neighbor set and displacing two higher points (`Tank Hill`
198 m, `Burnett Ave near Twin Peaks` 210 m) that used to anchor the local fit.** Total control
density in the area only went up. The estimate at a specific nearby point got dramatically worse.

This is a property of `neighbors=20` (a local RBF fit), not a bug in this task's data: a local fit
depends on *exactly which* points land in the k-nearest window, and that window's composition can
flip non-monotonically as points are added nearby, even when every added point is individually
correct. **A global fit (`neighbors=None`) would not have this specific failure mode** — every
point always contributes, so adding more real data can only ever add more real signal, never
displace an existing neighbor from consideration — though a global fit has its own known
weaknesses (see "Task 4, fix round 1": oscillation across the whole 105/118-point extent). This
is flagged explicitly for whoever runs the real 3DEP-and-real-graph pipeline: **do not assume that
adding more real elevation data to a local-neighbor RBF (or any k-nearest-neighbor-style
interpolation) can only improve accuracy near existing points.** It can silently make a specific
nearby estimate worse, and the only way to know is to keep checking held-out ground truth after
every change — exactly the discipline this task's holdout set exists to enforce.

### Closing ruling: summit defined by principle, tolerance set from measurement

Coordinator's own correction, recorded verbatim in spirit: naming exactly the two holdouts that
were failing in fix round 2 ("Twin Peaks and Bernal") was **goalpost-fitting** — the named
exception should follow from a stated *reason*, not from which holdouts happened to be failing at
the time. The reason was always "an interpolator undershoots a local maximum with no control point
at its own peak." Applied consistently, that reason names **three** holdouts, not two — Corona
Heights summit qualifies on the same structural grounds as Twin Peaks and Bernal Heights (its
nearest control points, including two of fix round 2's own additions, are all on its slopes,
below the peak).

**Decided:** `SUMMIT_HOLDOUT_NAMES` in `scripts/make_fixture_dem.py` is now `frozenset({"Twin
Peaks summit (Eureka Peak)", "Bernal Heights summit", "Corona Heights summit"})`, with a code
comment stating the definition — *"a holdout that is a local terrain maximum with no control point
at its own peak"* — so a future point added at any of these three peaks, or a future holdout that
newly qualifies, can be judged against a written rule rather than against whatever currently fails.
**Why:** exactly the coordinator's ruling; the alternative (leaving two names that happened to be
convenient) would have been a tolerance that quietly tracks outcomes instead of a fixed, auditable
rule.

**Decided:** `HOLDOUT_TOLERANCE_M = 25.0` (non-summit), `SUMMIT_HOLDOUT_TOLERANCE_M = 40.0`
(summit), replacing fix round 2's 12/35. Both derived from the measured 118-point LOO median error
(13.80 m): 25 m ≈ 1.8x median, 40 m ≈ 2.9x median (the extra headroom over the non-summit number
absorbs the systematic peak-undershoot every smooth interpolator exhibits at a true local maximum
with no data on top of it, on top of the base LOO error every holdout carries).
**Why the old ±12 m number was wrong, not just strict:** the model's own measured typical error
(median LOO, 13.80 m) is *larger* than a ±12 m gate. A tolerance set below the model's demonstrated
error rate doesn't test whether the DEM is broken — it tests whether a given holdout's error
happens to land above or below the noise floor, which is not a meaningful pass/fail signal at all.
That is why 8 of 10, then 7 of 10, kept almost-arbitrarily shuffling across fix rounds as control
points changed: the gate was measuring noise. Every number in this ruling now sits *above* that
noise floor by a stated, checkable margin.
**The comment placed next to these constants states, verbatim in spirit:** this gate exists to
catch **gross breakage** — a flipped axis, a units error, a broken sampler — not to certify
elevation accuracy. The fixture DEM cannot certify accuracy at any tolerance. The 3DEP run is the
accuracy gate, and it has not been run.

**Decided:** `test_holdout_tolerance_assignment_matches_the_ruling` (mechanism: every named summit
gets ±40 m, every other holdout gets ±25 m) and a new `test_all_holdouts_pass_the_gross_breakage_gate`
(a real hard gate, `assert not failures` — fix round 2 deliberately did not hard-gate this, because
the tolerance was still under dispute; it no longer is). All 10 holdouts pass under the final
ruling:

| Holdout | Actual | Sampled | Error | Tolerance | Summit? | Result |
|---|---:|---:|---:|---:|:---:|:---:|
| Twin Peaks summit (Eureka Peak) | 281.0 | 260.5 | −20.5 | ±40 | yes | OK |
| Ferry Building | 2.0 | 1.1 | −0.9 | ±25 | no | OK |
| Ocean Beach (at Judah) | 4.0 | 3.8 | −0.2 | ±25 | no | OK |
| Alamo Square | 75.9 | 72.5 | −3.4 | ±25 | no | OK |
| Corona Heights summit | 158.5 | 119.4 | −39.1 | ±40 | yes | OK |
| Bernal Heights summit | 135.6 | 114.6 | −21.0 | ±40 | yes | OK |
| Lands End | 50.0 | 60.3 | +10.3 | ±25 | no | OK |
| Mission Dolores Park | 18.9 | 18.8 | −0.1 | ±25 | no | OK |
| Fort Mason | 25.9 | 12.3 | −13.6 | ±25 | no | OK |
| Candlestick Point | 4.0 | 3.8 | −0.2 | ±25 | no | OK |

**10 of 10.** Note that Corona Heights summit's margin under its new ±40 m tolerance is 0.9 m —
this is a real pass against a principled, measured tolerance, not a comfortable one; the fragility
noted above means a future data change could push it back over the line, which is exactly why that
fragility is documented rather than left implicit.

**Decided: nothing about the interpolation or the data changed in this round.** No re-sweep, no
re-tuned parameters, no added or moved control points. `smoothing=2.0, neighbors=20` (selected in
fix round 1, re-confirmed unchanged in fix round 2) stands. `data/dem/sf_fixture_dem.tif`'s
`dem_file_sha256` (`2cb640b2...`) and `dem_data_sha256` (`48351781...`) are byte-identical to fix
round 2's build — verified directly (`sha256_of(DEFAULT_DEM_PATH)` re-run and compared) rather than
assumed from "I didn't touch the generator's data path." Only `scripts/make_fixture_dem.py`'s
tolerance constants and `services/api/tests/test_fixture_dem.py`'s corresponding tests changed.

**Alternatives rejected:** Keeping the two-name exception set and widening tolerances just enough
to pass Corona Heights as a one-off (rejected — the coordinator's explicit point: define the
exception by the stated principle, not by patching around whichever holdout is inconvenient this
round). Switching to `neighbors=None` (global fit) to eliminate the Corona Heights fragility
(rejected — explicitly out of scope for this round: "the interpolation is settled"; also would
undo fix round 1's own measured, LOO-selected choice for a reason not grounded in that same
selection process). Treating the Corona Heights fragility as a one-off anomaly not worth recording
(rejected — it is a real, general property of local-neighbor RBF interpolation that will recur
under the real 3DEP/real-graph pipeline too, and costs nothing to write down once, here, for
whoever hits it next).

## Task 5: Geodesic polyline resampling and bilinear DEM sampling

**Decided:** `services/api/contour/elevation.py` implements `geodesic_length_m`, `resample_polyline`,
and `DemSampler` exactly per the design doc §4.4 / task-5-brief.md interfaces. `resample_polyline`
uses `pyproj.Geod(ellps="WGS84")` — `inv` for per-edge distance + forward azimuth, `fwd` to step
along each edge — never planar interpolation, per the brief's stated reason (SF spans enough
longitude that planar interpolation drifts over a multi-kilometre route). `DemSampler` opens the
DEM once in `__init__`, reads its single band fully into memory, and reuses that state across
`sample()` calls.

**Bilinear was hand-rolled, not taken from a library.** `rasterio.sample()` (and
`rasterio.sample.sample_gen`) is nearest-neighbour only — confirmed directly by exercising it in
`tests/test_elevation_sampling.py::test_dem_sampler_bilinear_differs_from_nearest_neighbor`, which
asserts the two methods disagree at a deliberately off-centre point. `scipy.interpolate` (already a
project dependency, used by Task 4's DEM generator) was considered as an alternative source for
bilinear interpolation (e.g. `RegularGridInterpolator`), and rejected: it would mean re-reading the
whole grid into a scipy-managed structure with its own coordinate-ordering conventions to interpolate
one number, for no accuracy benefit over the direct closed-form 2x2 bilinear formula, and it
obscures exactly the row/col arithmetic that the lon/lat-order bug this module is most worried about
lives in. The hand-rolled version is ~15 lines: convert (lon, lat) to fractional (col, row) via the
dataset's inverse affine transform, shift by -0.5 into pixel-*centre* coordinates (GDAL's affine
transform is corner-referenced), floor to get the 2x2 neighbourhood, and interpolate. No new
dependency was added (`numpy` and `rasterio` are already present, per the brief's resolution #4);
`itertools.pairwise` (stdlib) replaced an initial `zip(coords, coords[1:], strict=True)` that was a
real bug caught by the RED test run (see below) — `strict=True` requires equal-length iterables, but
a pairwise zip is intentionally one element short, so every call raised `ValueError` immediately.

**Edge-clamping choice.** A query point can be inside the raster's bounding box (between the outer
edges of the outermost pixels) but past the *centre* of the outermost pixel — the "outermost
half-pixel margin" — where a full 2x2 interpolation neighbourhood isn't available on one side.
`DemSampler.sample()` clamps the fractional pixel coordinate into `[0, width-1)` / `[0, height-1)`
in that case, which pins the point to the nearest valid neighbourhood (in the limit, the corner
pixel's own value) rather than raising. This was resolution #2 in the task-5 brief, and the
reasoning is unchanged from there: real coastal/service-area-edge points are legitimate DEM queries,
and rejecting them just for being close to the grid boundary would be wrong — only a point genuinely
outside the bounding box (`ValueError`, "outside the DEM extent") or one whose neighbourhood touches
a masked/nodata pixel (`ValueError`, "masked/nodata DEM pixel") fails loudly. Both are covered by
dedicated tests (`test_dem_sampler_clamps_at_raster_edge_instead_of_raising`,
`test_dem_sampler_raises_on_masked_nodata_neighborhood`); the fixture DEM itself carries zero nodata
pixels (Task 4), so the nodata-raising test uses a small synthetic in-memory GeoTIFF built in the
test file rather than the committed fixture.

**Why fail loudly on out-of-bounds/masked, rather than returning nodata.** Per the brief: a route's
elevation profile downstream (Task 6) accumulates ascent and computes grades from every sampled
point. A silently-returned nodata sentinel (e.g. `-9999.0`) would get treated as a real elevation and
averaged into ascent/grade math, producing a wrong number with no error anywhere in the pipeline —
much harder to debug than an immediate `ValueError` at the sampling boundary.

**`affine` used directly in tests, not runtime code — not a new dependency.** The synthetic-raster
test helper (`_write_synthetic_dem` in `tests/test_elevation_sampling.py`) constructs an
`affine.Affine` transform to hand-build small GeoTIFFs with exact, known pixel values (needed for the
bilinear-vs-nearest contrast, exact-value, edge-clamp, and nodata tests, where the real fixture DEM's
smoothed terrain surface can't guarantee a specific number). `affine` is already an unconditional
transitive dependency of `rasterio` (rasterio's own `dataset.transform` is an `affine.Affine`
instance) and was already present in the resolved environment; it is not added to `pyproject.toml`
as a direct dependency because runtime code (`contour/elevation.py`) never imports it directly —
only the test file does, for GeoTIFF authoring convenience.

**Coordinate-order test.** `test_dem_sampler_lon_lat_order_is_not_swapped` samples two named
landmarks with clearly different, individually-plausible elevations in the correct `(lon, lat)`
order (Twin Peaks, a hill, vs. SoMa, near sea level — see `tests/test_fixture_dem.py`'s own
shape-sanity-check coordinates for both), then asserts that swapping one of them to `(lat, lon)`
raises `ValueError`. This is a stronger assertion than "returns a different plausible elevation":
`SF_BBOX`'s lon range (~-122.5) and lat range (~37.7-37.8) don't overlap in magnitude at all, so a
genuinely swapped SF coordinate can never land back inside the DEM's extent — it is structurally
guaranteed to raise the same "outside the DEM extent" error a wildly-out-of-service-area point would.
An implementation that silently swapped lon/lat *internally* (e.g. indexing the affine transform
with `(lat, lon)` instead of `(lon, lat)`) would fail the *correctly-ordered* calls in this same test
instead, for the equivalent reason: the internal fractional pixel coordinate would be computed from
values wildly outside the raster's pixel index range.

**Degenerate inputs, tested directly per the brief ("not hypothetical").** Empty input, a
single-point input, consecutive duplicate coordinates mid-route, leading/trailing duplicates, and
every-point-identical are each covered by a dedicated test in
`tests/test_elevation_sampling.py`. Zero-length edges (`Geod.inv` distance `<= 1e-6` m) are skipped
during segment-building rather than stepped along, since a zero-length edge has no defined azimuth.

**Round-trip accuracy.** `test_resample_1km_north_south_line_has_101_points` (brief step 1) and
`test_resample_polyline_round_trip_accuracy_multi_vertex` (task instructions' required addition, a
zigzag multi-vertex path) both assert the final cumulative distance matches
`geodesic_length_m`/`pyproj.Geod`'s independently computed total length to within 0.5 m. This holds
by construction rather than by luck: the final sample's distance is `total_length_m`, computed by
summing the same `Geod.inv` per-edge distances `geodesic_length_m` sums, not by re-deriving it via
`Geod.fwd` stepping (which would accumulate its own floating-point drift over many steps).

**Alternatives rejected:** Using `rasterio.sample()` directly and accepting nearest-neighbour
sampling (rejected outright — the brief's explicit stated reason: it produces a stair-step artefact
that reads downstream as alternating 0%/8% phantom grades on flat ground). Reopening the rasterio
dataset per `sample()` call (rejected — resolution #1: reopening per call would dominate the
1200 ms p95 route-request latency budget, since a route resamples to thousands of points). Looping
one `dataset.read()`/`Geod.fwd()` call per point instead of vectorizing with numpy (rejected —
resolution #3, same latency reasoning). Raising on any point in the outermost half-pixel margin
instead of clamping (rejected — resolution #2: would reject legitimate coastal/edge-of-service-area
points, which the fixture DEM covers right up to `SF_BBOX`'s boundary).

---

## Task 4, fix round 4: the DEM is now San Francisco outside the sampled corridor

The independent review's headline finding, restated plainly because it is the point of this
round: **outside the well-sampled central corridor, this DEM was not San Francisco.** McLaren
Park's summit read 0.0 m. So did the Excelsior, and the Presidio. Open SF Bay read 33-50 m — a
hill on open water. The central massif read 100 m too high. For a router whose objective *is*
climbing, a McLaren Park route reported zero gain and a ride across the Bay reported a climb.

The mechanism was never mysterious: where no control point was near, the thin-plate spline dived
negative and `np.clip` turned that into 0.0 m; over the Bay it overshot upward instead. What kept
it invisible is the part worth remembering — **no holdout and no sanity point sat anywhere near
any affected region. The validation set determined what could be seen.** Every check pointed at
the same corridor, and every check passed.

### Coverage: 98 new control points (118 -> 216 control; holdouts untouched at 10)

Added as `role=control` only. No holdout was added, moved, revalued, or built on top of; the
closest any control point comes to any holdout is 65.4 m ("Great Hwy and Judah St" vs. the
"Ocean Beach (at Judah)" holdout), a pre-existing pair, and every one of the 98 new points is
more than 460 m from the nearest holdout.

- **Southeast (21)** — McLaren Park (summit and four flanks), the Excelsior, Crocker-Amazon,
  Visitacion Valley, Little Hollywood, Bayview Hill (summit and south slope), Silver Terrace,
  Portola, University Mound, Hunters Point Hill, India Basin.
- **Southwest and west (28)** — Oceanview, Merced Heights, Ingleside, Ingleside Terraces,
  Sunnyside, Mission Terrace, Glen Canyon ridge, plus an Outer Sunset / Parkside / Lake Merced
  grid (Taraval, Judah, Noriega, Sloat, Great Hwy, Harding Park, SF State, Fort Funston).
- **The central massif (12)** — Sutro Tower base, Clarendon, Midtown Terrace, Laguna Honda
  (Blvd and reservoir), Portola Dr at Woodside and at Twin Peaks Blvd, Forest Hill station,
  West Portal, Edgehill Mountain, Miraloma Park, Mount Olympus. These read too *high* before;
  real values pull them down.
- **Northwest (17)** — the Presidio (Inspiration Point ridge, Main Post, Crissy Field, the toll
  plaza, Baker Beach, Rob Hill, the PHS hospital ground), Sea Cliff, Lincoln Park, Sutro Heights,
  Point Lobos and an Outer Richmond grid.
- **Northeast (10)** — see "a defect the review had not seen" below.
- **Sea-level anchors (20)** — a ring of `0.0 m` points over open water: SF Bay east and north of
  the city, the Golden Gate strait, and the Pacific west of Ocean Beach and Fort Funston. Marked
  `source=sea level by definition`, `confidence=high`. These are legitimate ground truth: open
  water is at 0 m, and they are what stops the surface inventing a hill on the Bay or diving
  negative at the coast.

**Sourcing, honestly.** Named summits carry citations: Sutro Tower base 834 ft (Sutro Tower's own
site and Wikipedia, `high`), Edgehill Mountain 734 ft, Mount Olympus 553 ft, Bayview Hill 425 ft,
Bayview Park 367 ft (topozone), McLaren Park's high point 519 ft (PeakVisor), Hunters Point Hill
125 ft (USGS as cited by a secondary source, so `low`), Lake Merced's surface (`low` — topozone
says ~16 ft while SFPUC's own records show a 3-27 ft historic range, and that disagreement is
recorded in the `source` field rather than hidden). **Everything else is
`estimated from surrounding terrain` at `confidence=low`, and says so** — the same standard the
existing table already used for the majority of its rows. Direct USGS 3DEP/EPQS access remains
blocked by this environment's egress proxy, unchanged from fix round 2.

### A defect the review had not seen, found by sampling beyond its list

The review sampled the southeast, the west and the northwest. Sampling the **northeast** while
verifying this round found the same defect with the sign flipped: **Columbus Ave at Union St read
100.2 m.** That is the flat saddle between Telegraph Hill and Russian Hill; the surface had simply
bridged the two ~100 m hilltops because nothing in the valley said otherwise. Aquatic Park, at the
waterline, read 53.6 m. **For a climbing router an invented 85 m hill in North Beach is worse than
a missing one in McLaren Park** — it will route around a climb that does not exist. Ten points
(North Beach valley, Bay St, Aquatic Park, Fisherman's Wharf, Jackson Square, the Broadway tunnel
portal, Polk/Union, Chestnut and Bay at Van Ness, Geary at 20th) fixed it: 15.1 m and 1.0 m now.

### The review's Presidio coordinate was mislabelled

The review lists "Presidio Inspiration Pt 37.7995, -122.4585, real ~95 m". The 0.0 m reading there
was a real defect. The label was not: that coordinate is **870 m north of the actual Inspiration
Point** (Trailforks puts the trailhead at 37.79168, -122.4582) and 157 m from the Main Post parade
ground, down on the terrace above Crissy Field where ~30 m, not ~95 m, is the real ground. Both
locations are now sampled under their true names — 16.0 m at the Main Post, 87.1 m at Inspiration
Point — rather than carrying the mislabel forward as a target to hit.

### The LOO sweep winner moved, for the first time

Re-run over all 216 control points, same 16-combination grid, same scoring:

| smoothing | neighbors | median LOO (m) | p90 LOO (m) |
|---:|---:|---:|---:|
| **0.0** | **global** | **16.39** | **72.71** |
| 0.0 | 10 | 18.50 | 74.54 |
| 0.0 | 20 | 17.36 | 72.67 |
| 0.0 | 40 | 17.13 | 72.59 |
| 0.1 / 0.5 / 2.0 | (same pattern) | (within 0.05 m of the 0.0 row at each `neighbors`) | |

**Winner: `smoothing=0.0, neighbors=None` (a single global fit)**, replacing
`smoothing=2.0, neighbors=20`. `neighbors=20` won at 105 and 118 points because the control set
was too sparse for a global fit to be well conditioned. At 216 points — with the southeast, the
west, the Presidio, the northeast and a 20-point water ring all represented — the global fit has
data everywhere and no longer extrapolates: p90 LOO error falls from 92.24 m to 72.71 m. The
smoothing tie persists for the third time and for the reason already recorded: in this module's
local-metres frame the thin-plate kernel runs ~1e4-1e8, so an additive term of 0-2 on the diagonal
is noise. `0.0` is the literal argmin of that tie, not a claim.

Median LOO error rose, 13.80 m -> 16.39 m. That is expected and is not a regression in the DEM:
the 98 new points are mostly `low`-confidence terrain estimates in districts that previously had
*no* data at all, so the cross-validation now scores the model on hard, sparse ground it was never
asked about before. The tolerances (±25 m / ±40 m) are unchanged — they are settled by the fix
round 3 ruling and were not re-derived to accommodate this number, though it is worth noting they
are now 1.5x and 2.4x the median rather than 1.8x and 2.9x.

### The in-hull clamp statistic had to be repaired before it could be reported

Adding water anchors stretches the convex hull of *all* control points out across the Bay and the
Pacific, nearly doubling the in-hull pixel count (970k -> 1.88M) with water — where a surface that
dips a hair below zero and clamps to 0 is doing exactly the right thing. Left as-is the statistic
reads **21.24%** and would look like a large regression while actually measuring a different
question. `ClampStats` now computes the hull over **land control points only** (`ele_m > 0`),
which is what the number was always supposed to mean: "clamped despite being surrounded by land
control data".

| | in-hull (land) clamped | total clamped |
|---|---:|---:|
| Fix round 2 (118 points) | 7.40% | 39.77% |
| **Fix round 4 (216 points)** | **3.35%** | **30.99%** |

### Minimum separation between control and holdout points (50 m)

`load_control_points` previously rejected only *exact* duplicate coordinates. A control point 1 m
from a holdout passed that check and would have silently made that holdout's accuracy check
tautological — the defect class this dataset was already cleaned of once, and one that gets easy
to reintroduce by accident when the table grows by 98 rows in a single round. Validation now fails
the load if any `control` point is within `MIN_CONTROL_HOLDOUT_SEPARATION_M = 50.0` m (true
haversine distance, not the module's local equirectangular approximation) of any `holdout`,
naming both points and telling the reader to move the control point, never the holdout. 50 m sits
below the tightest genuine separation in the committed table (65.4 m) and well above the 10 m
raster cell, so a violation means someone placed a point *on* a holdout rather than merely near
one.

### Three tests that could not fail, replaced

- `test_fixture_dem_values_are_within_clamp_bounds` asserted `min >= 0 and max <= 300`, which
  `np.clip(z, 0, 300)` guarantees unconditionally. Replaced by
  `test_every_district_reads_as_san_francisco`, which samples `DISTRICT_COVERAGE_SAMPLES` — 27
  locations across all four quadrants plus open water on both sides — and is the gate that the
  defect above could not have passed. Plus `test_district_coverage_table_spans_the_whole_city`,
  which guards the guard: the table must keep at least three land samples per bbox quadrant and
  water samples on both the Bay and Pacific side, so it cannot quietly shrink back toward the
  central corridor and reintroduce the blind spot while still passing.
- `test_holdout_tolerance_assignment_matches_the_ruling` re-implemented the exact
  `name in SUMMIT_HOLDOUT_NAMES ? 40 : 25` branch it was testing, and
  `test_summit_holdout_set_is_exactly_three_named_points` restated the frozenset's own literal
  contents. Both would have followed any edit to the code they guarded. Replaced by one golden
  table, `test_each_named_holdout_gets_the_tolerance_the_ruling_gave_it`, which writes out all ten
  holdout names and their tolerances by hand; editing the summit set, either constant, or the
  holdout roster now fails and has to be re-decided deliberately.

**The district bands are wide on purpose and are a regression guard, not evidence of accuracy.**
Most of those locations now have a control point within a few hundred metres — that is the fix —
so passing them proves the districts are represented, not that the elevations are right. The 10
holdouts remain the only independent check, and the 3DEP run remains the only accuracy gate.

### Two inflated confidence values downgraded

`Filbert St and Leavenworth St` was `medium` but is derived from a `low`-confidence anchor;
`Corona Heights base` was `medium` off a maps-aggregation source. Both are now `low`, with the
reason written into the `source` field.

### Corona Heights: still 1.1 m inside the gate

Corona Heights summit samples at 119.6 m against 158.5 m actual — **-38.9 m against a ±40 m
tolerance**, a margin of 1.1 m (it was 0.9 m before this round; adding Mount Olympus, a real
169 m local maximum 880 m away, helped slightly). This is reported rather than worked around: no
point was excluded, moved, or revalued to protect it, and the tolerance was not widened. It
remains the fixture DEM's most fragile holdout and a future data change could still push it over.

### Verified, not assumed

Determinism re-proven after every change: two full `make fixtures` runs at 10 m production
resolution give identical `dem_data_sha256` (`c3ee0fde...`) and `dem_file_sha256` (`4b071038...`);
only `generated_at` and the derived `manifest_sha256` differ, as designed. All 10 holdouts pass
the gross-breakage gate. `make verify` green at 93 tests.

**Alternatives rejected:** Keeping `smoothing=2.0, neighbors=20` because it was the incumbent
(rejected — the constants are pinned to the sweep's literal output by
`test_interpolation_parameters_match_cv_sweep_winner`, and the whole point of measurement-driven
selection is that it is allowed to change its mind). Fabricating precise spot elevations for the
~150 street corners with no citable figure (rejected — a fabricated value dressed as sourced is
worse than an honest `low`). Reporting the 21.24% all-hull clamp figure without repairing the
metric (rejected — it would have been true and misleading). Adding Corona Heights protection of
any kind (rejected — explicitly out of bounds, and the fragility is the finding).

---

## Task 6: Smoothing, ascent hysteresis, windowed grade, and profile assembly

**Decided:** `services/api/contour/elevation.py` gains `smooth`, `accumulate_relief`,
`windowed_grades`, and `build_profile`, exactly per the design doc §4.4 / task-6-brief.md
interfaces. `build_profile`'s pipeline is resample -> sample -> smooth -> accumulate -> grade, with
smoothing run exactly once on the raw sampled series and both `accumulate_relief` and
`windowed_grades` consuming that same smoothed array — never smoothed twice, never computed from
two different series.

**Smoothing: Savitzky-Golay over a moving average, and why a test proves it, not just a comment.**
A moving average is a local mean; averaging the samples straddling a genuine hill crest pulls the
computed elevation *down* from the true peak by construction, because every window centred near the
peak includes lower neighbouring points on both sides. Savitzky-Golay instead fits a local
degree-2 polynomial per window and evaluates it at the centre — a real curved crest is well
approximated by a local quadratic, so the fit tracks the peak's true height while still averaging
away the high-frequency component that is DEM sampling noise, not terrain. This is exactly the
failure mode design doc §4.4 names: "a moving average flattens genuine hill crests, which is
exactly the error that makes a router think it can cross Nob Hill cheaply." The window is
`SMOOTH_WINDOW_SAMPLES = 9` samples = 90 m at `PROFILE_SAMPLE_M` (10 m) spacing, chosen to sit just
under the ~150 m length of the shortest real SF grade change (a single block), so genuine
block-scale relief survives the filter and sub-block DEM noise does not.
`test_moving_average_loses_materially_more_peak_amplitude_than_savgol`
(`tests/test_elevation_profile.py`) builds a 20 m crest over a 300 m span (well above the 90 m
window, so the crest itself isn't the thing being tested for survival — its *amplitude* through the
filter is) and asserts a same-window moving average's amplitude loss is more than 1.5x
Savitzky-Golay's own loss on the identical input; `test_smoothing_preserves_peak_amplitude` pins the
brief's own ≥90%-of-amplitude requirement for Savitzky-Golay directly. Both passed on the first
implementation with no parameter tuning needed beyond the constants already fixed in
`constants.py`.

Short routes (fewer samples than the 9-sample window) short-circuit `smooth` to an identity copy —
`scipy.signal.savgol_filter` raises `ValueError` for `window_length > len(x)`, and a route shorter
than 90 m is a real input (a single short block, a snapped walk-up), not a degenerate one to reject.
`test_build_profile_short_route_does_not_crash_on_savgol_window` pins this directly against
`build_profile`, not just `smooth` in isolation, since `build_profile` is what a caller actually
invokes.

**`scipy.signal` needed a narrowly-scoped mypy override, no new dependency.** `scipy` (already a
project dependency) ships no `py.typed` marker. The matching PyPI stub package (`scipy-stubs`,
latest 1.17.1.5) trails the installed `scipy` (1.18.0) and, per Global Constraints, adding a new
dependency needs its own recorded justification that a two-line mypy override doesn't. Added
`[[tool.mypy.overrides]] module = "scipy.signal"` to `pyproject.toml`, narrowly scoped to the one
submodule `contour/` imports — same pattern Task 4 used for `rasterio.*` and Task 4 used again for
`make_fixture_dem`.

**Ascent hysteresis: peak-valley detection with an explicit "undetermined" phase, not just up/down.**
Design doc §4.4's pseudocode states the up/down tracking rule but doesn't spell out how the very
first leg's direction gets decided — and a naive choice there breaks one of the brief's own pinned
tests. Concretely: if the algorithm commits to a direction (e.g. "up") based on the very first
observed step and initializes its running extreme/pivot to `ele[0]`, then a 100-sample series
oscillating ±0.4 m around a flat baseline (well under `MIN_RISE_M = 1.0`) ends the loop still
notionally "in an up leg" whose committed magnitude is the *span between the first sample and
whichever oscillation extreme happened to be reached* (0.8 m here) — because "commit the final open
leg" fires unconditionally at the end, per the design doc's own pseudocode, and there was never a
confirmed reversal to reset the pivot. 0.8 m fails
`test_ascent_ignores_subthreshold_noise`'s `pytest.approx(0.0, abs=0.5)`.

The fix: track direction as a three-state value — `0` (undetermined), `1` (up), `-1` (down) — and
while undetermined, track a running high *and* a running low simultaneously against the anchor
(`ele[0]`), only committing to a direction once one of them has moved `>= min_rise_m` away from the
anchor. Until that happens nothing is committed at all, including at the end of the array: a series
that never moves more than `min_rise_m` from its start in either direction ends undetermined, and
both `ascent_m` and `descent_m` stay exactly `0.0`. This is what makes sub-threshold noise report
zero instead of crediting whatever small drift happened to be visible when sampling stopped. Once a
direction is confirmed, the algorithm is exactly the design doc's stated rule: track the extreme in
that direction, commit the completed leg (extreme minus the last pivot) when the series reverses by
more than `min_rise_m`, flip direction, and repeat; the final open leg always commits.
`test_ascent_accumulates_long_shallow_climb` (40 steps of +0.9 m, well past `min_rise_m` by the
second sample) and `test_descent_is_reported_separately_and_never_credits` (an up-ramp then a
symmetric down-ramp) both pass unchanged by this refinement — the undetermined phase only matters
for series that never move far enough to leave it.

**The reversal-commit boundary is inclusive: exactly `min_rise_m` commits.** Design doc §4.4's
pseudocode is written with a strict `<` ("`if ele < extreme - MIN_RISE_M`"), which — read literally
— would mean a reversal of *exactly* `MIN_RISE_M` does **not** commit, only a reversal strictly
greater than it does. Implemented instead with an inclusive `>=` (equivalently, `x <= extreme -
min_rise_m` to trigger a down-commit from an up leg), so a reversal of exactly `MIN_RISE_M` commits.
This was a deliberate choice, not an oversight, made because it is the more useful boundary to pin:
`test_ascent_hysteresis_commits_at_exact_threshold_boundary` constructs a climb-dip-climb profile
with a dip of exactly `MIN_RISE_M` (total ascent 21 m across two committed legs, descent 1 m) against
the same shape with a dip of `MIN_RISE_M - 0.01` (the dip never confirms; the whole thing reads as
one continuous 20 m climb, zero descent) — the two cases differ by exactly the dip's own magnitude,
which is a clean, legible pin of "which side of the boundary commits" per the task instructions.
Flagging the discrepancy from the design doc's literal `<` rather than silently choosing one
reading: **if `<` was intended literally, this is a one-line change** (`>=` -> `>` at both commit
sites), and no currently-listed test distinguishes the two except the boundary test above, which
would need its expected values swapped (21/1 -> whatever the strict-`<` reading produces for the
same input — the "exactly `MIN_RISE_M`" case would fall through to the *next* sample instead).

**Round-trip invariant.** `test_round_trip_ascent_approx_equals_descent` runs a real fixture-graph
path (Portola Drive -> Twin Peaks Boulevard over the summit -> Clarendon Avenue) and its exact
reverse through `build_profile`, and asserts `|ascent - descent| <= 3 * MIN_RISE_M`. Measured:
ascent 386.73 m, descent 386.68 m, difference 0.05 m — far inside the 3 m budget, which was sized to
absorb hysteresis edge effects at the geometry's start/end rather than tuned to the observed result.

**Grade: centred-difference window shrinks at the array edges instead of padding.** Implemented with
`np.clip` on the left/right window indices independently, so a query near either end of the array
naturally gets a smaller, one-sided baseline (e.g. the very first sample uses `[0, half]` instead of
the full `[-half, +half]`) rather than a padded/reflected value that isn't a real measurement.
`test_windowed_grades_edges_shrink_not_pad` pins this against a profile where zero-padding would
visibly change the edge grade values. `max_grade_pct` in `build_profile` is
`max(abs(windowed_grades))` — the peak magnitude of the windowed series, in either direction (a
steep descent is exactly as much a steepness-honesty concern as a steep climb), matching the design
doc's "not driven by a single bad pixel" requirement.

**Known imprecision, accepted deliberately.** `windowed_grades(ele, spacing_m, window)`'s signature
(fixed by the brief, consumed exactly by Task 7/12) takes a single scalar `spacing_m`, not a
per-sample distance array — so `build_profile` calls it with the nominal `PROFILE_SAMPLE_M`, even
though `resample_polyline`'s own final sample can land slightly short of that nominal spacing (it
always lands exactly on the route's final coordinate; see Task 5's decisions). This only perturbs
the grade estimate in the last `window // 2` samples of a route by the same small amount
`resample_polyline` already documents (well under a metre for realistic route lengths), not a
full baseline's worth of error, and keeping the exact locked interface was judged more important
than a marginal edge-case precision gain that would require changing a signature two downstream
tasks depend on.

**Holdout-landmark tolerance superseded, not re-litigated.** The brief's Step 1 test list states
`test_holdout_landmarks_within_tolerance` at "±12 m" verbatim. That figure predates Task 4's own
measured revision (`docs/DECISIONS.md` "Task 4, fix round 1"/"fix round 3", design doc §2.3): the
interpolator's own median leave-one-out error is 13.80 m, so a ±12 m gate sits *below* the model's
demonstrated noise floor and cannot be met by any parameter choice — Task 4 replaced it with ±25 m
(non-summit) / ±40 m (summit: Twin Peaks, Bernal Heights, Corona Heights), which
`tests/test_fixture_dem.py` already gates on. Implemented `test_holdout_landmarks_within_tolerance`
in `tests/test_elevation_profile.py` against the superseding ±25/±40 m tolerances instead of the
brief's stale ±12 m, importing `HOLDOUT_TOLERANCE_M`/`SUMMIT_HOLDOUT_TOLERANCE_M` directly from
`scripts/make_fixture_dem.py` (same `sys.path` pattern `test_fixture_dem.py` already uses) rather
than duplicating the numbers as literals, so this file cannot silently drift from the measured
ruling. This test exercises a different code path than `test_fixture_dem.py`'s own holdout gate —
`DemSampler`'s hand-rolled bilinear sampling against the committed fixture DEM file, not the DEM
generator's internal nearest-neighbour array indexing — so it validates the Task 5/6 sampling
machinery specifically, per the brief's own note that this is not a DEM-accuracy gate. All 7 named
holdouts pass.

**Bernal Heights summit is neither gated nor reported here — deliberately, following the brief
literally.** The brief's gating list names exactly 7 holdouts and its non-gating list names exactly
2 (Ocean Beach at Judah, Lands End), leaving Bernal Heights summit (a real holdout with an
independent-but-uncertain source: "Wikipedia, sources vary 433-475 ft") out of both lists. Rather
than guess which tier the brief intended for it, this task tests exactly the 9 holdouts the brief
names and no more — Bernal Heights summit remains covered by `test_fixture_dem.py`'s own
nearest-neighbour holdout gate (which does test all 10), just not re-validated here through
`DemSampler`. Flagged in the task report rather than silently deciding a tier for it.

**Real SF profile, measured, not asserted to a golden value.** `build_profile` run over a real,
graph-connected 15-vertex path (Portola Drive -> Twin Peaks Boulevard, climbing to the saddle near
the true Twin Peaks summit -> Clarendon Avenue descending the far side) against the committed
fixture DEM produces `ascent_m = 258.2`, `descent_m = 129.2`, `max_grade_pct = 37.9`. Twin Peaks'
real summit is 281 m (a holdout, per the control-point CSV); this route's vertices don't reach the
exact summit coordinate, so an ascent somewhat below that full relief is physically expected. The
committed test (`test_build_profile_on_real_sf_path_is_physically_plausible`) asserts a wide sanity
band (100-350 m ascent, 20-200 m descent, ascent > descent, 0 < max grade <= 40%) rather than the
measured point value, consistent with the brief's "not a precise value — a sanity band" instruction
and with design doc §2.3's repeated point that this fixture DEM cannot certify accuracy at any
tight tolerance. `max_grade_pct` (37.9%) is high for a real SF street but not implausible for this
specific synthetic DEM, whose known limitation (§2.3: "effective resolution is far coarser than its
10 m grid... except where control points are locally dense") can produce locally sharp
interpolation transitions near a sparsely-controlled summit approach; the ceiling was set generously
(40%) rather than tightened to the observed value, so the test doesn't silently become a golden-value
assertion in disguise.

**Alternatives rejected:** Initializing the hysteresis algorithm's direction from the first observed
step, with the pivot fixed at `ele[0]` from the start (rejected — produces a spurious "free" leg
equal to the anchor-to-first-extreme span whenever the series ends without ever confirming a
reversal, which is exactly `test_ascent_ignores_subthreshold_noise`'s failure mode; see above).
Implementing the design doc's `<` boundary literally (rejected in favor of an inclusive `>=` — see
above; flagged, not silently overridden). Vectorizing `accumulate_relief` with numpy (considered —
the algorithm is inherently sequential/stateful, unlike `DemSampler.sample`'s independent
per-point work, so a Python loop over the resampled series, typically hundreds to low thousands of
points per route, is the direct implementation rather than a numpy trick that would obscure the
state machine). Computing `windowed_grades` from the true per-sample cumulative-distance array
instead of a scalar `spacing_m` (rejected — the interface is locked for Task 7/Task 12; documented
as a known, small, deliberately-accepted imprecision above rather than a silent deviation from the
brief's signature).

## Task 8: Real OSM/3DEP data pipeline (written, never executed)

**Status: written and unit-tested, deliberately never run.** Every claim below about what this
pipeline *would* do is a claim about code that has never touched the network in this environment.
The banner at the top of `scripts/clip_osm.py`, `scripts/fetch_3dep.py`, `scripts/net_fetch.py`,
`services/routing-engine/Dockerfile`, and `services/routing-engine/config/graphhopper.yml` says so
verbatim: *"Written against documentation verified 2026-08-09; NEVER EXECUTED. The first real run
should expect to debug it. Verify tile availability, checksum behaviour, and GraphHopper graph-build
memory before trusting output."*

**GraphHopper version and config keys targeted (SPEC.md §9.5 requirement).** GraphHopper **11.0**
(released 2025-10-14), verified via `github.com/graphhopper/graphhopper` releases + `pom.xml` on
2026-08-09 — `.research/API_FACTS.md` pinned-versions table. Config keys, all copied from that same
file (§1-2), which itself cites GraphHopper's own source
(`core/src/main/java/com/graphhopper/reader/dem/SkadiProvider.java`,
`core/src/main/java/com/graphhopper/GraphHopper.java`) and shipped resources
(`core/src/main/resources/com/graphhopper/custom_models/bike.json`,
`.../bike_elevation.json`), dated 2026-08-09:
- `graph.elevation.provider: skadi` — SPEC.md §4.3 step 3 explicitly named this as needing
  verification before use ("verify this against the current GraphHopper release before committing
  to it"); it is verified live in the 11.0/master source even though the hosted prose docs
  (`docs/core/elevation.md`) omit `skadi` from their provider list entirely — a docs/code drift
  API_FACTS.md flags by name. No fallback (pre-tagging OSM `ele` nodes, or a custom
  `ElevationProvider`) was needed.
- `graph.elevation.cache_dir`, `graph.elevation.dataaccess: RAM`, `graph.elevation.interpolate:
  bilinear`, `graph.elevation.edge_smoothing: ramer`, `graph.elevation.long_edge_sampling_distance:
  60`, `graph.elevation.way_point_max_distance: 10` — verified in GraphHopper's own
  `config-example.yml`.
- `graph.encoded_values` and `profiles[].custom_model_files` — copied from the exact code comment
  shipped with `bike.json` itself, substituting `srtm` -> `skadi` and adding `max_slope` (flagged
  inline in `graphhopper.yml` as the one line-item not present in that verbatim comment, added
  because design doc §5.2 names both `average_slope` and `max_slope` as the reason GraphHopper was
  chosen over a generic router).
- Per-request `custom_model` + `"ch.disable": true` — verified from GraphHopper's own
  `RouteResourceCustomModelTest.java`; documented in `graphhopper.yml` as the reason no CH profile
  is declared (a per-request slope-weighted `custom_model`, which Stage A needs per candidate, is
  incompatible with CH's single fixed weighting).

**UNVERIFIED items touched, and how each was handled** (cross-referencing `.research/API_FACTS.md`'s
own numbered RISKS/UNVERIFIED list):
1. **#2 (elevation-provider docs/code drift):** not a risk to route around — it is the resolution.
   Trusted the source (`GraphHopper.java`'s provider switch) over the prose docs, exactly as
   API_FACTS.md itself recommends.
2. **#7 (3DEP GeoTIFFs being true COGs, unverified):** not load-bearing. `fetch_3dep.py` reads
   source tiles with `rasterio`/GDAL, which handles COG and plain GeoTIFF identically; if the source
   tiles turn out not to be COGs, the only effect is losing an unattempted read-directly-from-S3
   optimization, not correctness.
3. Two additional gaps **beyond** API_FACTS.md's own 7-item list surfaced while writing this task,
   both flagged inline at the point they matter rather than silently assumed:
   - **Geofabrik's NorCal extract URL** (`scripts/clip_osm.py` module docstring) is not covered by
     API_FACTS.md at all — Global Constraints names GraphHopper/Valhalla/MapLibre RN/Expo/pmtiles/
     3DEP as the APIs that file verifies; Geofabrik isn't one of them. The URL used
     (`download.geofabrik.de/north-america/us/california/norcal-latest.osm.pbf`) is Geofabrik's
     long-standing public path convention, not independently re-verified this session. Same
     honesty standard applied to the MD5-sidecar convention (`<file>.md5`) and to the Valhalla
     Docker image reference in `docker-compose.yml` (`ghcr.io/valhalla/valhalla:latest` — Valhalla's
     costing options and config *keys* are verified in API_FACTS.md §3, its Docker image name is
     not).
   - **GraphHopper `SkadiProvider`'s exact local-cache-hit behaviour when `cache_dir` is
     pre-seeded** (does it prefer an already-present same-named file over a network fetch, gzipped
     or raw, flat or nested under a lat-prefixed subdirectory?) is not derived from source in this
     session — API_FACTS.md §2 quotes `SkadiProvider`'s *download* URL and cache-filename
     convention (`"Local cache filename is the same string lower-cased (n37w123)"`) but not
     `AbstractSRTMElevationProvider`'s cache-hit code path line-by-line. `fetch_3dep.py::
     write_skadi_tile` follows the one directly-quoted convention (lower-cased, flat, `.hgt.gz`)
     as its best-effort implementation, and `graphhopper.yml` flags the gap explicitly right next
     to `graph.elevation.cache_dir` so it's the first thing checked if graph build tries to hit the
     network for elevation despite a pre-seeded cache.

**Manifest shape — same keys as the fixture manifest, real-appropriate values, not a schema
fork.** Task-8-brief: *"match it — same keys, same hashing approach — so `/v1/health` and the eval
harness work unchanged against either."* `fetch_3dep.py::_manifest_dict` emits every key
`scripts/make_fixture_dem.py::write_manifest` does (pinned by
`test_manifest_key_set_matches_the_fixture_manifest_shape`), with the four fixture-specific
interpolation-provenance fields given their honest real-pipeline value rather than omitted:
`clamped_pixel_count`/`in_hull_clamped_pixel_count` are always `0` (a real 3DEP mosaic has no RBF
extrapolation step to clamp), `in_hull_pixel_count` equals `total_pixel_count` (no "convex hull of
sparse control points" concept applies to directly-surveyed data), and
`interpolation_smoothing`/`interpolation_neighbors` are always `null` (no RBF fit happens).
`control_point_sha256`/`control_point_count` are repurposed to the same *structural* role
("hash + count of what fed the DEM") applied to this pipeline's actual input: the sorted set of
3DEP tile ids and their own file hashes, instead of a control-point CSV. Two additive keys not in
the fixture manifest (`source_tiles`, `source_tile_urls`) carry real-only provenance; adding keys
is safe for any consumer reading by name, never a subset of the fixture's shape. `"source": "real"`
is hardcoded, never derived, so it can't silently drift to match `"fixture"`.

**Alternatives considered:**
- *Folding manifest-writing into a shared module with `make_fixture_dem.py`* (rejected — that
  script is fixture-only tooling per its own docstring; this real pipeline should not import it or
  be imported by it, so the manifest-dict-building logic is duplicated in shape but independent in
  code, same pattern as `write_cog`).
- *`pyosmium` instead of shelling out to the `osmium` CLI* (rejected — adds a new Python dependency
  needing its own justification here; `osmium-tool` is already the natural fit baked into
  `services/routing-engine/Dockerfile`'s image, so `clip_osm.py` shells out to it and no Python
  dependency is added).
- *A single combined script instead of `clip_osm.py` + `fetch_3dep.py` + shared `net_fetch.py`*
  (rejected — the brief names two separate scripts; `net_fetch.py` was added, not in the brief's
  file list, purely to avoid duplicating the conditional-request/resume/rate-limit logic both need
  identically — this is implementation detail behind the two named entry points, not new scope).
- *Making `make data` a no-op/print-only stub, matching how it read before this task* (rejected —
  the task is to *write* the real pipeline, not to describe it; `make data` now genuinely invokes
  `clip_osm.py`/`fetch_3dep.py`, still gated by the fact that nobody runs `make data` in this
  environment, per the global constraint that fixtures are the only data path CI or `make verify`
  ever touches).
- *Fully containerizing `clip_osm.py`/`fetch_3dep.py` themselves* (SPEC.md §4.3's "all
  containerized" framing) — considered and scoped down: `osmium-tool` lives in the
  `routing-engine` image so its binary is available somewhere, but the two Python scripts still run
  via `uv run` against `services/api`'s own environment (they need `contour.constants`/
  `contour.errors` and the project's `rasterio`/`httpx`), documented as a host-level prerequisite in
  the `Makefile`'s `data` target comment rather than built out as its own Docker image — a fully
  containerized data-prep step felt like scope the never-executed nature of this task doesn't
  justify building blind.

**TDD evidence.** `services/api/tests/test_data_scripts.py`, 26 tests, all written and run red
(`ModuleNotFoundError` before `scripts/net_fetch.py`/`clip_osm.py`/`fetch_3dep.py` existed) then
green: 3DEP tile-name computation for `SF_BBOX` (`n38w123`, matching API_FACTS.md §7's directly
verified live-bucket-listing example) and three bbox-spanning-a-boundary cases (longitude,
latitude, and both at once — one, two, two, four tiles respectively); the 3DEP<->Skadi tile-name
conversion against the one directly-quoted example (`n38w123` <-> `N37W123`); manifest-checksum
stability (identical inputs -> identical `manifest_sha256`, changing one input changes it, the key
set matches the fixture manifest, the clamp/interpolation fields read their honest real-pipeline
value); the bbox out-of-service-area guard (`validate_service_area_bbox` raises `OutOfServiceArea`
with code `OUT_OF_SERVICE_AREA` for a bbox outside SF and for one that only partially exceeds it);
and the conditional/resume download control flow against a stub `Fetcher` — 304 uses the cache
untouched and sends the cached `If-None-Match`/`If-Modified-Since`, 200 overwrites and re-caches,
a fresh 206 append-and-rename completes the file (proven by asserting the final bytes are
`partial + new`, not just `new`), a `Content-Range`-reported incomplete resume stays a `.partial`
file rather than being prematurely renamed, and both a 304-with-no-cached-file and an unsolicited
206 raise `UnexpectedStatusError`. No test opens a socket — every network-shaped input is a
pre-programmed `StubFetcher` implementing `net_fetch.Fetcher` structurally. `make verify`: 139
tests green (113 pre-existing + 26 new), ruff/ruff-format/mypy strict clean across
`services/api/`. `scripts/*.py` are outside `make verify`'s scope (same as the pre-existing
`make_fixture_dem.py`/`cv_sweep.py`), so they were additionally checked by hand:
`ruff check`/`ruff format --check` clean and `mypy --strict` clean (with `MYPYPATH` pointed at
`services/api` so `contour.*` resolved instead of falling back to `Any`).

**Data manifest / gitignore:** no change needed — `data/**` is already gitignored except
`data/manifest.json` and `data/fixtures/**`, so the new `data/raw/`, `data/osm/`, `data/skadi/`
directories these scripts would create are covered by the existing pattern. The committed
`data/manifest.json` is untouched by this task (still `"source": "fixture"`) — writing to it would
require actually running `fetch_3dep.py`, which this task deliberately does not do.

## Task 8, fix round 1: bounded multi-round resume; corrupt-checksum cleanup

Review finding: `fetch_3dep.py::download_tiles` hashed `dest` unconditionally rather than the
actually-returned path, crashing with a confusing `FileNotFoundError` on a `RESUMED_INCOMPLETE`
outcome (only `.partial` exists then). Underneath that: neither `download_tiles` nor
`clip_osm.py::download_norcal_extract` ever called the download function more than once, so a
resume that didn't finish in a single further request — the expected case on a ~1 GB extract or
~213 MB tile, not an edge case — had no way to actually complete. Resume support that cannot resume
implies a capability that isn't there.

**Fix.** Added `net_fetch.download_until_complete(fetcher, url, dest_path, *, max_attempts=5,
backoff_s, sleep)`: loops the existing single-shot `conditional_resumable_download`, retrying only
on `RESUMED_INCOMPLETE` with exponential backoff (capped at 30s), and raises `ResumeIncompleteError`
— naming the file, bytes received, and attempt count — once `max_attempts` is exhausted, leaving the
`.partial` file in place so a later invocation continues it. Both orchestration functions now route
through this instead of the single-shot call, and both expose `max_attempts`/`sleep` as parameters
(the latter purely so tests can avoid a real backoff delay). `download_tiles` now hashes
`result.path`, never the pre-download `dest`.

**Second bug found while fixing the first, not by the review:** `clip_osm.py::_verify_checksum`
raised on a mismatch but left the corrupt file on disk under its normal name, with a cache-meta
sidecar already written (the download function writes it on every `200`/`206`, before checksum
verification runs). A later run would see the file present, send its cached `ETag`, get a `304`
from an unchanged remote, and trust the corrupt bytes forever — a silently-corrupted ~1 GB extract
would present as a routing bug, not a data bug. Fixed: on mismatch, both the bad file and its
`.meta.json` are deleted before raising.

**Alternatives considered:** capping resume attempts by elapsed wall-clock time instead of a count
(rejected — a count is simpler to reason about and to drive from a test; nothing in the brief or
finding asked for a time budget). Making the retry loop live inside
`conditional_resumable_download` itself rather than a separate `download_until_complete` (rejected
— `conditional_resumable_download`'s single-request-per-call contract is exactly what its own
existing tests pin, and callers that only ever expect one round — none currently, but the shape is
worth keeping — shouldn't have to opt out of retrying).

**Coverage.** Added 4 orchestration-level tests (`test_data_scripts.py`, 26 -> 30) that exercise
`download_tiles`/`download_norcal_extract` directly: a genuinely multi-round resume completing
across two `206` responses; exhausting `max_attempts` on a resume that never completes (asserts the
error names the file/attempt count and the `.partial` file survives with the right bytes); a `304`
using the cache without re-fetching the MD5 sidecar; and a checksum mismatch leaving neither the bad
file nor its cache meta behind. Verified these are behavioural (not tautological) by temporarily
stashing just `scripts/fetch_3dep.py` back to its pre-fix state and confirming the `download_tiles`
tests fail against it, then restoring the fix.

**Minor:** pinned the Valhalla `docker-compose.yml` image tag `latest` -> `3.8.3` (matching
API_FACTS.md's verified-current Valhalla release), flagged inline that the exact tag string wasn't
independently re-verified. Copied API_FACTS.md RISK #8 (Valhalla `use_hills` needing
`additional_data.elevation` at tile-build time, "strongly implied, not verbatim-confirmed") into a
`docker-compose.yml` comment next to the flag it concerns, since that risk previously existed only
in a file nobody editing compose would be reading.
