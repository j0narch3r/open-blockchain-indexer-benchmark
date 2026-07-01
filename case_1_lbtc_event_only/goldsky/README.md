# Goldsky Implementation — LBTC Event Only Benchmark

This directory contains a **Goldsky** pipeline that indexes every **LBTC Transfer** event
in blocks **0 → 22,200,000**, decoding `from`, `to`, and `value` and writing one row per
transfer to Postgres.

## Prerequisites

* **Goldsky account** on a plan with hosted Postgres. Sign up at
  [app.goldsky.com](https://app.goldsky.com).
* **Goldsky CLI:**
  ```bash
  curl https://goldsky.com | sh
  ```
* **A Postgres sink secret** named `MY_POSTGRES_SECRET`. Provision a Goldsky-hosted Postgres
  (Sinks → New sink → Hosted Postgres) or bring your own:
  ```bash
  goldsky secret create --name MY_POSTGRES_SECRET
  # paste your Postgres connection string when prompted, e.g.
  #   postgresql://user:pass@host:5432/db?sslmode=require
  ```

## Setup & Running Instructions

### 1. Log in
```bash
goldsky login
```

### 2. Validate
```bash
goldsky turbo validate goldsky-case1.yaml
```

### 3. Deploy (run the job)
The pipeline is `job: true`, so it runs over the fixed block range and self-terminates —
giving a clean wall-clock number.
```bash
goldsky turbo apply goldsky-case1.yaml
```

### 4. Monitor & verify
```bash
goldsky turbo monitor goldsky-case1
goldsky turbo logs goldsky-case1 --follow
```
Verify completeness by counting rows in the Postgres sink (see Query Examples).

## Implementation Details

- **Source:** `ethereum.raw_logs` (v1.2.0), filtered at the source to the LBTC contract
  `0x8236a87084f8b84306f72007f36f2618a5634494` and `block_number <= 22200000` — the engine
  fast-seeks only LBTC logs instead of scanning 22.2M blocks.
- **Decode:** a two-stage SQL transform — `_gs_log_decode(<Transfer ABI>, topics, data)` yields a
  `decoded` struct, then a second transform extracts `from`/`to`/`value` and keeps rows where
  `event_signature = 'Transfer'`. `value` is stored as the decoded uint256 string (exact, no
  arithmetic).
- **Sink:** Postgres table `goldsky_case1`, `primary_key: id`.

## Project Structure
- `goldsky-case1.yaml` — the pipeline (source → decode → extract → Postgres sink)

## Query Examples
```sql
SELECT count(*) FROM goldsky_case1;
-- expected: 294,278
```

## Performance Results

Goldsky (cloud, `resource_size: m`)

| Wall-clock | Indexing span | Records |
|---|---|---|
| **3.34 min** | **0.36 min** | 294,278 ✅ |

Against the published indexers (Jan 2026 report):

| Indexer | Time | Records |
|---|---|---|
| **Goldsky** | **3.34 min** | 294,278 ✅ |
| Envio HyperIndex | 6.94 min | 294,278 |
| Sentio | 11.02 min | 294,278 |
| Sentio Subgraph | 14.90 min | 294,278 |
| Ponder | 34.80 min | 294,278 |
| Subsquid | 40.94 min | 294,278 |
| Subgraph (The Graph) | 188.79 min | 294,278 |

## Running via the benchmark harness
To benchmark all cases in one go — deploy, time, count, and generate a report — use the repo's
Turbo runner; see [`scripts/goldsky_turbo_benchmark.ts`](../../scripts/goldsky_turbo_benchmark.ts)
for setup and usage.

## Additional Commands
```bash
goldsky turbo get goldsky-case1 -o yaml    # full definition + status
goldsky turbo list                         # all pipelines in the project
goldsky turbo delete goldsky-case1         # tear down
```

For more on Turbo pipelines, see the [Turbo documentation](https://docs.goldsky.com/turbo-pipelines).
