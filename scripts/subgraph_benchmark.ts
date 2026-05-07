/**
 * Subgraph (The Graph) Benchmark Automation Script - Cloud Version
 * 
 * This script automates running Subgraph indexer benchmarks by:
 * 1. Parsing subgraph.yaml to get block range
 * 2. Building the subgraph locally
 * 3. Deploying to The Graph Studio (cloud)
 * 4. Monitoring progress via GraphQL API
 * 5. Detecting completion and collecting metrics
 * 6. Generating benchmark reports (JSON and Markdown)
 * 
 * Required environment variables:
 * - GRAPH_DEPLOY_KEY: Your deploy key from The Graph Studio
 * - STUDIO_ID: Your Studio account ID (number in query URL)
 * 
 * Optional environment variables:
 * - GRAPH_API_KEY: API key for querying (if using Gateway)
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
const POLL_INTERVAL_MS = 30000; // 30 seconds (cloud takes longer to update)
const TIMEOUT_MS = 480 * 60 * 1000; // 480 minutes max

// The Graph Studio API endpoint
const STUDIO_GRAPHQL_BASE = "https://api.studio.thegraph.com/query";

// Configuration for supported cases
// Each case needs SUBGRAPH_SLUG set in its .env file
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
 * Manually parse and set to ensure override works correctly
 */
function loadEnvFile(casePath: string, caseId: string): void {
    const envPath = join(casePath, ".env");
    if (existsSync(envPath)) {
        console.log(`[${caseId}] Loading environment from ${envPath}`);
        const content = readFileSync(envPath, "utf-8");
        const lines = content.split("\n");

        for (const line of lines) {
            const trimmed = line.trim();
            // Skip comments and empty lines
            if (!trimmed || trimmed.startsWith("#")) continue;

            const eqIndex = trimmed.indexOf("=");
            if (eqIndex === -1) continue;

            const key = trimmed.substring(0, eqIndex).trim();
            let value = trimmed.substring(eqIndex + 1).trim();

            // Remove quotes if present
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
 * Get deploy key from environment
 */
function getDeployKey(): string {
    const key = process.env.GRAPH_DEPLOY_KEY;
    if (!key) {
        throw new Error("GRAPH_DEPLOY_KEY environment variable is required. Get it from https://thegraph.com/studio");
    }
    return key;
}

/**
 * Deploy subgraph to The Graph Studio
 * Uses the graph CLI with --deploy-key for authentication
 */
function deployToStudio(casePath: string, studioSlug: string, caseId: string): string {
    console.log(`[${caseId}] Deploying to The Graph Studio: ${studioSlug}...`);

    const deployKey = getDeployKey();
    const versionLabel = `v${Date.now()}`;

    try {
        // Use --deploy-key for authentication (new CLI syntax)
        const output = execSync(
            `npx graph deploy ${studioSlug} --deploy-key ${deployKey} --version-label ${versionLabel}`,
            {
                cwd: casePath,
                encoding: "utf-8",
                stdio: ["pipe", "pipe", "inherit"],
            }
        );

        // Try to extract deployment ID from output
        const deploymentMatch = output.match(/Deployment ID: (Qm[a-zA-Z0-9]+)/);
        const deploymentId = deploymentMatch ? deploymentMatch[1] : versionLabel;

        console.log(`[${caseId}] Deployed successfully! Version: ${versionLabel}`);
        return deploymentId;
    } catch (e) {
        console.error(`[${caseId}] Deploy failed:`, e);
        throw e;
    }
}

/**
 * Get the GraphQL query URL for a Studio subgraph
 * Format: https://api.studio.thegraph.com/query/<STUDIO_ID>/<SLUG>/version/latest
 */
function getStudioQueryUrl(studioSlug: string): string {
    const studioId = process.env.STUDIO_ID;
    if (!studioId) {
        throw new Error("STUDIO_ID environment variable is required. Find it in your Studio query URL.");
    }
    return `https://api.studio.thegraph.com/query/${studioId}/${studioSlug}/version/latest`;
}

/**
 * Get headers for GraphQL requests (includes API key if available)
 */
function getQueryHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
        "Content-Type": "application/json",
    };

    const apiKey = process.env.GRAPH_API_KEY;
    if (apiKey) {
        headers["Authorization"] = `Bearer ${apiKey}`;
    }

    return headers;
}

/**
 * Query Studio subgraph for current indexed block using _meta
 */
async function queryCurrentBlock(queryUrl: string, caseId: string): Promise<number | null> {
    try {
        const query = `{
            _meta {
                block {
                    number
                }
            }
        }`;

        const response = await fetch(queryUrl, {
            method: "POST",
            headers: getQueryHeaders(),
            body: JSON.stringify({ query }),
        });

        if (!response.ok) {
            const text = await response.text();
            if (text.includes("indexing_progress")) {
                // Extract progress from error message if available
                console.log(`[${caseId}] Subgraph still syncing...`);
            }
            return null;
        }

        const data = await response.json() as any;
        return data?.data?._meta?.block?.number || null;
    } catch (e) {
        return null;
    }
}

/**
 * Query for highest block number from entities (as fallback)
 */
async function queryMaxBlockFromEntities(queryUrl: string, entityType: string, caseId: string): Promise<number | null> {
    try {
        const query = `{
            ${entityType}(first: 1, orderBy: blockNumber, orderDirection: desc) {
                blockNumber
            }
        }`;

        const response = await fetch(queryUrl, {
            method: "POST",
            headers: getQueryHeaders(),
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
 * Use GraphQL introspection to find the collection query name for an entity
 * This handles cases where entities have non-standard naming (e.g., "Accounts" already plural)
 */
async function findCollectionQueryName(queryUrl: string, entityType: string): Promise<string | null> {
    try {
        // Introspect the schema to find queries that accept 'first' argument (collection queries)
        const introspectionQuery = `{
            __schema {
                queryType {
                    fields {
                        name
                        args {
                            name
                        }
                    }
                }
            }
        }`;

        const response = await fetch(queryUrl, {
            method: "POST",
            headers: getQueryHeaders(),
            body: JSON.stringify({ query: introspectionQuery }),
        });

        if (!response.ok) return null;

        const data = await response.json() as any;
        const fields = data?.data?.__schema?.queryType?.fields || [];

        // Look for queries that:
        // 1. Have 'first' argument (collection queries)
        // 2. Match the entity type name (case-insensitive)
        const entityLower = entityType.toLowerCase();

        for (const field of fields) {
            const hasFirstArg = field.args?.some((arg: any) => arg.name === "first");
            if (hasFirstArg) {
                const fieldLower = field.name.toLowerCase();
                // Check various naming patterns
                if (fieldLower === entityLower ||
                    fieldLower === entityLower + "s" ||
                    fieldLower === entityLower + "es" ||
                    fieldLower.replace("_collection", "") === entityLower) {
                    return field.name;
                }
            }
        }

        // Fallback: find any field that starts with the entity name and has 'first' arg
        for (const field of fields) {
            const hasFirstArg = field.args?.some((arg: any) => arg.name === "first");
            if (hasFirstArg && field.name.toLowerCase().startsWith(entityLower.substring(0, 5))) {
                return field.name;
            }
        }

        return null;
    } catch (e) {
        console.error("Introspection failed:", e);
        return null;
    }
}

/**
 * Query subgraph for total record count (data completeness)
 * Uses cursor-based pagination (id_gt) instead of skip to avoid The Graph's 5000 skip limit
 */
async function queryRecordCount(queryUrl: string, entityType: string): Promise<number> {
    try {
        // First, find the correct collection query name via introspection
        let collectionQueryName = await findCollectionQueryName(queryUrl, entityType);

        if (!collectionQueryName) {
            console.log(`  Could not find collection query for '${entityType}', trying default...`);
            collectionQueryName = entityType;
        } else if (collectionQueryName !== entityType) {
            console.log(`  Found collection query: '${collectionQueryName}' for entity '${entityType}'`);
        }

        let totalCount = 0;
        let lastId = "";
        const batchSize = 1000;
        let iterations = 0;
        const maxIterations = 10000; // Safety limit: 10M records max

        while (iterations < maxIterations) {
            iterations++;

            // Use cursor-based pagination with id_gt instead of skip
            const whereClause = lastId ? `, where: { id_gt: "${lastId}" }` : "";
            const query = `{
                ${collectionQueryName}(first: ${batchSize}, orderBy: id, orderDirection: asc${whereClause}) {
                    id
                }
            }`;

            const response = await fetch(queryUrl, {
                method: "POST",
                headers: getQueryHeaders(),
                body: JSON.stringify({ query }),
            });

            if (!response.ok) {
                console.error(`Record count query failed: ${response.status}`);
                break;
            }

            const data = await response.json() as any;

            // Check for errors in response
            if (data.errors) {
                console.error(`GraphQL error:`, data.errors);
                break;
            }

            const records = data?.data?.[collectionQueryName] || [];

            if (records.length === 0) break;

            totalCount += records.length;
            lastId = records[records.length - 1].id;

            // Log progress every 50k records
            if (totalCount % 50000 === 0) {
                console.log(`  Counting records: ${totalCount}...`);
            }

            // Break if we got fewer than requested (end of data)
            if (records.length < batchSize) break;
        }

        return totalCount;
    } catch (e) {
        console.error(`Error querying record count:`, e);
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

    // Get SUBGRAPH_SLUG from env (loaded from case .env)
    const studioSlug = process.env.SUBGRAPH_SLUG;
    if (!studioSlug) {
        throw new Error(`SUBGRAPH_SLUG not set in ${casePath}/.env`);
    }

    console.log(`\n${"=".repeat(60)}`);
    console.log(`[${caseId}] Starting benchmark iteration ${iteration}`);
    console.log(`[${caseId}] ${caseName}`);
    console.log(`[${caseId}] Studio slug: ${studioSlug}`);
    console.log(`${"=".repeat(60)}\n`);

    // Parse config to get block range
    const config = parseSubgraphConfig(casePath);
    const totalBlocks = config.endBlock - config.startBlock;

    console.log(`[${caseId}] Block range: ${config.startBlock} -> ${config.endBlock} (${totalBlocks} blocks)`);

    // Get the query URL using Studio ID
    const queryUrl = getStudioQueryUrl(studioSlug);
    console.log(`[${caseId}] Query URL: ${queryUrl}`);

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

    // 5. Deploy to Studio - START TIMING HERE (auth is done via --deploy-key)
    const startTime = new Date();
    let deploymentId = "";
    try {
        deploymentId = deployToStudio(casePath, studioSlug, caseId);
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

    // 7. Monitor progress
    console.log(`\n[${caseId}] Monitoring indexing progress on Studio...`);
    console.log(`[${caseId}] Note: Check https://thegraph.com/studio for detailed status`);

    let error: string | undefined;
    let indexingComplete = false;
    let lastProgress = 0;
    let lastLogTime = Date.now();
    let currentBlock = config.startBlock;

    const timeoutTime = Date.now() + TIMEOUT_MS;

    // Poll for completion
    while (!indexingComplete && Date.now() < timeoutTime) {
        await sleep(POLL_INTERVAL_MS);

        // Query GraphQL for current block
        const block = await queryCurrentBlock(queryUrl, caseId);

        if (block === null) {
            // Try querying entities directly as fallback
            const entityBlock = await queryMaxBlockFromEntities(queryUrl, caseConfig.entityType, caseId);
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

            // Check if complete (within 1 block of target)
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

    // Query record count for data completeness
    console.log(`\n[${caseId}] Querying record count for data completeness...`);
    const recordCount = await queryRecordCount(queryUrl, caseConfig.entityType);
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
        deploymentId,
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
    // Save JSON report
    const jsonPath = join(__dirname, "subgraph_benchmark_report.json");
    writeFileSync(jsonPath, JSON.stringify(results, null, 2));
    console.log(`\nSaved JSON report to: ${jsonPath}`);

    // Save Markdown report
    const mdPath = join(__dirname, "subgraph_benchmark_report.md");
    let md = "# Subgraph (The Graph Studio) Benchmark Report\n\n";
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
    console.log("Subgraph (The Graph Studio) Benchmark Script");
    console.log("=".repeat(60));

    // Parse command line arguments
    const args = process.argv.slice(2);

    if (args.length === 0 || args[0] === "--help" || args[0] === "-h") {
        console.log("\nUsage: bun subgraph_benchmark.ts <case1|case2|case3|case5|case6|all> [iterations]");
        console.log("\nEnvironment variables:");
        console.log("  GRAPH_DEPLOY_KEY       - Required. Your Studio deploy key");
        console.log("  SUBGRAPH_QUERY_URL     - Optional. GraphQL query URL for your subgraph");
        console.log("  STUDIO_ID              - Optional. Your Studio account ID");
        console.log("\nExample:");
        console.log("  GRAPH_DEPLOY_KEY=xxx bun subgraph_benchmark.ts case1 3");
        console.log("\nNote: You must create the subgraph in Studio dashboard first!");
        console.log("      The studioSlug in the script must match your Studio subgraph name.");
        process.exit(0);
    }

    const caseArg = args[0];
    const iterations = parseInt(args[1]) || 1;

    // Determine which cases to run
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

    console.log(`\nRunning benchmarks for: ${casesToRun.join(", ")}`);
    console.log(`Iterations per case: ${iterations}`);

    const results: BenchmarkResult[] = [];

    for (const caseId of casesToRun) {
        try {
            for (let i = 1; i <= iterations; i++) {
                const result = await runBenchmark(caseId, i);
                results.push(result);

                // Save intermediate results
                saveResults(results);

                // Wait between iterations
                if (i < iterations) {
                    console.log(`\n[${caseId}] Waiting 60s before next iteration (let Studio stabilize)...\n`);
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
    console.log("BENCHMARK COMPLETE");
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
