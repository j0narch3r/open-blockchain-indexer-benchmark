# Goldsky Implementation — Uniswap V2 Trace Benchmark

This directory contains a **Goldsky** pipeline that decodes Uniswap V2 Router
`swapExactTokensForTokens` calls from **transaction traces** in blocks
**22,200,000 → 22,290,000**, writing one row per swap to Postgres.

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

### 2. Deploy (run the job)
```bash
goldsky turbo apply goldsky-case5.yaml
```

### 3. Monitor & verify
```bash
goldsky turbo monitor goldsky-case5
goldsky turbo logs goldsky-case5 --follow
```

## Implementation Details

- **Source:** `ethereum.raw_traces` (v1.2.0), filtered to `22,200,000 ≤ block ≤ 22,290,000` and
  the Uniswap V2 Router `swapExactTokensForTokens` selector.
- **Transform:** `evm_trace_decode(...)` decodes the call input into typed fields
  (`amount_in`, `amount_out_min`, `path`, `to`, `deadline`, …), emitted one row per matching
  trace.
- **Sink:** Postgres table `goldsky_case5`, `primary_key: id`.

## Project Structure
- `goldsky-case5.yaml` — the pipeline (source → transform → Postgres sink)

## Query Examples
```sql
SELECT count(*) FROM goldsky_case5;
-- expected: 50,191
```

## Performance Results

Goldsky (cloud, `resource_size: m`)

| Time | Time (actual indexing only) | Records |
|---|---|---|
| **0.98 min (58.6s)** | 0.05 min (3.2s) | 50,191 |

Against the published indexers (Jan 2026 report):

| Indexer | Time | Records |
|---|---|---|
| **Goldsky** | **0.98 min (58.6s)** | **50,191** |
| Sentio Subgraph | 2.17 min | 45,895 ⚠️ |
| Sentio | 2.54 min | 50,191 |
| Subsquid | 7.42 min | 50,191 |
| Subgraph (The Graph) | 17.81 min | 29,058 ⚠️ (~58% of traces) |
| Ponder | 74.71 min | 44,400 ⚠️ |

## Running via the benchmark harness
To benchmark all cases in one go — deploy, time, count, and generate a report — use the repo's
Turbo runner; see [`scripts/goldsky_turbo_benchmark.ts`](../../scripts/goldsky_turbo_benchmark.ts)
for setup and usage.

## Additional Commands
```bash
goldsky turbo get goldsky-case5 -o yaml
goldsky turbo list
goldsky turbo delete goldsky-case5
```

For more on Turbo pipelines, see the [Turbo documentation](https://docs.goldsky.com/turbo-pipelines).
