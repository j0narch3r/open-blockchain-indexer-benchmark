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
