# Backlog

Good ideas that are out of scope for the current task. Append, don't implement.

---

- **Fixture DEM: sharp-summit holdout accuracy.** `scripts/make_fixture_dem.py`'s
  `RBFInterpolator(kernel="thin_plate_spline", smoothing=0.5)` (Task 4's brief-specified
  parameters, kept as-is) systematically undershoots isolated summit holdouts — Twin Peaks
  summit samples 33.5 m low, Bernal Heights summit 21.1 m low, both outside design doc §2.3's
  ±12 m widened landmark tolerance (the other 8 of 10 holdouts are within it). Candidates for a
  future task: revisit `smoothing` (e.g. lower it, trading summit accuracy for more local
  wiggle elsewhere), or special-case peak/summit holdouts with a wider tolerance in Task 5's
  accuracy test. See `docs/DECISIONS.md` Task 4 for the investigation.
- **Fixture DEM: 41% of pixels clamped to `[0, 300]`.** Mostly explained (open Bay/ocean water
  inside `SF_BBOX` with no nearby control points — see `docs/DECISIONS.md` Task 4) but ~7% of
  pixels *inside* the control points' convex hull also clamp, which is more than a single-digit
  edge effect. Worth a second look if the fixture DEM is ever visualized or if in-hull clamped
  pixels turn out to fall on any real street geometry once the fixture graph (a later task)
  exists.
