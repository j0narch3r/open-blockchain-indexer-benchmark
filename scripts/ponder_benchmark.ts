/**
 * Ponder Benchmark Automation Script
 * 
 * This script automates running Ponder indexer benchmarks by:
 * 1. Parsing ponder.config.ts to get block range
 * 2. Starting the indexer with bun run dev
 * 3. Monitoring progress via log parsing
 * 4. Detecting completion and collecting metrics
 * 5. Generating benchmark reports (JSON and Markdown)
 */

import { spawn, type Subprocess } from "bun";
import { join, dirname } from "path";
import { rmSync, existsSync, readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Configuration constants
const POLL_INTERVAL_MS = 5000; // 5 seconds
const TIMEOUT_MS = 120 * 60 * 1000; // 120 minutes max

// Configuration for supported cases
const CASES: Record<string, { path: string; description: string; entityTable: string }> = {
    case1: {
        path: "case_1_lbtc_event_only/ponder",
        description: "LBTC Event Only Benchmark",
        entityTable: "lbtc_transfer",
    },
    case2: {
        path: "case_2_lbtc_full/ponder",
        description: "LBTC Full Benchmark",
        entityTable: "accounts",
    },
    case3: {
        path: "case_3_ethereum_block/ponder",
        description: "Ethereum Block Benchmark",
        entityTable: "block",
    },
    case4: {
        path: "case_4_on_transaction/ponder",
        description: "On Transaction Benchmark",
        entityTable: "gas_spent",
    },
    case5: {
        path: "case_5_on_trace/ponder",
        description: "On Trace Benchmark",
        entityTable: "swap",
    },
    case6: {
        path: "case_6_template/ponder",
        description: "Template (Factory) Benchmark",
        entityTable: "swap",
    },
};

interface PonderConfig {
    startBlock: number;
    endBlock: number;
    chainName: string;
}

interface PerformanceMetrics {
    rpcTime: number;
    computeTime: number;
    storageTime: number;
    operationCount: number;
}

interface BenchmarkResult {
    caseName: string;
    caseId: string;
    iteration: number;
    startTime: string;
    endTime: string;
    durationSeconds: number;
    startBlock: number;
    endBlock: number;
    totalBlocks: number;
    blocksPerSecond: number;
    lastProgressPercent: number;
    recordCount: number;  // Data completeness - total records indexed
    performanceMetrics?: PerformanceMetrics;
    error?: string;
}

/**
 * Parse ponder.config.ts to extract block range
 */
function parsePonderConfig(casePath: string): PonderConfig {
    const configPath = join(casePath, "ponder.config.ts");
    const content = readFileSync(configPath, "utf-8");

    // Extract startBlock and endBlock from config
    const startBlockMatch = content.match(/startBlock:\s*(\d+)/);
    const endBlockMatch = content.match(/endBlock:\s*(\d+)/);
    const chainMatch = content.match(/chains:\s*\{\s*(\w+):/);

    return {
        startBlock: startBlockMatch ? parseInt(startBlockMatch[1], 10) : 0,
        endBlock: endBlockMatch ? parseInt(endBlockMatch[1], 10) : 0,
        chainName: chainMatch ? chainMatch[1] : "mainnet",
    };
}

/**
 * Parse performance metrics from log content
 * Format: [timestamp] Block X - Ops: Y - RPC: Zs - Compute: Ws - Storage: Vs
 */
function parsePerformanceMetrics(logContent: string): PerformanceMetrics | undefined {
    const matches = logContent.match(/Ops:\s*(\d+).*RPC:\s*([\d.]+)s.*Compute:\s*([\d.]+)s.*Storage:\s*([\d.]+)s/g);
    if (matches && matches.length > 0) {
        const lastMatch = matches[matches.length - 1];
        const parsed = lastMatch.match(/Ops:\s*(\d+).*RPC:\s*([\d.]+)s.*Compute:\s*([\d.]+)s.*Storage:\s*([\d.]+)s/);
        if (parsed) {
            return {
                operationCount: parseInt(parsed[1], 10),
                rpcTime: parseFloat(parsed[2]),
                computeTime: parseFloat(parsed[3]),
                storageTime: parseFloat(parsed[4]),
            };
        }
    }
    return undefined;
}

/**
 * Parse progress from Ponder logs
 * Looking for patterns like: "progress=X.X%" or "XX.X% (XXm XXs eta)"
 */
function parseProgress(logContent: string): { percent: number; currentBlock: number } {
    let percent = 0;
    let currentBlock = 0;

    // Try to find progress percentage (e.g., "4.9% (22m 06s eta)" or "progress=5.2%")
    const progressMatches = logContent.match(/(\d+\.?\d*)%/g);
    if (progressMatches && progressMatches.length > 0) {
        const lastPercent = parseFloat(progressMatches[progressMatches.length - 1]);
        if (!isNaN(lastPercent)) {
            percent = lastPercent;
        }
    }

    // Try to find block numbers from logs (e.g., "block_range=[22401234,22401567]")
    const blockMatches = logContent.match(/block_range=\[(\d+),(\d+)\]/g);
    if (blockMatches && blockMatches.length > 0) {
        const lastMatch = blockMatches[blockMatches.length - 1].match(/block_range=\[(\d+),(\d+)\]/);
        if (lastMatch) {
            currentBlock = parseInt(lastMatch[2], 10);
        }
    }

    return { percent, currentBlock };
}

/**
 * Check if indexing is complete
 */
function isIndexingComplete(logContent: string): boolean {
    // Check for completion indicators from Ponder logs
    return (
        logContent.includes("Completed indexing") ||
        logContent.includes("Status: complete") ||
        logContent.includes("│ complete │") ||
        logContent.includes("Skipped live indexing") ||
        logContent.includes("only requires backfill indexing") ||
        logContent.includes("Indexed all historical") ||
        logContent.includes("All chains synced")
    );
}

/**
 * Sleep helper
 */
function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Query record count via GraphQL API (while Ponder is running)
 * Ponder runs GraphQL API on port 42069 by default
 */
async function queryRecordCount(casePath: string, entityTable: string, caseId: string, port: number = 42069): Promise<number> {
    const graphqlUrl = `http://localhost:${port}/graphql`;

    // Convert snake_case table name to camelCase for GraphQL (e.g., lbtc_transfer -> lbtcTransfers)
    const pluralize = (name: string) => {
        // Simple pluralization: add 's' unless already ends with 's'
        return name.endsWith("s") ? name : name + "s";
    };

    const toCamelCase = (str: string) => {
        return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
    };

    const entityName = pluralize(toCamelCase(entityTable));

    // Query with limit 0 to just get the totalCount
    const query = `
        query {
            ${entityName}(limit: 1000) {
                items {
                    id
                }
                totalCount
            }
        }
    `;

    try {
        const response = await fetch(graphqlUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query }),
        });

        if (!response.ok) {
            console.log(`[${caseId}] GraphQL request failed: ${response.status}`);
            return 0;
        }

        const result = await response.json() as { data?: Record<string, { totalCount?: number }> };
        const count = result.data?.[entityName]?.totalCount ?? 0;
        console.log(`[${caseId}] Total records indexed: ${count}`);
        return count;
    } catch (e) {
        console.log(`[${caseId}] Error querying GraphQL (server may not be running):`, e);
        return 0;
    }
}

/**
 * Run a single benchmark
 */
async function runBenchmark(
    caseId: string,
    iteration: number
): Promise<BenchmarkResult> {
    const caseConfig = CASES[caseId];
    const casePath = join(process.cwd(), caseConfig.path);
    const caseName = caseConfig.description;

    console.log(`\n${"=".repeat(60)}`);
    console.log(`[${caseId}] Starting benchmark iteration ${iteration}`);
    console.log(`[${caseId}] ${caseName}`);
    console.log(`${"=".repeat(60)}\n`);

    // Parse config to get block range
    const config = parsePonderConfig(casePath);
    const totalBlocks = config.endBlock - config.startBlock;

    console.log(`[${caseId}] Block range: ${config.startBlock} -> ${config.endBlock} (${totalBlocks} blocks)`);

    // 1. Cleanup directories
    console.log(`[${caseId}] Cleaning up artifacts...`);
    const dirsToClean = [".ponder", "generated", "node_modules"];
    for (const dir of dirsToClean) {
        const fullPath = join(casePath, dir);
        if (existsSync(fullPath)) {
            console.log(`  Removing ${dir}/`);
            rmSync(fullPath, { recursive: true, force: true });
        }
    }

    // Remove lockfiles
    const locksToRemove = ["pnpm-lock.yaml", "package-lock.json", "yarn.lock", "bun.lockb"];
    for (const file of locksToRemove) {
        const fullPath = join(casePath, file);
        if (existsSync(fullPath)) {
            console.log(`  Removing ${file}`);
            rmSync(fullPath, { force: true });
        }
    }

    // 2. Install dependencies
    console.log(`\n[${caseId}] Installing dependencies with Bun...`);
    const installProc = spawn({
        cmd: ["bun", "install"],
        cwd: casePath,
        stdout: "inherit",
        stderr: "inherit",
    });
    await installProc.exited;

    // 3. Run codegen
    console.log(`\n[${caseId}] Running codegen...`);
    const codegenProc = spawn({
        cmd: ["bun", "run", "codegen"],
        cwd: casePath,
        stdout: "inherit",
        stderr: "inherit",
    });
    await codegenProc.exited;

    // 4. Start Ponder and monitor
    const startTime = new Date();
    const logFile = join(casePath, "benchmark.log");

    console.log(`\n[${caseId}] Starting Ponder...`);
    console.log(`[${caseId}] Log file: ${logFile}\n`);

    // Create env without DATABASE_URL to force PGlite
    const cleanEnv = { ...process.env };
    delete cleanEnv.DATABASE_URL;

    // Start dev process with output to log file
    const devProc = spawn({
        cmd: ["sh", "-c", `bun run dev 2>&1 | tee "${logFile}"`],
        cwd: casePath,
        stdout: "inherit",
        stderr: "inherit",
        env: cleanEnv,
    });

    let error: string | undefined;
    let indexingComplete = false;
    let lastProgress = 0;
    let lastLogTime = Date.now();
    let lastPerformanceMetrics: PerformanceMetrics | undefined;
    let currentBlock = config.startBlock;

    const timeoutTime = Date.now() + TIMEOUT_MS;

    // Poll for completion
    while (!indexingComplete && Date.now() < timeoutTime) {
        await sleep(POLL_INTERVAL_MS);

        // Read log file
        try {
            if (existsSync(logFile)) {
                const logContent = readFileSync(logFile, "utf-8");

                // Check completion
                if (isIndexingComplete(logContent)) {
                    console.log(`\n[${caseId}] ✓ Indexing complete!`);
                    indexingComplete = true;
                    break;
                }

                // Parse progress
                const progress = parseProgress(logContent);
                if (progress.percent > lastProgress) {
                    lastProgress = progress.percent;
                }
                if (progress.currentBlock > currentBlock) {
                    currentBlock = progress.currentBlock;
                }

                // Parse performance metrics
                const metrics = parsePerformanceMetrics(logContent);
                if (metrics) {
                    lastPerformanceMetrics = metrics;
                }

                // Log progress every 30 seconds
                if (Date.now() - lastLogTime > 30000) {
                    const elapsed = (Date.now() - startTime.getTime()) / 1000;
                    console.log(`\n[${caseId}] Progress: ${lastProgress.toFixed(1)}% | Block: ${currentBlock}/${config.endBlock} | Elapsed: ${elapsed.toFixed(0)}s`);
                    lastLogTime = Date.now();
                }
            }
        } catch (e) {
            // Ignore file read errors
        }
    }

    const endTime = new Date();
    const durationSeconds = (endTime.getTime() - startTime.getTime()) / 1000;

    // Check for timeout
    if (!indexingComplete && !error) {
        error = `Timeout: only reached ${lastProgress.toFixed(1)}% (block ${currentBlock}/${config.endBlock})`;
    }

    // Query record count for data completeness BEFORE stopping (GraphQL still available)
    console.log(`\n[${caseId}] Querying record count for data completeness...`);
    const recordCount = await queryRecordCount(casePath, caseConfig.entityTable, caseId);

    // Stop the process immediately and forcefully
    console.log(`\n[${caseId}] Stopping Ponder...`);
    try {
        devProc.kill();
    } catch (e) {
        // Ignore if already dead
    }

    // Wait a moment then force kill any remaining processes
    await sleep(1000);

    // Force kill any ponder processes that might still be running
    try {
        const { execSync } = await import("child_process");
        execSync(`pkill -f "ponder" || true`, { stdio: "ignore" });
        execSync(`lsof -ti:42069 | xargs kill -9 2>/dev/null || true`, { stdio: "ignore" });
    } catch (e) {
        // Ignore errors
    }

    await sleep(1000);

    const result: BenchmarkResult = {
        caseName,
        caseId,
        iteration,
        startTime: startTime.toISOString(),
        endTime: endTime.toISOString(),
        durationSeconds,
        startBlock: config.startBlock,
        endBlock: config.endBlock,
        totalBlocks,
        blocksPerSecond: totalBlocks / durationSeconds,
        lastProgressPercent: lastProgress,
        recordCount,
        performanceMetrics: lastPerformanceMetrics,
        error,
    };

    console.log(`\n[${caseId}] Benchmark Result:`);
    console.log(`  Duration: ${durationSeconds.toFixed(2)}s (${(durationSeconds / 60).toFixed(2)} min)`);
    console.log(`  Progress: ${lastProgress.toFixed(1)}%`);
    console.log(`  Blocks/sec: ${result.blocksPerSecond.toFixed(2)}`);
    if (lastPerformanceMetrics) {
        console.log(`  RPC Time: ${lastPerformanceMetrics.rpcTime.toFixed(2)}s`);
        console.log(`  Compute Time: ${lastPerformanceMetrics.computeTime.toFixed(2)}s`);
        console.log(`  Storage Time: ${lastPerformanceMetrics.storageTime.toFixed(2)}s`);
        console.log(`  Operations: ${lastPerformanceMetrics.operationCount}`);
    }
    if (error) {
        console.log(`  Error: ${error}`);
    }

    return result;
}

/**
 * Save benchmark results to files
 */
function saveResults(results: BenchmarkResult[]): void {
    // Save JSON report
    const jsonPath = join(__dirname, "ponder_benchmark_report.json");
    writeFileSync(jsonPath, JSON.stringify(results, null, 2));
    console.log(`\nSaved JSON report to: ${jsonPath}`);

    // Save Markdown report
    const mdPath = join(__dirname, "ponder_benchmark_report.md");
    let md = "# Ponder Benchmark Report\n\n";
    md += `Generated: ${new Date().toISOString()}\n\n`;
    md += "| Case | Iteration | Duration (s) | Duration (min) | Blocks | Blocks/s | Records | Progress | Error |\n";
    md += "|------|-----------|--------------|----------------|--------|----------|---------|----------|-------|\n";

    for (const r of results) {
        const error = r.error ? r.error.substring(0, 20) + "..." : "-";
        md += `| ${r.caseId} | ${r.iteration} | ${r.durationSeconds.toFixed(2)} | ${(r.durationSeconds / 60).toFixed(2)} | ${r.totalBlocks} | ${r.blocksPerSecond.toFixed(2)} | ${r.recordCount} | ${r.lastProgressPercent.toFixed(1)}% | ${error} |\n`;
    }

    // Add summary statistics
    md += "\n## Summary Statistics\n\n";

    const caseIds = [...new Set(results.map(r => r.caseId))];
    for (const caseId of caseIds) {
        const caseResults = results.filter(r => r.caseId === caseId && !r.error);
        if (caseResults.length === 0) continue;

        const durations = caseResults.map(r => r.durationSeconds);
        const avgDuration = durations.reduce((a, b) => a + b, 0) / durations.length;
        const minDuration = Math.min(...durations);
        const maxDuration = Math.max(...durations);

        md += `### ${caseId} - ${CASES[caseId].description}\n`;
        md += `- Iterations: ${caseResults.length}\n`;
        md += `- Average: ${avgDuration.toFixed(2)}s (${(avgDuration / 60).toFixed(2)} min)\n`;
        md += `- Min: ${minDuration.toFixed(2)}s\n`;
        md += `- Max: ${maxDuration.toFixed(2)}s\n\n`;
    }

    writeFileSync(mdPath, md);
    console.log(`Saved Markdown report to: ${mdPath}`);
}

/**
 * Query records only mode
 * 
 * NOTE: This feature has limitations with PGlite (Ponder's default database):
 * - `ponder dev` wipes the database on startup
 * - `ponder serve` requires an external PostgreSQL with DATABASE_SCHEMA
 * 
 * For PGlite, records are queried automatically during the benchmark run
 * (before stopping the indexer).
 * 
 * To use this mode, configure DATABASE_URL to point to an external PostgreSQL.
 */
async function queryRecordsOnly(casesToQuery: string[]): Promise<void> {
    console.log("\n" + "═".repeat(60));
    console.log("QUERY RECORDS ONLY MODE");
    console.log("═".repeat(60));
    console.log("\n⚠️  LIMITATION: This mode does not work reliably with PGlite.\n");
    console.log("Ponder uses PGlite by default, which has the following issues:");
    console.log("  • 'ponder dev' - Wipes the database on startup");
    console.log("  • 'ponder serve' - Requires external PostgreSQL + DATABASE_SCHEMA\n");
    console.log("OPTIONS:");
    console.log("  1. Run full benchmarks - Records are queried before stopping the indexer");
    console.log("  2. Configure DATABASE_URL to use external PostgreSQL\n");

    const results: { caseId: string; caseName: string; recordCount: number; dbExists: boolean }[] = [];

    for (const caseId of casesToQuery) {
        const caseConfig = CASES[caseId];
        const casePath = join(process.cwd(), caseConfig.path);
        const dbPath = join(casePath, ".ponder/pglite");
        const dbExists = existsSync(dbPath);

        console.log(`[${caseId}] ${caseConfig.description}`);
        console.log(`  Database: ${dbExists ? "Found (.ponder/pglite)" : "Not found"}`);

        results.push({
            caseId,
            caseName: caseConfig.description,
            recordCount: 0,
            dbExists,
        });
    }

    // Print summary table
    console.log("\n" + "═".repeat(60));
    console.log("DATABASE STATUS");
    console.log("═".repeat(60));
    console.table(results.map(r => ({
        Case: r.caseId,
        Name: r.caseName,
        "DB Exists": r.dbExists ? "Yes (PGlite)" : "No",
        "Note": r.dbExists ? "Run full benchmark to query records" : "-",
    })));
}

/**
 * Main function
 */
async function main(): Promise<void> {
    const args = process.argv.slice(2);
    let casesToRun: string[] = [];
    let iterations = 1;
    let queryOnly = false;

    // Parse arguments
    for (let i = 0; i < args.length; i++) {
        if (args[i] === "--iterations" && args[i + 1]) {
            iterations = parseInt(args[i + 1], 10);
            i++;
        } else if (args[i] === "--all") {
            casesToRun = Object.keys(CASES);
        } else if (args[i] === "--queryRecordsOnly") {
            queryOnly = true;
        } else if (CASES[args[i]]) {
            casesToRun.push(args[i]);
        }
    }

    if (casesToRun.length === 0) {
        console.error("Usage: bun bun_ponder_benchmark.ts <case_id> [--iterations N]");
        console.error("       bun bun_ponder_benchmark.ts --all [--iterations N]");
        console.error("       bun bun_ponder_benchmark.ts <case_id> --queryRecordsOnly");
        console.error("       bun bun_ponder_benchmark.ts --all --queryRecordsOnly");
        console.error("\nAvailable cases:", Object.keys(CASES).join(", "));
        process.exit(1);
    }

    // Handle query-only mode
    if (queryOnly) {
        await queryRecordsOnly(casesToRun);
        return;
    }

    console.log("╔════════════════════════════════════════════════════════════╗");
    console.log("║            Ponder Benchmark Automation Script              ║");
    console.log("╠════════════════════════════════════════════════════════════╣");
    console.log(`║ Cases to run: ${casesToRun.join(", ").padEnd(44)}║`);
    console.log(`║ Iterations: ${String(iterations).padEnd(47)}║`);
    console.log("╚════════════════════════════════════════════════════════════╝\n");

    const results: BenchmarkResult[] = [];

    for (const caseId of casesToRun) {
        const casePath = join(process.cwd(), CASES[caseId].path);

        // Check if ponder directory exists
        if (!existsSync(casePath)) {
            console.log(`[${caseId}] Ponder directory not found, skipping...`);
            continue;
        }

        try {
            for (let i = 1; i <= iterations; i++) {
                const result = await runBenchmark(caseId, i);
                results.push(result);

                // Save intermediate results
                saveResults(results);

                // Wait between iterations
                if (i < iterations) {
                    console.log(`\n[${caseId}] Waiting 10s before next iteration...\n`);
                    await sleep(10000);
                }
            }
        } catch (err) {
            console.error(`[${caseId}] Fatal error:`, err);
            results.push({
                caseName: CASES[caseId].description,
                caseId,
                iteration: 0,
                startTime: new Date().toISOString(),
                endTime: new Date().toISOString(),
                durationSeconds: 0,
                startBlock: 0,
                endBlock: 0,
                totalBlocks: 0,
                blocksPerSecond: 0,
                lastProgressPercent: 0,
                recordCount: 0,
                error: err instanceof Error ? err.message : String(err),
            });
        }
    }

    // Final report
    console.log("\n" + "═".repeat(60));
    console.log("BENCHMARK COMPLETE");
    console.log("═".repeat(60));
    saveResults(results);

    // Print summary table
    console.log("\nResults Summary:");
    console.table(results.map(r => ({
        Case: r.caseId,
        Iter: r.iteration,
        "Duration (s)": r.durationSeconds.toFixed(2),
        "Duration (min)": (r.durationSeconds / 60).toFixed(2),
        "Progress": `${r.lastProgressPercent.toFixed(1)}%`,
        "Blocks/s": r.blocksPerSecond.toFixed(2),
        Error: r.error ? "Yes" : "No",
    })));
}

// Run the benchmark
main().catch(console.error);
