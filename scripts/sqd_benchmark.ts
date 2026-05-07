/**
 * SQD (Subsquid) Benchmark Automation Script
 * 
 * This script automates running Subsquid indexer benchmarks by:
 * 1. Parsing processor.ts to get block range
 * 2. Starting Docker Postgres database
 * 3. Building and running the squid processor
 * 4. Monitoring progress via log parsing
 * 5. Detecting completion and collecting metrics
 * 6. Generating benchmark reports (JSON and Markdown)
 */

import { spawn, type Subprocess } from "bun";
import { join, dirname } from "path";
import { rmSync, existsSync, readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Configuration constants
const POLL_INTERVAL_MS = 5000; // 5 seconds
const TIMEOUT_MS = 120 * 60 * 1000; // 120 minutes max

// Configuration for supported cases
const CASES: Record<string, { path: string; description: string; entityTable: string }> = {
    case1: {
        path: "case_1_lbtc_event_only/sqd",
        description: "LBTC Event Only Benchmark",
        entityTable: "transfer",
    },
    case2: {
        path: "case_2_lbtc_full/sqd",
        description: "LBTC Full Benchmark",
        entityTable: "accounts",
    },
    case3: {
        path: "case_3_ethereum_block/sqd",
        description: "Ethereum Block Benchmark",
        entityTable: "block",
    },
    case4: {
        path: "case_4_on_transaction/sqd",
        description: "On Transaction Benchmark",
        entityTable: "gas_spent",
    },
    case5: {
        path: "case_5_on_trace/sqd",
        description: "On Trace Benchmark",
        entityTable: "swap",
    },
    case6: {
        path: "case_6_template/sqd",
        description: "Template (Factory) Benchmark",
        entityTable: "uniswap_v2_event",
    },
};

interface SqdConfig {
    startBlock: number;
    endBlock: number;
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
 * Parse processor.ts or main.ts to extract block range
 */
function parseSqdConfig(casePath: string): SqdConfig {
    // Try processor.ts first, then main.ts (case 6 uses main.ts)
    let content = "";
    const processorPath = join(casePath, "src/processor.ts");
    const mainPath = join(casePath, "src/main.ts");

    if (existsSync(processorPath)) {
        content = readFileSync(processorPath, "utf-8");
    } else if (existsSync(mainPath)) {
        content = readFileSync(mainPath, "utf-8");
    } else {
        throw new Error(`No processor.ts or main.ts found in ${casePath}/src/`);
    }

    // Extract from and to from .setBlockRange({ from: X, to: Y })
    const fromMatch = content.match(/\.setBlockRange\s*\(\s*\{[^}]*from\s*:\s*(\d+)/);
    const toMatch = content.match(/\.setBlockRange\s*\(\s*\{[^}]*to\s*:\s*(\d+)/);

    return {
        startBlock: fromMatch ? parseInt(fromMatch[1], 10) : 0,
        endBlock: toMatch ? parseInt(toMatch[1], 10) : 0,
    };
}

/**
 * Start Docker containers and wait for Postgres to be ready
 */
async function startDocker(caseDir: string, caseId: string): Promise<void> {
    console.log(`[${caseId}] Starting Docker Postgres...`);
    const dbPort = process.env.DB_PORT || "23798";

    try {
        execSync(`DB_PORT=${dbPort} docker compose up -d`, {
            cwd: caseDir,
            stdio: "inherit",
        });
    } catch (e) {
        console.error(`[${caseId}] Failed to start Docker:`, e);
        throw e;
    }

    // Wait for Postgres to be ready (retry loop)
    console.log(`[${caseId}] Waiting for Postgres to be ready...`);
    const maxRetries = 30;
    for (let i = 0; i < maxRetries; i++) {
        await sleep(1000);
        try {
            // Try to connect to Postgres and create the database if it doesn't exist
            execSync(
                `PGPASSWORD=postgres psql -h localhost -p ${dbPort} -U postgres -c "SELECT 1" 2>/dev/null`,
                { stdio: "pipe" }
            );
            console.log(`[${caseId}] Postgres is ready!`);

            // Ensure the squid database exists
            try {
                execSync(
                    `PGPASSWORD=postgres psql -h localhost -p ${dbPort} -U postgres -c "CREATE DATABASE squid" 2>/dev/null`,
                    { stdio: "pipe" }
                );
                console.log(`[${caseId}] Created squid database`);
            } catch (e) {
                // Database might already exist, that's fine
            }
            return;
        } catch (e) {
            // PostgreSQL not ready yet
            if (i % 5 === 0) {
                console.log(`[${caseId}] Waiting for Postgres... (${i + 1}/${maxRetries})`);
            }
        }
    }
    throw new Error("Postgres did not become ready in time");
}

/**
 * Stop Docker containers
 */
async function stopDocker(caseDir: string, caseId: string): Promise<void> {
    console.log(`[${caseId}] Stopping Docker containers...`);
    try {
        execSync("docker compose down -v", {
            cwd: caseDir,
            stdio: "pipe",
        });
    } catch (e) {
        // Ignore errors - containers might not exist
    }
}

/**
 * Parse progress from SQD logs
 * Looking for patterns like: "Processed X transfers from Y to Z"
 */
function parseProgress(logContent: string, endBlock: number): { percent: number; currentBlock: number } {
    let currentBlock = 0;

    // Parse block numbers from "Processed X transfers from Y to Z" or similar patterns
    const blockMatches = logContent.match(/from (\d+) to (\d+)/gi);
    if (blockMatches && blockMatches.length > 0) {
        const lastMatch = blockMatches[blockMatches.length - 1].match(/from (\d+) to (\d+)/i);
        if (lastMatch) {
            currentBlock = parseInt(lastMatch[2], 10);
        }
    }

    // Also try to match "saving X blocks" or block height patterns
    const heightMatches = logContent.match(/block (\d+)/gi);
    if (heightMatches && heightMatches.length > 0) {
        for (const match of heightMatches) {
            const blockNum = parseInt(match.replace(/block /i, ""), 10);
            if (blockNum > currentBlock) {
                currentBlock = blockNum;
            }
        }
    }

    const percent = endBlock > 0 ? (currentBlock / endBlock) * 100 : 0;
    return { percent: Math.min(percent, 100), currentBlock };
}

/**
 * Check if indexing is complete
 * SQD processor exits when it reaches the end block
 */
function isIndexingComplete(logContent: string, processExited: boolean): boolean {
    // SQD process exits when done, so check if process exited
    if (processExited) {
        return true;
    }

    // Check for completion indicators
    return (
        logContent.includes("squid sync is complete") ||
        logContent.includes("Completed indexing") ||
        logContent.includes("All blocks processed")
    );
}

/**
 * Sleep helper
 */
function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Query PostgreSQL for total record count (data completeness)
 */
function queryRecordCount(entityTable: string, caseId: string): number {
    const dbPort = process.env.DB_PORT || "23798";
    try {
        const result = execSync(
            `PGPASSWORD=postgres psql -h localhost -p ${dbPort} -U postgres -d squid -t -c "SELECT COUNT(*) FROM ${entityTable}" 2>/dev/null`,
            { encoding: "utf-8" }
        );
        const count = parseInt(result.trim(), 10);
        console.log(`[${caseId}] Total records indexed: ${count}`);
        return isNaN(count) ? 0 : count;
    } catch (e) {
        console.error(`[${caseId}] Error querying record count:`, e);
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
    const config = parseSqdConfig(casePath);
    const totalBlocks = config.endBlock - config.startBlock;

    console.log(`[${caseId}] Block range: ${config.startBlock} -> ${config.endBlock} (${totalBlocks} blocks)`);

    // 1. Stop any existing Docker containers first
    await stopDocker(casePath, caseId);

    // 2. Cleanup directories for fresh state
    console.log(`[${caseId}] Cleaning up artifacts...`);
    const dirsToClean = ["lib", "db/migrations", "node_modules"];
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

    // 3. Install dependencies
    console.log(`\n[${caseId}] Installing dependencies with Bun...`);
    const installProc = spawn({
        cmd: ["bun", "install"],
        cwd: casePath,
        stdout: "inherit",
        stderr: "inherit",
    });
    await installProc.exited;

    // 4. Run codegen (squid-typeorm-codegen)
    console.log(`\n[${caseId}] Running codegen...`);
    try {
        execSync("npx squid-typeorm-codegen", { cwd: casePath, stdio: "inherit" });
    } catch (e) {
        console.log(`[${caseId}] Codegen skipped or failed (may already exist)`);
    }

    // 5. Build (clean lib folder then run tsc)
    console.log(`\n[${caseId}] Building...`);
    try {
        execSync("rm -rf lib && npx tsc", { cwd: casePath, stdio: "inherit" });
    } catch (e) {
        console.error(`[${caseId}] Build failed:`, e);
        throw e;
    }

    // 6. Start Docker
    await startDocker(casePath, caseId);

    // 7. Generate and apply migrations
    console.log(`\n[${caseId}] Generating and applying migrations...`);
    const dbPort = process.env.DB_PORT || "23798";
    const dbUrl = `postgresql://postgres:postgres@localhost:${dbPort}/squid`;
    try {
        execSync("npx squid-typeorm-migration generate", {
            cwd: casePath,
            stdio: "inherit",
            env: { ...process.env, DB_URL: dbUrl }
        });
        execSync("npx squid-typeorm-migration apply", {
            cwd: casePath,
            stdio: "inherit",
            env: { ...process.env, DB_URL: dbUrl }
        });
    } catch (e) {
        console.log(`[${caseId}] Migration generation/apply failed:`, e);
    }

    // 8. Start processor and monitor
    const startTime = new Date();
    const logFile = join(casePath, "benchmark.log");

    console.log(`\n[${caseId}] Starting SQD processor...`);
    console.log(`[${caseId}] Log file: ${logFile}\n`);

    // Start process with output to log file (run node directly)
    const processProc = spawn({
        cmd: ["sh", "-c", `DB_URL="${dbUrl}" node --require=dotenv/config lib/main.js 2>&1 | tee "${logFile}"`],
        cwd: casePath,
        stdout: "inherit",
        stderr: "inherit",
    });

    let error: string | undefined;
    let indexingComplete = false;
    let lastProgress = 0;
    let lastLogTime = Date.now();
    let processExited = false;
    let currentBlock = config.startBlock;

    // Handle process exit
    processProc.exited.then((code) => {
        processExited = true;
        if (code !== 0 && code !== null) {
            error = `Process exited with code ${code}`;
        }
    });

    const timeoutTime = Date.now() + TIMEOUT_MS;

    // Poll for completion
    while (!indexingComplete && Date.now() < timeoutTime) {
        await sleep(POLL_INTERVAL_MS);

        // Read log file
        try {
            if (existsSync(logFile)) {
                const logContent = readFileSync(logFile, "utf-8");

                // Check completion
                if (isIndexingComplete(logContent, processExited)) {
                    console.log(`\n[${caseId}] ✓ Indexing complete!`);
                    indexingComplete = true;
                    break;
                }

                // Parse progress
                const progress = parseProgress(logContent, config.endBlock);
                if (progress.percent > lastProgress) {
                    lastProgress = progress.percent;
                }
                if (progress.currentBlock > currentBlock) {
                    currentBlock = progress.currentBlock;
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

    // Stop the process
    console.log(`\n[${caseId}] Stopping SQD processor...`);
    try {
        processProc.kill();
    } catch (e) {
        // Ignore if already dead
    }

    await sleep(1000);

    // Force kill any remaining processes
    try {
        execSync(`pkill -f "lib/main.js" || true`, { stdio: "ignore" });
    } catch (e) {
        // Ignore errors
    }

    // Query record count for data completeness BEFORE stopping Docker
    console.log(`\n[${caseId}] Querying record count for data completeness...`);
    const recordCount = queryRecordCount(caseConfig.entityTable, caseId);

    // Stop Docker
    await stopDocker(casePath, caseId);

    // Check for timeout
    if (!indexingComplete && !error) {
        error = `Timeout: only reached ${lastProgress.toFixed(1)}% (block ${currentBlock}/${config.endBlock})`;
    }

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
        error,
    };

    console.log(`\n[${caseId}] Benchmark Result:`);
    console.log(`  Duration: ${durationSeconds.toFixed(2)}s (${(durationSeconds / 60).toFixed(2)} min)`);
    console.log(`  Progress: ${lastProgress.toFixed(1)}%`);
    console.log(`  Blocks/sec: ${result.blocksPerSecond.toFixed(2)}`);
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
    const jsonPath = join(__dirname, "sqd_benchmark_report.json");
    writeFileSync(jsonPath, JSON.stringify(results, null, 2));
    console.log(`\nSaved JSON report to: ${jsonPath}`);

    // Save Markdown report
    const mdPath = join(__dirname, "sqd_benchmark_report.md");
    let md = "# SQD (Subsquid) Benchmark Report\n\n";
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
 * Main function
 */
async function main(): Promise<void> {
    const args = process.argv.slice(2);
    let casesToRun: string[] = [];
    let iterations = 1;

    // Parse arguments
    for (let i = 0; i < args.length; i++) {
        if (args[i] === "--iterations" && args[i + 1]) {
            iterations = parseInt(args[i + 1], 10);
            i++;
        } else if (args[i] === "--all") {
            casesToRun = Object.keys(CASES);
        } else if (CASES[args[i]]) {
            casesToRun.push(args[i]);
        }
    }

    if (casesToRun.length === 0) {
        console.error("Usage: bun sqd_benchmark.ts <case_id> [--iterations N]");
        console.error("       bun sqd_benchmark.ts --all [--iterations N]");
        console.error("\nAvailable cases:", Object.keys(CASES).join(", "));
        process.exit(1);
    }

    console.log("╔════════════════════════════════════════════════════════════╗");
    console.log("║          SQD (Subsquid) Benchmark Automation Script        ║");
    console.log("╠════════════════════════════════════════════════════════════╣");
    console.log(`║ Cases to run: ${casesToRun.join(", ").padEnd(44)}║`);
    console.log(`║ Iterations: ${String(iterations).padEnd(47)}║`);
    console.log("╚════════════════════════════════════════════════════════════╝\n");

    const results: BenchmarkResult[] = [];

    for (const caseId of casesToRun) {
        const casePath = join(process.cwd(), CASES[caseId].path);

        // Check if sqd directory exists
        if (!existsSync(casePath)) {
            console.log(`[${caseId}] SQD directory not found, skipping...`);
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
