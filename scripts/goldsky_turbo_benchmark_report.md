# Goldsky Turbo Benchmark Report

Generated: 2026-07-15T16:07:56.836Z

**Two timings** (the benchmark mixes cloud and local indexers, measured differently):
- **Wall-clock** = `apply` → last row, **including cloud provisioning**. Compare against other
  **cloud** tools (Sentio, The Graph Subgraph), which include their deploy/upload the same way.
- **Indexing** = exact DB-side `MAX-MIN(inserted_at)` (first write → last write, both on the sink's
  clock — skew-free, provisioning excluded). Compare against **local** tools (Envio, Ponder,
  Subsquid), whose published times are spawn→done with negligible provisioning.

| Case | Iter | Wall-clock (s) | Wall-clock (min) | Indexing (s) | Indexing (min) | Idx Blocks/s | Records | Expected | Complete | Error |
|------|------|----------------|------------------|--------------|----------------|--------------|---------|----------|----------|-------|
| case1 | 1 | 61.3 | 1.02 | 17.1 | 0.28 | 1298246 | 294278 | 294278 | ✅ | - |
| case2 | 1 | 68.4 | 1.14 | 35.5 | 0.59 | 2814 | 7634 | 7634 | ✅ | - |
| case3 | 1 | 34.3 | 0.57 | 6.8 | 0.11 | 14755 | 100001 | 100001 | ✅ | - |
| case4 | 1 | 280.4 | 4.67 | 175.7 | 2.93 | 57 | 1696641 | 1696641 | ✅ | - |
| case5 | 1 | 58.6 | 0.98 | 3.2 | 0.05 | 27800 | 50191 | 50191 | ✅ | - |
| case6 | 1 | 25.1 | 0.42 | 3.7 | 0.06 | 2681 | 35039 | 35039 | ✅ | - |
