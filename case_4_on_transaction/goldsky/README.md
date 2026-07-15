# Goldsky Implementation — Transaction Gas Benchmark

This directory contains a **Goldsky** pipeline that, for every transaction in blocks
**22,280,000 → 22,290,000**, computes `gas_value = gas_used × effective_gas_price`.

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
The pipeline is `job: true`, so it runs over the fixed block range and self-terminates —
giving a clean wall-clock number.
```bash
goldsky turbo apply goldsky-case4.yaml
```

### 3. Monitor & verify
```bash
goldsky turbo monitor goldsky-case4
goldsky turbo logs goldsky-case4 --follow
```

## Implementation Details

- **Source:** `ethereum.receipt_transactions` (v1.2.0), filtered to `22,280,000 ≤ block ≤
  22,290,000`.
- **Transform:** a SQL transform computes `gas_value = gas_used × effective_gas_price` with
  native sql arithmetic.
- **Sink:** Postgres table `goldsky_case4`, `primary_key: id`.

## Project Structure
- `goldsky-case4.yaml` — the pipeline (source → transform → Postgres sink)

## Query Examples
```sql
SELECT count(*) FROM goldsky_case4;
-- expected: 1,696,641
```

## Performance Results

Goldsky (cloud, `resource_size: m`)

| Time | Time (actual indexing only) | Records |
|---|---|---|
| **4.67 min** | 2.93 min | 1,696,641 |

Against the published indexers (Jan 2026 report):

| Indexer | Time | Records |
|---|---|---|
| Subsquid | 1.25 min | 1,696,641 |
| **Goldsky** | **4.67 min** | 1,696,641 |
| Sentio | 22.12 min | 1,696,641 |
| Ponder | Timeout (2h, 2.5%) | — |
| Envio HyperIndex | N/A | 1,696,423 |
| Subgraph (The Graph) | N/A (no tx handler) | — |


## Running via the benchmark harness
To benchmark all cases in one go — deploy, time, count, and generate a report — use the repo's
Turbo runner; see [`scripts/goldsky_turbo_benchmark.ts`](../../scripts/goldsky_turbo_benchmark.ts)
for setup and usage.

## Additional Commands
```bash
goldsky turbo get goldsky-case4 -o yaml
goldsky turbo list
goldsky turbo delete goldsky-case4
```

For more on Turbo pipelines, see the [Turbo documentation](https://docs.goldsky.com/turbo-pipelines).
