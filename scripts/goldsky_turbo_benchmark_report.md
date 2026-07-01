# Goldsky Turbo Benchmark Report

Generated: 2026-06-23T23:37:12.075Z

**Two timings** (the benchmark mixes cloud and local indexers, measured differently):
- **Wall-clock** = `apply` → last row, **including cloud provisioning**. Compare against other
  **cloud** tools (Sentio, The Graph Subgraph), which include their deploy/upload the same way.
- **Indexing** = exact DB-side `MAX-MIN(inserted_at)` (first write → last write, both on the sink's
  clock — skew-free, provisioning excluded). Compare against **local** tools (Envio, Ponder,
  Subsquid), whose published times are spawn→done with negligible provisioning.

The **Image** column is the `config_overrides.image_tag` used (`default` = stock image); override
runs accumulate beside the baseline for comparison.

| Case | Iter | Wall-clock (s) | Wall-clock (min) | Indexing (s) | Indexing (min) | Idx Blocks/s | Records | Expected | Complete | Error |
|-------|------|----------------|------------------|--------------|----------------|--------------|---------|----------|----------|-------|
| case1  | 1 | 200.2 | 3.34 | 21.8 | 0.36 | 1018242 | 294278 | 294278 | ✅ | - |
| case2  | 1 | 89.1 | 1.49 | 34.7 | 0.58 | 2884 | 7634 | 7634 | ✅ | - |
| case3  | 1 | 32.5 | 0.54 | 6.0 | 0.10 | 16582 | 100001 | 100001 | ✅ | - |
| case4  | 1 | 190.8 | 3.18 | 148.7 | 2.48 | 67 | 1696641 | 1696641 | ✅ | - |
| case5  | 1 | 44.9 | 0.75 | 4.6 | 0.08 | 19649 | 50191 | 50191 | ✅ | - |
| case6  | 1 | 13.0 | 0.22 | 2.8 | 0.05 | 3586 | 35039 | 35039 | ✅ | - |
