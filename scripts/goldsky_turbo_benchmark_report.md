# Goldsky Turbo Benchmark Report

Generated: 2026-07-01T23:25:56.905Z

**Two timings** (the benchmark mixes cloud and local indexers, measured differently):
- **Wall-clock** = `apply` → last row, **including cloud provisioning**. Compare against other
  **cloud** tools (Sentio, The Graph Subgraph), which include their deploy/upload the same way.
- **Indexing** = exact DB-side `MAX-MIN(inserted_at)` (first write → last write, both on the sink's
  clock — skew-free, provisioning excluded). Compare against **local** tools (Envio, Ponder,
  Subsquid), whose published times are spawn→done with negligible provisioning.

| Case | Iter | Wall-clock (s) | Wall-clock (min) | Indexing (s) | Indexing (min) | Idx Blocks/s | Records | Expected | Complete | Error |
|------|------|----------------|------------------|--------------|----------------|--------------|---------|----------|----------|-------|
| case1 | 1 | 82.9 | 1.38 | 25.7 | 0.43 | 865094 | 294278 | 294278 | ✅ | - |
| case2 | 1 | 155.4 | 2.59 | 121.5 | 2.03 | 823 | 7634 | 7634 | ✅ | - |
| case3 | 1 | 19.7 | 0.33 | 6.2 | 0.10 | 16103 | 100001 | 100001 | ✅ | - |
| case4 | 1 | 225.8 | 3.76 | 134.4 | 2.24 | 74 | 1696641 | 1696641 | ✅ | - |
| case5 | 1 | 45.0 | 0.75 | 3.5 | 0.06 | 25865 | 50191 | 50191 | ✅ | - |
| case6 | 1 | 15.2 | 0.25 | 3.4 | 0.06 | 2949 | 35039 | 35039 | ✅ | - |
