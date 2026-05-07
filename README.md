# Indexer Benchmarks - January 2026

This repository contains performance benchmarks for various blockchain indexers, comparing their capabilities and performance across different indexing scenarios.

## Benchmark Cases

| Case | Description | Features |
|------|-------------|----------|
| [case_1_lbtc_event_only](./case_1_lbtc_event_only/) | Simple event indexing of LBTC token transfers | Event handling, No RPC calls, Write-only operations |
| [case_2_lbtc_full](./case_2_lbtc_full/) | Complex indexing with RPC calls for token balances and point calculation | Event handling, RPC calls, Read-after-write operations, Point calculation |
| [case_3_ethereum_block](./case_3_ethereum_block/) | Block-level indexing of Ethereum blocks | Block handling, Metadata extraction |
| [case_4_on_transaction](./case_4_on_transaction/) | Transaction gas usage indexing | Transaction handling, Gas calculations |
| [case_5_on_trace](./case_5_on_trace/) | Uniswap V2 transaction trace analysis | Transaction trace handling, Swap decoding |
| [case_6_template](./case_6_template/) | Uniswap V2 template benchmark | Event handling, Pair and swap analysis |

## Latest Benchmark Results

Our most recent benchmark (January 2026) shows significant performance performance metrics across different platforms.

**Key Highlights:**

- **Fastest Event Processing**: Envio HyperIndex (6.94 min) and Sentio (11.02 min)
- **Top Block Processing**: Subsquid (0.25 min) and Sentio (2.51 min)
- **Best Trace Processing**: Sentio Subgraph (2.17 min) and Sentio (2.54 min)
- **Raw Data Engine**: Envio HyperSync provides massive throughput (100k blocks in 3s) for non-indexing use cases

See the [complete benchmark results](#performance-results) for detailed timing data.

## Test Methodology

### Test Configuration
- All benchmarks run on standardized hardware environments
- Each test runs until completion or timeout (72 hours)
- RPC providers: When built-in RPC support isn't available, we use Alchemy Growth tier

### Test Case Design
Our benchmark cases are designed to test different aspects of indexer performance:

1. **Chain Selection**:
   - Ethereum Mainnet for all test cases

2. **Data Types**:
   - Events: Transfer events in case_1 and case_2
   - Blocks: Block data in case_3
   - Transactions: Gas usage in case_4
   - Traces: Uniswap V2 swap transactions in case_5

3. **RPC Patterns**:
   - No RPC: case_1 tests raw event processing
   - RPC Calls: case_2 tests balanceOf() calls
   - Block Data: case_3 tests block processing
   - Transaction Data: case_4 tests transaction processing
   - Trace Data: case_5 tests transaction trace processing
   - Template Data: case_6 tests factory contract event processing and pair creation tracking

4. **Write Patterns**:
   - Write-only: case_1 tests simple data storage
   - Read-after-write: case_2 tests database interaction complexity
   - Computational: case_2/case_4 tests calculation and derivation of metrics

## Indexer Platforms

### Platform Descriptions

- **Sentio**: The most comprehensive blockchain data platform with the widest chain support, offering both raw data access and indexing capabilities across multiple blockchain ecosystems
- **Envio HyperSync**: Envio's high-performance EVM based blockchain data engine that serves as a direct replacement for traditional RPC endpoints for raw blockchain data
- **Envio HyperIndex**: Built on top of HyperSync, providing a complete indexing framework with schema management, event handling, and GraphQL APIs
- **Ponder**: A framework for building and deploying blockchain data APIs
- **Subsquid**: A framework for building GraphQL APIs on top of blockchain data
- **Subgraph**: The Graph Protocol's indexing solution for building open APIs

### Use Cases

- Use HyperSync directly when you need raw blockchain data at maximum speed
- Use HyperIndex when you need a full-featured indexing solution

### Supported Chains

| Chain | Sentio | Envio | Ponder | Subsquid | Subgraph |
|-------|--------|-------|--------|----------|----------|
| EVM* | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sui | ✅ | ❌ | ❌ | ❌ | ❌ |
| Aptos | ✅ | ❌ | ❌ | ❌ | ❌ |
| StarkNet | ✅ | ❌ | ❌ | ✅ | ✅ |
| Cosmos | ⚠️ | ❌ | ❌ | ❌ | ❌ |
| Solana | ⚠️ | ❌ | ❌ | ✅ | ⚠️ |
| Bitcoin | ⚠️ | ❌ | ❌ | ❌ | ✅ |
| Fuel | ✅ | ✅ | ❌ | ✅ | ❌ |

\* Including many EVM-compatible L1/L2 chains

⚠️ Limited support

### Supported Features

| Feature | Sentio | Envio | Ponder | Subsquid | Subgraph |
|---------|--------|-------|--------|----------|----------|
| Event Handler | ✅ | ✅ | ✅ | ✅ | ✅ |
| Block Handler | ✅ | ⚠️$ | ✅ | ✅ | ✅ |
| Transaction Handler | ✅ | ⚠️$ | ✅ | ✅ | ❌ |
| Trace/Internal Tx Handler | ✅ | ⚠️$ | ❌ | ✅$$ | ⚠️† |
| Native RPC | ✅ | ⚠️$ | ❌ | ❌ | ❌ |
| SQL Querying | ✅ | ✅ | ✅ | ✅ | ❌ |
| GraphQL API | ✅ | ✅ | ✅ | ❌ | ✅ |
| Factory Template Dynamic Registation | ✅ | ✅ | ✅ | ⚠️* | ✅ |
| Batch RPC calls | ✅^ | ✅^ | ✅^ | ✅^ | ❌ |

⚠️ Limited capability or requires additional configuration

$ Envio does not support natively, but one can utilize HyperSync to retrieve data, but it requires manual decoding and client-side processing of reorgs.

$$ Subsquid does have access internal call trace as bytes, however, it requires manual decoding

† Subgraph has limited internal transaction visibility, only detecting direct contract calls, not internal transactions. This leads to incomplete data (~40% fewer records) and inaccurate sender identification in trace-level indexing as documented in the [case_5_on_trace](./case_5_on_trace/) benchmark.

\* Subsquid requires manual configuration updates at fixed blocks to optimize template indexing, with limitations on the number of contracts (up to tens of thousands) and requiring periodic maintenance to minimize sync overhead.

^ Rely on multicall contract deployed by MakerDAO for batch RPC calls, with Envio using built-in platform infrastructure and others using multicall

This benchmark provides a comparative analysis of indexer performance across different scenarios, helping developers choose the most appropriate indexing solution for their specific needs.

### Performance Results

#### Standard Indexer Benchmark

Full indexing frameworks including database storage and API generation.

| Case | Sentio | Envio HyperIndex | Ponder | Subsquid | Subgraph (Hosted) | Sentio Subgraph |
|------|--------|------------------|--------|----------|-------------------|-----------------|
| case_1_lbtc_event_only | **11.02 min** | 6.94 min | 34.80 min | 40.94 min | 188.79 min | 14.90 min |
| case_2_lbtc_full | **7.78 min** | 8.54 min | 64.86 min | 46.85 min | 66.41 min | 29.23 min |
| case_3_ethereum_block | **2.51 min** | N/A | 9.63 min | 0.25 min | 50.58 min | 2.67 min |
| case_4_on_transaction | **22.12 min** | N/A | Timeout§ | 1.25 min | N/A | N/A |
| case_5_on_trace | **2.54 min** | N/A | 74.71 min | 7.42 min | 17.81 min | 2.17 min |
| case_6_template | **14.36 min** | 1.92 min | 6.44 min | 5.34 min | 16.83 min | 4.26 min |

> **Notes**:
> - § Ponder timed out after 2 hours with only 2.5% progress

#### Envio HyperSync (Raw Data Engine)

HyperSync is a raw data extraction engine, not a full indexer. It does not handle database storage or schema management in these benchmarks.

| Case | Duration | Blocks | Records |
|------|----------|--------|---------|
| case_3_ethereum_block | **3.19 s** | 100,000 | 100,001 |
| case_4_on_transaction | **128.64 s** | 10,000 | 1,696,423 |
| case_5_on_trace | **33.84 s** | 90,000 | 50,191 |

### Data Completeness

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

### Key Observations

1. **Performance Leaders**:
   - **Event Processing**: Envio HyperIndex and Sentio lead the pack.
   - **Block/Trace/Transaction Processing**: Sentio, Sentio Subgraph, and Subsquid perform well for specialized data needs.
   - **Raw Data Fetching**: Envio HyperSync is in a league of its own for non-indexing raw data retrieval.

2. **Data Completeness**:
   - Most modern indexers (Sentio, Envio, Ponder) achieve 100% data completeness for standard cases.
   - Trace-level indexing remains challenging, with Subgraph finding significantly fewer traces (~58%).
   - Subsquid showed gaps in block processing coverage (case 3).

3. **Infrastructure**:
   - Cloud-native solutions (Sentio, Sentio Subgraph) generally offer strong consistency and ease of use.
   - Local solutions with optimized RPCs (Envio, Ponder) are highly competitive but depend on local hardware resources.

## Exported Data

All benchmark datasets, comparison reports, and analysis results are available via Google Drive:

- **Complete Dataset Collection**: [Indexer Benchmark Datasets](https://drive.google.com/drive/folders/1zwJsEoxQJSAKKPMlji4xRqnqR2nqVQ4k)
- Contains data from all benchmark cases for all tested indexer platforms
- Includes raw data, comparison reports, and analysis files for each benchmark scenario
- Individual case folders are also linked in their respective README files
