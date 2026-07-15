# Goldsky Implementation — Uniswap V2 Template (Factory) Benchmark

This directory contains a **Goldsky** pipeline that reproduces the "factory/template"
pattern for Uniswap V2 over blocks **19,000,000 → 19,010,000**: discover pair contracts created
by the factory, then index `Swap` events emitted by those dynamically-discovered pairs.

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
goldsky turbo validate goldsky-case6.yaml
```

### 3. Deploy (run the job)
```bash
goldsky turbo apply goldsky-case6.yaml
```

### 4. Monitor & verify
```bash
goldsky turbo monitor goldsky-case6
goldsky turbo logs goldsky-case6 --follow
```

## Implementation Details

- **Source:** `ethereum.raw_logs` (v1.2.0), filtered to `19,000,000 ≤ block ≤ 19,010,000`.
- **Pair discovery (dynamic table):** a `dynamic_table` transform captures pair addresses from the
  factory's `PairCreated` events into table `goldsky_case6_pairs`.
- **Swap indexing:** a SQL transform decodes the `Swap` event (`_gs_log_decode`) for logs whose
  `topics LIKE '0xd78ad95f%'` **and** whose contract address passes
  `dynamic_table_check('goldsky_case6_pairs', LOWER(address))` — i.e. only swaps from pairs the
  factory actually created.
- **Sink:** Postgres table `goldsky_case6`, `primary_key: id`.

## Project Structure
- `goldsky-case6.yaml` — the pipeline (source → dynamic pair table + Swap decode → Postgres sink)

## Query Examples
```sql
SELECT count(*) FROM goldsky_case6;
-- expected: 35,039
```

## Performance Results

Goldsky (cloud, `resource_size: m`):

| Time | Time (actual indexing only) | Records |
|---|---|---|
| **0.42 min (25.1s)** | 0.06 min (3.7s) | 35,039 |

Against the published indexers (Jan 2026 report):

| Indexer | Time | Records |
|---|---|---|
| **Goldsky** | **0.42 min (25.1s)** | **35,039** |
| Envio HyperIndex | 1.92 min | 35,039 |
| Sentio Subgraph | 4.26 min | 75,951 § |
| Subsquid | 5.34 min | 33,972 ⚠️ |
| Ponder | 6.44 min | 182,767 § |
| Sentio | 14.36 min | 75,951 § |
| Subgraph (The Graph) | 16.83 min | 35,039 |

> § Different counting methodology inflates several entries (75,951 / 182,767). Goldsky matches the
> canonical **35,039** exactly.

## Running via the benchmark harness
To benchmark all cases in one go — deploy, time, count, and generate a report — use the repo's
Turbo runner; see [`scripts/goldsky_turbo_benchmark.ts`](../../scripts/goldsky_turbo_benchmark.ts)
for setup and usage.

## Additional Commands
```bash
goldsky turbo get goldsky-case6 -o yaml
goldsky turbo list
goldsky turbo delete goldsky-case6
```

For more on Turbo pipelines, see the [Turbo documentation](https://docs.goldsky.com/turbo-pipelines).
