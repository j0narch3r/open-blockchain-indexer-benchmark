.PHONY: data fixtures dev test eval verify ship

API_DIR := services/api

# Fetches the real OSM extract + 3DEP elevation tiles for San Francisco.
# Written but never executed in this environment — data is fixtures-only
# here (see docs/DECISIONS.md and global constraints).
data:
	@echo "make data: real OSM/3DEP pipeline is written but not run in this environment."
	@echo "Use 'make fixtures' for the fixture data CI and tests rely on."

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
