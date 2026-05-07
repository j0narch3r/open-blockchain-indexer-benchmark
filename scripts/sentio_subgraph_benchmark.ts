/**
 * Sentio Hosted Subgraph Benchmark Automation Script
 * 
 * This script automates running Subgraph indexer benchmarks on Sentio by:
 * 1. Parsing subgraph.yaml to get block range
 * 2. Building the subgraph locally
 * 3. Deploying to Sentio (via @sentio/cli)
 * 4. Monitoring progress via Sentio Data Source page
 * 5. Detecting completion and collecting metrics
 * 6. Generating benchmark reports (JSON and Markdown)
 * 
 * Required environment variables (in case .env file):
 * - SENTIO_API_KEY: Your Sentio API key
 * - SENTIO_OWNER: Your username or team slug on Sentio
 * - SENTIO_PROJECT_NAME: Project name on Sentio
 */

import { spawn, type Subprocess } from "bun";
import { join, dirname } from "path";
import { rmSync, existsSync, readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { execSync } from "child_process";
import { parse as parseYaml } from "yaml";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Configuration constants
const POLL_INTERVAL_MS = 30000; // 30 seconds
const TIMEOUT_MS = 480 * 60 * 1000; // 480 minutes max

// Sentio API base URL
const SENTIO_API_BASE = "https://test.sentio.xyz/api/v1";

// Configuration for supported cases
const CASES: Record<string, { path: string; description: string; entityType: string }> = {
    case1: {
        path: "case_1_lbtc_event_only/subgraph",
        description: "LBTC Event Only Benchmark",
        entityType: "transfers",
    },
    case2: {
        path: "case_2_lbtc_full/subgraph",
        description: "LBTC Full Benchmark",
        entityType: "accounts",
    },
    case3: {
        path: "case_3_ethereum_block/subgraph",
        description: "Ethereum Block Benchmark",
        entityType: "blocks",
    },
    case5: {
        path: "case_5_on_trace/subgraph",
        description: "On Trace Benchmark",
        entityType: "swaps",
    },
    case6: {
        path: "case_6_template/subgraph",
        description: "Template (Factory) Benchmark",
        entityType: "uniswapV2Events",
    },
};

interface SubgraphConfig {
    startBlock: number;
    endBlock: number;
    name: string;
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
    recordCount: number;
    deploymentId?: string;
    graphqlUrl?: string;
    error?: string;
}

/**
 * Parse subgraph.yaml to extract block range and name
 */
function parseSubgraphConfig(casePath: string): SubgraphConfig {
    const subgraphPath = join(casePath, "subgraph.yaml");
    const content = readFileSync(subgraphPath, "utf-8");
    const config = parseYaml(content);

    const dataSource = config.dataSources?.[0];
    const source = dataSource?.source || {};

    return {
        startBlock: source.startBlock || 0,
        endBlock: source.endBlock || 0,
        name: dataSource?.name || "subgraph",
    };
}

/**
 * Load environment variables from case folder's .env file
 */
function loadEnvFile(casePath: string, caseId: string): void {
    const envPath = join(casePath, ".env");
    if (existsSync(envPath)) {
        console.log(`[${caseId}] Loading environment from ${envPath}`);
        const content = readFileSync(envPath, "utf-8");
        const lines = content.split("\n");

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith("#")) continue;

            const eqIndex = trimmed.indexOf("=");
            if (eqIndex === -1) continue;

            const key = trimmed.substring(0, eqIndex).trim();
            let value = trimmed.substring(eqIndex + 1).trim();

            if ((value.startsWith('"') && value.endsWith('"')) ||
                (value.startsWith("'") && value.endsWith("'"))) {
                value = value.slice(1, -1);
            }

            process.env[key] = value;
            console.log(`[${caseId}]   Set ${key}=${value.substring(0, 20)}${value.length > 20 ? '...' : ''}`);
        }
    } else {
        console.log(`[${caseId}] No .env file found at ${envPath}`);
    }
}

/**
 * Get Sentio configuration from environment
 */
function getSentioConfig(): { owner: string; projectName: string; apiKey?: string } {
    const owner = process.env.SENTIO_OWNER;
    const projectName = process.env.SENTIO_PROJECT_NAME;

    if (!owner) {
        throw new Error("SENTIO_OWNER not set in .env file");
    }
    if (!projectName) {
        throw new Error("SENTIO_PROJECT_NAME not set in .env file");
    }

    return {
        owner,
        projectName,
        apiKey: process.env.SENTIO_API_KEY,
    };
}

/**
 * Deploy subgraph to Sentio
 * Note: Sentio CLI sometimes has a bug where it exits with code 1 even after successful deploy
 */
function deployToSentio(casePath: string, owner: string, projectName: string, caseId: string): string {
    console.log(`[${caseId}] Deploying to Sentio: ${owner}/${projectName}...`);

    const versionLabel = `v${Date.now()}`;

    // Deploy using Sentio CLI (bunx is faster than npx)
    const cmd = `bunx @sentio/cli graph deploy --owner ${owner} --name ${projectName} --api-key ${process.env.SENTIO_API_KEY} --host https://test.sentio.xyz`;
    console.log(`[${caseId}] Running: ${cmd}`);

    try {
        const output = execSync(cmd, {
            cwd: casePath,
            encoding: "utf-8",
            stdio: ["pipe", "pipe", "pipe"],
            env: {
                ...process.env,
                SENTIO_API_KEY: process.env.SENTIO_API_KEY,
            },
        });

        console.log(`[${caseId}] Deploy output:\n${output}`);

        // Extract IPFS hash from output if available
        const ipfsMatch = output.match(/Qm[a-zA-Z0-9]+/);
        if (ipfsMatch) {
            console.log(`[${caseId}] IPFS hash: ${ipfsMatch[0]}`);
        }

        console.log(`[${caseId}] Deployed successfully!`);
        return versionLabel;
    } catch (e: any) {
        // Log full response details for debugging
        const stdout = e?.stdout || "";
        const stderr = e?.stderr || "";
        const status = e?.status;
        const signal = e?.signal;

        console.log(`\n[${caseId}] ====== SENTIO UPLOAD RESPONSE ======`);
        console.log(`[${caseId}] Exit code: ${status}`);
        console.log(`[${caseId}] Signal: ${signal}`);
        console.log(`[${caseId}] STDOUT:\n${stdout}`);
        console.log(`[${caseId}] STDERR:\n${stderr}`);
        console.log(`[${caseId}] Full error object:`, JSON.stringify({
            message: e?.message,
            name: e?.name,
            status: e?.status,
            signal: e?.signal,
            stdout: e?.stdout,
            stderr: e?.stderr,
        }, null, 2));
        console.log(`[${caseId}] ====================================\n`);

        // Check if the error contains "Graph deploy success" in stdout
        // This handles a Sentio CLI bug where it exits with code 1 after successful deploy
        const hasSuccess = stdout.includes("Graph deploy success") || stdout.includes("Build completed");

        if (hasSuccess) {
            console.log(`[${caseId}] Deploy completed (ignoring CLI exit code bug)`);

            // Extract IPFS hash from output
            const ipfsMatch = stdout.match(/Qm[a-zA-Z0-9]+/);
            if (ipfsMatch) {
                console.log(`[${caseId}] IPFS hash: ${ipfsMatch[0]}`);
            }

            return versionLabel;
        }

        console.error(`[${caseId}] Deploy failed`);
        throw e;
    }
}

/**
 * Get Sentio GraphQL endpoint for a subgraph project
 */
function getSentioGraphqlUrl(owner: string, projectName: string): string {
    // Sentio GraphQL endpoint format
    return `${SENTIO_API_BASE}/graphql/${owner}/${projectName}`;
}

/**
 * Query Sentio project status via API
 */
async function querySentioStatus(owner: string, projectName: string, apiKey?: string): Promise<{ synced: boolean; progress?: number; currentBlock?: number }> {
    try {
        const url = `${SENTIO_API_BASE}/processors/${owner}/${projectName}`;
        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (apiKey) {
            headers["api-key"] = apiKey;
        }

        const response = await fetch(url, { headers });

        if (!response.ok) {
            return { synced: false };
        }

        const data = await response.json() as any;

        // Parse status from response
        const status = data?.processor?.status;
        const progress = data?.processor?.progress || 0;
        const currentBlock = data?.processor?.currentBlock;

        return {
            synced: status === "ACTIVE" || progress >= 100,
            progress,
            currentBlock,
        };
    } catch (e) {
        return { synced: false };
    }
}

/**
 * Query subgraph for current indexed block using _meta
 */
async function queryCurrentBlock(queryUrl: string, apiKey?: string): Promise<number | null> {
    try {
        const query = `{
            _meta {
                block {
                    number
                }
            }
        }`;

        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (apiKey) {
            headers["api-key"] = apiKey;
        }

        const response = await fetch(queryUrl, {
            method: "POST",
            headers,
            body: JSON.stringify({ query }),
        });

        if (!response.ok) {
            return null;
        }

        const data = await response.json() as any;
        return data?.data?._meta?.block?.number || null;
    } catch (e) {
        return null;
    }
}

/**
 * Query for highest block number from entities
 */
async function queryMaxBlockFromEntities(queryUrl: string, entityType: string, apiKey?: string): Promise<number | null> {
    try {
        const query = `{
            ${entityType}(first: 1, orderBy: blockNumber, orderDirection: desc) {
                blockNumber
            }
        }`;

        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (apiKey) {
            headers["api-key"] = apiKey;
        }

        const response = await fetch(queryUrl, {
            method: "POST",
            headers,
            body: JSON.stringify({ query }),
        });

        if (!response.ok) return null;

        const data = await response.json() as any;
        const records = data?.data?.[entityType];
        if (records && records.length > 0) {
            return Number(records[0].blockNumber);
        }
        return null;
    } catch (e) {
        return null;
    }
}

/**
 * Query subgraph for total record count using Sentio SQL API
 * This is more reliable than GraphQL pagination
 */
async function queryRecordCountSQL(owner: string, projectName: string, entityType: string, apiKey?: string): Promise<number> {
    try {
        // Sentio SQL endpoint
        const url = `${SENTIO_API_BASE}/analytics/${owner}/${projectName}/sql/execute`;

        // SQL to count records - entity type needs to be capitalized for table name
        // e.g., "transfers" -> "Transfer" table
        const tableName = entityType.charAt(0).toUpperCase() + entityType.slice(1);
        // Remove trailing 's' for singular table name if needed
        const singularTable = tableName.endsWith('s') ? tableName.slice(0, -1) : tableName;

        const sqlQuery = `SELECT COUNT(*) as count FROM ${singularTable}`;
        console.log(`  SQL Query: ${sqlQuery}`);

        const headers: Record<string, string> = {
            "Content-Type": "application/json",
        };
        if (apiKey) {
            headers["api-key"] = apiKey;
        }

        const body = {
            sqlQuery: {
                sql: sqlQuery
            }
        };

        const response = await fetch(url, {
            method: "POST",
            headers,
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            console.error(`  SQL query failed: HTTP ${response.status} ${response.statusText}`);
            const text = await response.text();
            console.error(`  Response body: ${text.substring(0, 300)}`);
            return 0;
        }

        const data = await response.json() as any;

        if (data.error) {
            console.error(`  SQL error:`, data.error);
            return 0;
        }

        // Parse result from Sentio SQL response
        const rows = data?.result?.rows || [];
        if (rows.length > 0) {
            const count = parseInt(rows[0].count || rows[0].COUNT || "0");
            console.log(`  SQL result: ${count} records`);
            return count;
        }

        return 0;
    } catch (e) {
        console.error(`Error querying record count via SQL:`, e);
        return 0;
    }
}

/**
 * Sleep helper
 */
function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
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

    // Load .env from case folder FIRST
    loadEnvFile(casePath, caseId);

    const sentioConfig = getSentioConfig();

    console.log(`\n${"=".repeat(60)}`);
    console.log(`[${caseId}] Starting Sentio Subgraph benchmark iteration ${iteration}`);
    console.log(`[${caseId}] ${caseName}`);
    console.log(`[${caseId}] Sentio project: ${sentioConfig.owner}/${sentioConfig.projectName}`);
    console.log(`${"=".repeat(60)}\n`);

    // Parse config to get block range
    const config = parseSubgraphConfig(casePath);
    const totalBlocks = config.endBlock - config.startBlock;

    console.log(`[${caseId}] Block range: ${config.startBlock} -> ${config.endBlock} (${totalBlocks} blocks)`);

    // 1. Cleanup build artifacts
    console.log(`[${caseId}] Cleaning up build artifacts...`);
    const dirsToClean = ["build"];
    for (const dir of dirsToClean) {
        const fullPath = join(casePath, dir);
        if (existsSync(fullPath)) {
            rmSync(fullPath, { recursive: true, force: true });
        }
    }

    // 2. Install dependencies
    console.log(`\n[${caseId}] Installing dependencies...`);
    const installProc = spawn({
        cmd: ["yarn", "install"],
        cwd: casePath,
        stdout: "inherit",
        stderr: "inherit",
    });
    await installProc.exited;

    // 3. Run codegen
    console.log(`\n[${caseId}] Running codegen...`);
    try {
        execSync("yarn codegen", { cwd: casePath, stdio: "inherit" });
    } catch (e) {
        console.error(`[${caseId}] Codegen failed:`, e);
        throw e;
    }

    // 4. Build
    console.log(`\n[${caseId}] Building subgraph...`);
    try {
        execSync("yarn build", { cwd: casePath, stdio: "inherit" });
    } catch (e) {
        console.error(`[${caseId}] Build failed:`, e);
        throw e;
    }

    // 5. Deploy to Sentio - START TIMING HERE
    const startTime = new Date();
    let versionLabel = "";
    try {
        versionLabel = deployToSentio(casePath, sentioConfig.owner, sentioConfig.projectName, caseId);
    } catch (e) {
        return {
            caseName,
            caseId,
            iteration,
            startTime: startTime.toISOString(),
            endTime: new Date().toISOString(),
            durationSeconds: 0,
            startBlock: config.startBlock,
            endBlock: config.endBlock,
            totalBlocks,
            blocksPerSecond: 0,
            lastProgressPercent: 0,
            recordCount: 0,
            error: `Deploy failed: ${e}`,
        };
    }

    // Get GraphQL endpoint
    const queryUrl = getSentioGraphqlUrl(sentioConfig.owner, sentioConfig.projectName);
    console.log(`[${caseId}] Query URL: ${queryUrl}`);

    // 6. Monitor progress
    console.log(`\n[${caseId}] Monitoring indexing progress on Sentio...`);
    console.log(`[${caseId}] Check progress at: https://app.sentio.xyz/${sentioConfig.owner}/${sentioConfig.projectName}`);

    let error: string | undefined;
    let indexingComplete = false;
    let lastProgress = 0;
    let lastLogTime = Date.now();
    let currentBlock = config.startBlock;

    const timeoutTime = Date.now() + TIMEOUT_MS;

    // Poll for completion
    while (!indexingComplete && Date.now() < timeoutTime) {
        await sleep(POLL_INTERVAL_MS);

        // Try Sentio status API first
        const status = await querySentioStatus(sentioConfig.owner, sentioConfig.projectName, sentioConfig.apiKey);
        if (status.synced) {
            console.log(`\n[${caseId}] ✓ Indexing complete (Sentio status: synced)`);
            indexingComplete = true;
            lastProgress = 100;
            break;
        }
        if (status.progress && status.progress > lastProgress) {
            lastProgress = status.progress;
        }
        if (status.currentBlock && status.currentBlock > currentBlock) {
            currentBlock = status.currentBlock;
        }

        // Try querying current block via GraphQL
        const block = await queryCurrentBlock(queryUrl, sentioConfig.apiKey);

        if (block === null) {
            // Try querying entities directly as fallback
            const entityBlock = await queryMaxBlockFromEntities(queryUrl, caseConfig.entityType, sentioConfig.apiKey);
            if (entityBlock !== null) {
                currentBlock = entityBlock;
            }
        } else {
            currentBlock = block;
        }

        if (currentBlock > config.startBlock) {
            const progress = config.endBlock > 0
                ? ((currentBlock - config.startBlock) / (config.endBlock - config.startBlock)) * 100
                : 0;
            if (progress > lastProgress) {
                lastProgress = Math.min(progress, 100);
            }

            // Check if complete
            if (currentBlock >= config.endBlock - 1) {
                console.log(`\n[${caseId}] ✓ Indexing complete! Reached block ${currentBlock}`);
                indexingComplete = true;
                break;
            }
        }

        // Log progress every 30 seconds
        if (Date.now() - lastLogTime > 30000) {
            const elapsed = (Date.now() - startTime.getTime()) / 1000;
            console.log(`\n[${caseId}] Progress: ${lastProgress.toFixed(1)}% | Block: ${currentBlock}/${config.endBlock} | Elapsed: ${elapsed.toFixed(0)}s`);
            lastLogTime = Date.now();
        }
    }

    const endTime = new Date();
    const durationSeconds = (endTime.getTime() - startTime.getTime()) / 1000;

    // Query record count for data completeness using SQL
    console.log(`\n[${caseId}] Querying record count for data completeness...`);
    const recordCount = await queryRecordCountSQL(sentioConfig.owner, sentioConfig.projectName, caseConfig.entityType, sentioConfig.apiKey);
    console.log(`[${caseId}] Total records indexed: ${recordCount}`);

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
        deploymentId: versionLabel,
        graphqlUrl: queryUrl,
        error,
    };

    console.log(`\n[${caseId}] Benchmark Result:`);
    console.log(`  Duration: ${durationSeconds.toFixed(2)}s (${(durationSeconds / 60).toFixed(2)} min)`);
    console.log(`  Progress: ${lastProgress.toFixed(1)}%`);
    console.log(`  Records: ${recordCount}`);
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
    const jsonPath = join(__dirname, "sentio_subgraph_benchmark_report.json");
    writeFileSync(jsonPath, JSON.stringify(results, null, 2));
    console.log(`\nSaved JSON report to: ${jsonPath}`);

    const mdPath = join(__dirname, "sentio_subgraph_benchmark_report.md");
    let md = "# Sentio Hosted Subgraph Benchmark Report\n\n";
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
        const avgRecords = caseResults.reduce((a, b) => a + b.recordCount, 0) / caseResults.length;

        md += `### ${caseId} - ${CASES[caseId].description}\n`;
        md += `- Iterations: ${caseResults.length}\n`;
        md += `- Average: ${avgDuration.toFixed(2)}s (${(avgDuration / 60).toFixed(2)} min)\n`;
        md += `- Min: ${minDuration.toFixed(2)}s\n`;
        md += `- Max: ${maxDuration.toFixed(2)}s\n`;
        md += `- Records: ${avgRecords.toFixed(0)}\n\n`;
    }

    writeFileSync(mdPath, md);
    console.log(`Saved Markdown report to: ${mdPath}`);
}

/**
 * Main function
 */
async function main(): Promise<void> {
    console.log("=".repeat(60));
    console.log("Sentio Hosted Subgraph Benchmark Script");
    console.log("=".repeat(60));

    // Check @sentio/cli is available
    try {
        execSync("bunx @sentio/cli --version", { stdio: "pipe" });
    } catch (e) {
        console.error("\n❌ WARNING: @sentio/cli may not be installed");
        console.log("The script will use bunx to run it.");
    }

    // Parse command line arguments
    const args = process.argv.slice(2);

    if (args.length === 0 || args[0] === "--help" || args[0] === "-h") {
        console.log("\nUsage: bun sentio_subgraph_benchmark.ts <case1|case2|case3|case5|case6|all> [iterations] [--query-only]");
        console.log("\nOptions:");
        console.log("  --query-only    Only query record count, skip deployment and monitoring");
        console.log("\nEnvironment variables (in case .env file):");
        console.log("  SENTIO_OWNER         - Required. Your username or team slug on Sentio");
        console.log("  SENTIO_PROJECT_NAME  - Required. Project name on Sentio");
        console.log("  SENTIO_API_KEY       - Required for SQL queries. API key for querying");
        console.log("\nExamples:");
        console.log("  bun sentio_subgraph_benchmark.ts case1");
        console.log("  bun sentio_subgraph_benchmark.ts case1 --query-only");
        console.log("\nNote: Create the Subgraph project in Sentio UI first!");
        console.log("      Visit: https://app.sentio.xyz → Create Project → Subgraph");
        process.exit(0);
    }

    // Check for --query-only flag
    const queryOnly = args.includes("--query-only");
    const filteredArgs = args.filter(a => a !== "--query-only");

    const caseArg = filteredArgs[0];
    const iterations = parseInt(filteredArgs[1]) || 1;

    let casesToRun: string[] = [];
    if (caseArg === "all") {
        casesToRun = Object.keys(CASES);
    } else if (CASES[caseArg]) {
        casesToRun = [caseArg];
    } else {
        console.error(`Unknown case: ${caseArg}`);
        console.log("Available cases:", Object.keys(CASES).join(", "));
        process.exit(1);
    }

    // Query-only mode: just test the record count query
    if (queryOnly) {
        console.log("\n🔍 Query-only mode: Testing SQL record count queries...\n");

        for (const caseId of casesToRun) {
            const caseConfig = CASES[caseId];
            const casePath = join(process.cwd(), caseConfig.path);

            // Load .env from case folder
            loadEnvFile(casePath, caseId);

            const sentioConfig = getSentioConfig();

            console.log(`\n[${caseId}] Testing SQL query for ${sentioConfig.owner}/${sentioConfig.projectName}`);
            console.log(`[${caseId}] Entity type: ${caseConfig.entityType}`);

            const recordCount = await queryRecordCountSQL(
                sentioConfig.owner,
                sentioConfig.projectName,
                caseConfig.entityType,
                sentioConfig.apiKey
            );

            console.log(`[${caseId}] ✓ Record count: ${recordCount}\n`);
        }

        console.log("Query-only test completed.");
        process.exit(0);
    }

    console.log(`\nRunning Sentio Subgraph benchmarks for: ${casesToRun.join(", ")}`);
    console.log(`Iterations per case: ${iterations}`);

    const results: BenchmarkResult[] = [];

    for (const caseId of casesToRun) {
        try {
            for (let i = 1; i <= iterations; i++) {
                const result = await runBenchmark(caseId, i);
                results.push(result);
                saveResults(results);

                if (i < iterations) {
                    console.log(`\n[${caseId}] Waiting 60s before next iteration...\n`);
                    await sleep(60000);
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
    console.log("SENTIO SUBGRAPH BENCHMARK COMPLETE");
    console.log("═".repeat(60));

    saveResults(results);

    // Print summary
    console.log("\nSummary:");
    for (const result of results) {
        const status = result.error ? `❌ ${result.error.substring(0, 30)}` : "✓";
        console.log(`  ${result.caseId} #${result.iteration}: ${result.durationSeconds.toFixed(2)}s | ${result.recordCount} records | ${status}`);
    }
}

// Run the benchmark
main().catch(console.error);
