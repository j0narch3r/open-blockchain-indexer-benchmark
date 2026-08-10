.PHONY: data fixtures dev test eval verify ship

API_DIR := services/api

# Fetches the real OSM extract + 3DEP elevation tiles for San Francisco and
# builds the real DEM + Skadi tiles + manifest.json (source: "real").
# Implements SPEC.md §4.3 steps 1-3 and 5 (step 4, the routing graph build,
# happens when the `routing-engine` container starts — `docker compose up
# routing-engine` after this target, per docker-compose.yml).
#
# WRITTEN BUT NEVER EXECUTED IN THIS ENVIRONMENT (task-8-brief.md; see the
# banner atop each script and docs/DECISIONS.md "Task 8"). This is a target
# a human invokes deliberately — it is NOT part of `make verify` and never
# will be. Expect to debug the first real run: verify tile availability,
# Geofabrik's current URL (scripts/clip_osm.py's module docstring flags
# this specifically), and checksum behaviour before trusting the output.
#
# Prerequisites this target does NOT install: the `osmium` CLI
# (`osmium-tool`) must be on PATH for `clip_osm.py`'s `osmium extract` step
# — it is installed in `services/routing-engine/Dockerfile`'s image but not
# on the bare host; run `clip_osm.py` inside that container (or install
# `osmium-tool` locally) if this fails with "osmium: command not found".
data:
	cd $(API_DIR) && uv run python ../../scripts/clip_osm.py
	cd $(API_DIR) && uv run python ../../scripts/fetch_3dep.py
	@echo "make data: OSM clip + 3DEP DEM + Skadi tiles + manifest.json written."
	@echo "Next: 'docker compose up routing-engine' to build the GraphHopper graph (SPEC.md §4.3 step 4)."

# Generates the fixture DEM + graph used by tests and CI.
# The DEM generator (Task 4) is real; the fixture graph generator lands in
# a later task, so this target does not yet produce everything `make data`
# eventually will.
fixtures:
	cd $(API_DIR) && uv run python ../../scripts/make_fixture_dem.py

# Runs the API dev server with autoreload.
dev:
	cd $(API_DIR) && uv run uvicorn contour.api:app --reload --port 8000

# Runs the Python test suite.
test:
	cd $(API_DIR) && uv run pytest

# Eval harness stub — becomes real in Task 14. Not part of `make verify`.
eval:
	@echo "eval harness lands in Task 14"

# The gate every task must pass before committing.
verify:
	cd $(API_DIR) && uv run ruff check .
	cd $(API_DIR) && uv run ruff format --check .
	cd $(API_DIR) && uv run mypy .
	cd $(API_DIR) && uv run pytest

# Builds and deploys the docker compose stack to the target host.
ship:
	@echo "make ship: deploy pipeline lands in a later task."
