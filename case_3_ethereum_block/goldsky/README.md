# Goldsky Implementation — Ethereum Block Benchmark

This directory contains a **Goldsky** pipeline that indexes Ethereum block metadata for
blocks **0 → 100,000** — `number`, `hash`, `parent_hash`, and `timestamp` — one row per block.

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
goldsky turbo validate goldsky-case3.yaml
```

### 3. Deploy (run the job)
```bash
goldsky turbo apply goldsky-case3.yaml
```

### 4. Monitor & verify
```bash
goldsky turbo monitor goldsky-case3
goldsky turbo logs goldsky-case3 --follow
```

## Implementation Details

- **Source:** `ethereum.raw_blocks` (v1.0.0), filtered to `block_number <= 100000`.
- **Transform:** a single SQL projection selecting `id`, `number`, `hash`, `parent_hash`, and
  `block_timestamp`.
- **Sink:** Postgres table `goldsky_case3`, `primary_key: id`.

## Project Structure
- `goldsky-case3.yaml` — the pipeline (source → projection → Postgres sink)

## Query Examples
```sql
SELECT count(*) FROM goldsky_case3;
-- expected: 100,001 (blocks 0 through 100,000 inclusive)
```

## Performance Results

Goldsky (cloud, `resource_size: m`)

| Wall-clock | Indexing span | Records |
|---|---|---|
| **0.54 min** | 0.10 min | 100,001 ✅ |

Against the published indexers (Jan 2026 report):

| Indexer | Time | Records |
|---|---|---|
| Subsquid | 0.25 min | 13,156 ⚠️ (only ~13% of blocks) |
| **Goldsky** | **0.54 min** | **100,001 ✅** |
| Sentio | 2.51 min | 100,001 |
| Sentio Subgraph | 2.67 min | 100,001 |
| Ponder | 9.63 min | 100,001 |
| Subgraph (The Graph) | 50.58 min | 100,001 |
| Envio HyperIndex | N/A | — |

## Running via the benchmark harness
To benchmark all cases in one go — deploy, time, count, and generate a report — use the repo's
Turbo runner; see [`scripts/goldsky_turbo_benchmark.ts`](../../scripts/goldsky_turbo_benchmark.ts)
for setup and usage.

## Additional Commands
```bash
goldsky turbo get goldsky-case3 -o yaml
goldsky turbo list
goldsky turbo delete goldsky-case3
```

For more on Turbo pipelines, see the [Turbo documentation](https://docs.goldsky.com/turbo-pipelines).
