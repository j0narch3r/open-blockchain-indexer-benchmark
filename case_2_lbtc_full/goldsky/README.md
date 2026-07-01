# Goldsky Implementation — LBTC Full (Balances + Points) Benchmark

This directory contains a **Goldsky** pipeline that, over LBTC Transfers in blocks
**22,400,000 → 22,500,000**, computes each account's **current LBTC balance** (via on-chain
`balanceOf`) and a **points** score, writing both to Postgres.

This is the benchmark's **RPC case** — its purpose is to exercise read-after-write RPC
enrichment. Every published indexer runs it with real RPC calls; this implementation does the
same, using **Goldsky Edge RPC** (hedged, low-latency) as the RPC source, in a single combined
deployment that fans one source read into two branches.

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
* **A Goldsky Edge RPC secret.** Create one in the dashboard (Edge → endpoints) and supply it at
  deploy time — the pipeline references it as `<YOUR_GOLDSKY_EDGE_SECRET>` in the `_gs_eth_call`
  URL.

## Setup & Running Instructions

### 1. Log in
```bash
goldsky login
```

### 2. Insert your Edge secret
Replace `<YOUR_GOLDSKY_EDGE_SECRET>` in `goldsky-case2.yaml` with your Edge secret token.

### 3. Deploy (run the job)
```bash
goldsky turbo apply goldsky-case2.yaml --skip-validation
```
`--skip-validation` is used because the `postgres_aggregate` sink's validator can time out
against a pooled Postgres connection; runtime is unaffected.

### 4. Monitor & verify
```bash
goldsky turbo monitor goldsky-case2
goldsky turbo logs goldsky-case2 --follow
```

## Implementation Details

One source read of the LBTC Transfer stream fans into two branches:

- **Shared:** source `ethereum.raw_logs` (v1.2.0) filtered to LBTC + `22,400,000 ≤ block ≤
  22,500,000`, then `_gs_log_decode` of the Transfer event.
- **Branch A — balances:** the distinct set of `from`∪`to` accounts (zero address excluded),
  each enriched with `balanceOf(address)` via `_gs_eth_call` over Edge RPC (multicall enabled,
  chunk size 500) → Postgres table `goldsky_case2_balances`.
- **Branch B — points:** a summation-by-parts score (RPC start-of-window balance + the
  within-window transfer-delta integral) → `postgres_aggregate` sink `goldsky_case2_points`
  (with landing log `goldsky_case2_points_log`).

Both branches run concurrently off the one read, so wall-clock ≈ the slower (points) branch, not
the sum. Edge RPC is the bottleneck; the sink `parallelism` knob is inert here (tiny write
volume).

## Project Structure
- `goldsky-case2.yaml` — single combined pipeline (shared decode → balances + points branches → 2 sinks)

## Query Examples
```sql
SELECT count(*) FROM goldsky_case2_balances;   -- distinct accounts
-- expected: 7,634 (zero address excluded)
```

## Performance Results

Goldsky (cloud, `resource_size: m`)

| Wall-clock | Indexing span | Records |
|---|---|---|
| **1.49 min** | 0.58 min | 7,634 ✅ |

Against the published indexers (Jan 2026 report):

| Indexer | Time | Records |
|---|---|---|
| **Goldsky** | **1.49 min** | 7,634 ✅ |
| Sentio | 7.78 min | 7,634 |
| Envio HyperIndex | 8.54 min | 7,634 |
| Sentio Subgraph | 29.23 min | 7,634 |
| Subsquid | 46.85 min | 7,634 |
| Ponder | 64.86 min | 7,634 |
| Subgraph (The Graph) | 66.41 min | 7,634 |

> This case is RPC-bound for every indexer; Goldsky's number reflects enrichment over Edge RPC,
> not an RPC-dodging shortcut (balances are read from chain via `balanceOf`, as the case intends).

## Running via the benchmark harness
To benchmark all cases in one go — deploy, time, count, and generate a report — use the repo's
Turbo runner; see [`scripts/goldsky_turbo_benchmark.ts`](../../scripts/goldsky_turbo_benchmark.ts)
for setup and usage.

## Additional Commands
```bash
goldsky turbo get goldsky-case2 -o yaml
goldsky turbo list
goldsky turbo delete goldsky-case2
```

For more on Turbo pipelines, see the [Turbo documentation](https://docs.goldsky.com/turbo-pipelines).
