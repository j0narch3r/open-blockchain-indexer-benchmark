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
