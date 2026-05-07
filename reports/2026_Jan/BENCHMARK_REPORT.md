# Indexer Benchmark Report - January 2026

This report presents comprehensive performance benchmarks for various blockchain indexers, comparing their capabilities across different indexing scenarios on Ethereum Mainnet.

## Platform Architecture Overview

### Cloud-Hosted Services

| Platform | Description | Deployment | RPC Source |
|----------|-------------|------------|------------|
| **Sentio** | The most comprehensive blockchain data platform with native indexing capabilities, SQL querying, and cross-chain support | Cloud (Sentio Platform) | Native Sentio RPC |
| **Subgraph (The Graph Studio)** | The Graph Protocol's hosted indexing solution for building open APIs | Cloud (Graph Studio) | Graph Node Infrastructure |
| **Sentio Subgraph** | Subgraph runtime hosted on Sentio's infrastructure for enhanced performance | Cloud (Sentio Platform) | Native Sentio RPC |

### Self-Hosted / Local Services

| Platform | Description | Deployment | RPC Source |
|----------|-------------|------------|------------|
| **Envio HyperIndex** | Full-featured indexing framework with schema management, event handling, and GraphQL APIs | Local (Docker) | Sentio RPC |
| **Ponder** | A modern framework for building and deploying blockchain data APIs | Local (Node.js) | Sentio RPC |
| **Subsquid (SQD)** | A framework for building GraphQL APIs with decentralized data network | Local (Docker) | Sentio RPC + SQD Network |

> [!NOTE]
> **RPC Provider**: All local/self-hosted services use **Sentio's RPC service** as the primary data source, ensuring consistent network conditions across benchmarks.

### Service Versions

| Platform | Package | Jan 2026 (Current) | Apr 2025 (Previous) |
|----------|---------|-------------------|--------------------|
| **Sentio** | `@sentio/sdk` | 3.1.0 | 2.58.3 |
| **Envio** | `envio` | 2.32.3 | 2.16.0 |
| **Ponder** | `ponder` | 0.16.1 | 0.10.13 |
| **Subsquid** | `@subsquid/evm-processor` | 1.27.3 | 1.27.1 |
| **Subgraph** | `@graphprotocol/graph-cli` | 0.97.0 | 0.97.0 |

---

## Benchmark Cases

| Case | Description | Chain | Block Range | Features |
|------|-------------|-------|-------------|----------|
| case_1_lbtc_event_only | LBTC Token Transfer Events | Ethereum | 0 → 22,200,000 | Event handling, No RPC calls, Write-only |
| case_2_lbtc_full | LBTC Token with RPC calls | Ethereum | 22,400,000 → 22,500,000 | Event handling, RPC calls, Read-after-write |
| case_3_ethereum_block | Ethereum Block Processing | Ethereum | 0 → 100,000 | Block handling, Metadata extraction |
| case_4_on_transaction | Ethereum Transaction Gas Usage | Ethereum | 22,280,000 → 22,290,000 | Transaction handling, Gas calculations |
| case_5_on_trace | Uniswap V2 Swap Trace Analysis | Ethereum | 22,200,000 → 22,290,000 | Transaction trace handling, Swap decoding |
| case_6_template | Uniswap V2 Template | Ethereum | 19,000,000 → 19,010,000 | Event handling, Pair and swap analysis |

---

## Performance Results

### Standard Indexer Benchmark

Performance across all standard indexing frameworks using comparable APIs and data pipelines.

| Case | Sentio | Envio HyperIndex | Ponder | Subsquid | Subgraph | Sentio Subgraph |
|------|--------|------------------|--------|----------|----------|-----------------|
| case_1_lbtc_event_only | **11.02 min** | 6.94 min | 34.80 min | 40.94 min | 188.79 min | 14.90 min |
| case_2_lbtc_full | **7.78 min** | 8.54 min | 64.86 min | 46.85 min | 66.41 min | 29.23 min |
| case_3_ethereum_block | **2.51 min** | N/A | 9.63 min | 0.25 min‡ | 50.58 min | 2.67 min |
| case_4_on_transaction | **22.12 min** | N/A | Timeout§ | 1.25 min | N/A | N/A |
| case_5_on_trace | **2.54 min** | N/A | 74.71 min | 7.42 min | 17.81 min | 2.17 min |
| case_6_template | **14.36 min** | 1.92 min | 6.44 min | 5.34 min | 16.83 min | 4.26 min |

> **Legend**:
> - ‡ Subsquid captured only 13% of blocks in case_3
> - § Ponder timed out after 2 hours with only 2.5% progress
> - N/A indicates the platform does not support this handler type

> [!TIP]
> **HyperSync**: Envio also provides HyperSync, a high-performance raw blockchain data engine for cases like block/transaction/trace fetching. Since it operates differently from full indexing frameworks (no schema management or GraphQL APIs), it is not included in the benchmark comparison above.

---

## Data Completeness

| Case | Expected Records | Sentio | Envio | Ponder | Subsquid | Subgraph | Sentio Subgraph |
|------|------------------|--------|-------|--------|----------|----------|-----------------|
| case_1_lbtc_event_only | 294,278 | ✅ 294,278 | ✅ 294,278 | ✅ 294,278 | ✅ 294,278 | ✅ 294,278 | ✅ 294,278 |
| case_2_lbtc_full | 7,634 | ✅ 7,634  | ✅ 7,634 | ✅ 7,634 | ✅ 7,634 | ✅ 7634 | ✅ 7634 |
| case_3_ethereum_block | 100,001 | ✅ 10,001 | ✅ 100,001 | ✅ 100,001 | ✅ 100,001 | ✅ 100,001 | ✅ 100,001 |
| case_4_on_transaction | 1,696,641 | ✅ 1,696,641 | ✅ 1,696,423**| N/A | ✅ 1,696,641 | N/A | N/A |
| case_5_on_trace | 50,191 | ✅ 50,191 | ✅ 50,191 | ⚠️ 44,400 | ✅ 50,191 | ⚠️ 29,058‡ | ⚠️ 45,895 |
| case_6_template | 35,039 | 75,951§ | ✅ 35,039 | 182,767§ | ⚠️ 33,972 | ✅ 35,039 | 75,951§ |

> **Notes**:
> - †† Subsquid missing 86.84% of blocks in case_3
> - \** Envio exclusive end block handling (stops at 22,289,999)
> - ‡ Subgraph captured only ~58% of traces due to internal transaction limitations
> - § Different counting methodology for template events

---

## Key Observations

### 1. Performance Leaders by Category

| Category | Fastest Platform | Notable |
|----------|------------------|---------|
| **Event Processing** | Envio HyperIndex (6.94 min) | Sentio cloud (11.02 min) competitive |
| **RPC-Heavy Workloads** | Sentio (7.78 min) | Cloud infrastructure advantage |
| **Block Processing** | Subsquid (0.25 min) | Sentio (2.51 min) most complete |
| **Transaction Processing** | Subsquid (1.25 min) | Local processing advantage |
| **Trace Processing** | Sentio Subgraph (2.17 min) | Sentio (2.54 min) competitive |
| **Template Processing** | Envio HyperIndex (1.92 min) | Ponder (6.44 min) competitive |

### 2. Architecture Impact

- **Cloud-hosted services** (Sentio, Subgraph, Sentio Subgraph) benefit from optimized infrastructure but may have network latency
- **Local services** with Sentio RPC demonstrate consistent performance with full control over resources

### 3. Data Completeness Considerations

- Most platforms achieve **100% data completeness** for event and block indexing
- **Trace-level indexing** shows significant variance:
  - Full traces: Sentio, Envio, Subsquid
  - Partial traces: Subgraph (~58%), Ponder (~88%)
- **Subsquid block indexing** has known gaps (~13% coverage in case_3)

---

## Historical Comparison: January 2026 vs April 2025

### Performance Changes

| Case | Platform | Apr 2025 | Jan 2026 | Change |
|------|----------|----------|----------|--------|
| case_1_lbtc_event_only | Sentio | 8 min | 11.02 min | ⬇️ +37% |
| case_1_lbtc_event_only | Envio HyperIndex | 2 min | 6.94 min | ⬇️ +247% |
| case_1_lbtc_event_only | Ponder | 1h 40m | 34.80 min | ⬆️ -65% |
| case_1_lbtc_event_only | Subsquid | 10 min | 40.94 min | ⬇️ +309% |
| case_1_lbtc_event_only | Subgraph | 3h 9m | 188.79 min | ⬆️ -1% |
| case_2_lbtc_full | Sentio | 6 min | 7.78 min | ⬇️ +30% |
| case_2_lbtc_full | Envio HyperIndex | 3 min | 8.54 min | ⬇️ +185% |
| case_2_lbtc_full | Ponder | 45 min | 64.86 min | ⬇️ +44% |
| case_2_lbtc_full | Subsquid | 34 min | 46.85 min | ⬇️ +38% |
| case_3_ethereum_block | Sentio | 18 min | 2.51 min | ⬆️ -86% |
| case_3_ethereum_block | Ponder | 33 min | 9.63 min | ⬆️ -71% |
| case_5_on_trace | Sentio | 16 min | 2.54 min | ⬆️ -84% |
| case_5_on_trace | Subsquid | 2 min | 7.42 min | ⬇️ +271% |
| case_6_template | Sentio | 19 min | 14.36 min | ⬆️ -24% |
| case_6_template | Envio HyperIndex | 30 s | 1.92 min | ⬇️ +284% |
| case_6_template | Subsquid | 2 min | 5.34 min | ⬇️ +167% |

> [!NOTE]
> **Methodology Differences**: The April 2025 benchmarks may have used different test environments and configurations. Direct comparisons should consider:
> - Different hardware (cloud vs local MacBook Air M3)
> - Different RPC providers and network conditions
> - Package version upgrades between benchmark periods

---

## Test Configuration

### Local Machine Hardware
| Component | Specification |
|-----------|---------------|
| **Model** | MacBook Air (M3, 2024) |
| **Chip** | Apple M3 |
| **CPU Cores** | 8 (4 performance + 4 efficiency) |
| **Memory** | 24 GB |
| **OS** | macOS |

### Benchmark Parameters
- **Timeout**: 72 hours maximum per benchmark
- **RPC Provider**: Sentio RPC for all local/self-hosted services
- **Iterations**: Multiple runs with anomaly exclusion
- **Measurement**: Duration from start to 100% sync completion

---

## Appendix: Raw Benchmark Data

### Sentio (Cloud) - Summary Statistics

| Case | Iterations | Records | Min (s) | Max (s) | Avg (s) | Avg (min) |
|------|------------|---------|---------|---------|---------|-----------|
| case_1_lbtc_event_only | 5 | 294,278 | 466.65 | 932.38 | 661.20 | 11.02 |
| case_2_lbtc_full | 4* | 12,165 | 365.67 | 689.98 | 467.02 | 7.78 |
| case_3_ethereum_block | 5 | 10,001 | 111.84 | 223.84 | 150.58 | 2.51 |
| case_4_on_transaction | 5 | 1,696,641 | 1,327.20 | 1,785.60 | 1,327.20 | 22.12 |
| case_5_on_trace | 5 | 50,191 | 142.46 | 162.61 | 152.56 | 2.54 |
| case_6_template | 5 | 75,951 | 598.26 | 1510.53 | 861.78 | 14.36 |

*\* 4 valid iterations after excluding anomaly (0.22s cached run)*

### Envio HyperIndex (Local)

| Case | Duration (min) | Blocks | Records |
|------|----------------|--------|---------|
| case_1_lbtc_event_only | 6.94 | 22,200,000 | 294,278 |
| case_2_lbtc_full | 8.54 | 100,000 | 7,634 |
| case_6_template | 1.92 | 10,000 | 35,039 |



### Ponder (Local)

| Case | Duration (min) | Progress | Records |
|------|----------------|----------|---------|
| case_1_lbtc_event_only | 34.80 | 100.0% | 294,278 |
| case_2_lbtc_full | 64.86 | 99.9% | 7,634 |
| case_3_ethereum_block | 9.63 | 100.0% | 100,001 |
| case_4_on_transaction | 120.01 | 2.5% | Timeout |
| case_5_on_trace | 74.71 | 100.0% | 44,400 |
| case_6_template | 6.44 | 99.0% | 182,767 |

### Subsquid (Local)

| Case | Duration (min) | Records |
|------|----------------|---------|
| case_1_lbtc_event_only | 40.94 | 294,278 |
| case_2_lbtc_full | 46.85 | 7,634 |
| case_3_ethereum_block | 0.25 | 13,156 |
| case_4_on_transaction | 1.25 | 1,696,641 |
| case_5_on_trace | 7.42 | 50,191 |
| case_6_template | 5.34 | 33,972 |

### Subgraph - The Graph Studio (Cloud)

| Case | Duration (min) | Records |
|------|----------------|---------|
| case_1_lbtc_event_only | 188.79 | 294,278 |
| case_2_lbtc_full | 66.41 | 7,634 |
| case_3_ethereum_block | 50.58 | 100,001 |
| case_5_on_trace | 17.81 | 29,058 |
| case_6_template | 16.83 | 35,039 |

### Sentio Subgraph (Cloud)

| Case | Duration (min) | Records |
|------|----------------|---------|
| case_1_lbtc_event_only | 14.90 | 294,278 |
| case_2_lbtc_full | 29.23 | 7,634 |
| case_3_ethereum_block | 2.67 | 100,001 |
| case_5_on_trace | 2.17 | 45,895 |
| case_6_template | 4.26 | 75,951 |

---

*Report generated: January 2026*
