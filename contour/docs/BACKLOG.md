# Backlog

Good ideas that are out of scope for the current task. Append, don't implement.

---

- **Fixture DEM: sharp-summit holdout accuracy — CONFIRMED not a hyperparameter artifact
  (Task 4, fix round 1).** Originally flagged against the brief's guessed `smoothing=0.5`; a
  leave-one-out cross-validation sweep over `smoothing ∈ {0, 0.1, 0.5, 2.0}` ×
  `neighbors ∈ {None, 10, 20, 40}` (16 combinations, scored on the 105 control points, never the
  holdouts) selected `smoothing=2.0, neighbors=20` as the measured winner, and the two summit
  holdouts are essentially unchanged under it: Twin Peaks summit -33.0 m (was -33.5 m), Bernal
  Heights summit -21.0 m (was -21.1 m), both still outside design doc §2.3's ±12 m tolerance; the
  other 8 of 10 holdouts remain within it. This looks like a genuine data-density limit, not a
  tunable parameter: both summits' nearest control points sit on their slopes, already below the
  peak, so any smooth interpolator fit through slope-only data undershoots the true summit by
  construction — no point in the 105-point control set sits *at* either summit's top (they are
  holdouts specifically because a control point there would make the check circular). Candidates
  for whoever owns Task 5's accuracy test: accept a wider (or summit-specific) tolerance for
  these two, or accept that ±12 m is achievable for 8 of 10 SF landmark holdouts but not sharp,
  sparsely-sampled summits. See `docs/DECISIONS.md` "Task 4, fix round 1" for the full sweep
  table and reasoning.
- **Fixture DEM: ~42% of pixels clamped to `[0, 300]`, ~7.7% even inside the control points'
  convex hull (Task 4, fix round 1).** The `neighbors=20` config selected by the CV sweep above
  did *not* reduce in-hull clamping as hypothesized — it went from 7.27% (old global fit) to
  7.67% (new local fit), i.e. slightly worse. The bulk of total clamping (the other ~35 points)
  remains explained by open Bay/ocean water inside `SF_BBOX` with no nearby control points. Worth
  a second look if the fixture DEM is ever visualized, if in-hull clamped pixels turn out to fall
  on real street geometry once the fixture graph (a later task) exists, or if someone wants to
  investigate why a local fit didn't tame the in-hull oscillation the way a smaller neighborhood
  was expected to.
