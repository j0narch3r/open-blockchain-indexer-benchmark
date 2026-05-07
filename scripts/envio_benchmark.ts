/**
 * Envio Benchmark Automation Script
 * 
 * This script automates running Envio indexer benchmarks by:
 * 1. Parsing config.yaml to get block range
 * 2. Starting the indexer with pnpm dev
 * 3. Monitoring progress via GraphQL API and log parsing
 * 4. Detecting completion and collecting metrics
 * 5. Cleaning up Docker containers
 * 6. Generating benchmark reports
 */

import * as fs from 'fs';
import * as path from 'path';
import { spawn, ChildProcess, execSync } from 'child_process';
import axios from 'axios';
import * as dotenv from 'dotenv';
import { fileURLToPath } from 'url';
import { parse as parseYaml } from 'yaml';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration constants
const GRAPHQL_URL = 'http://localhost:8080/v1/graphql';
const GRAPHQL_PASSWORD = 'testing';
const POLL_INTERVAL_MS = 5000; // 5 seconds
const TIMEOUT_MS = 60 * 60 * 1000; // 60 minutes
const STARTUP_WAIT_MS = 30000; // Wait 30s for services to start

// Package manager command - use bun for best performance
// Set ENVIO_PM_CMD environment variable to override (e.g., 'pnpm' or 'npm')
const PM_CMD = process.env.ENVIO_PM_CMD || 'bun';

// Envio case directories with envio implementations
const ENVIO_CASES = [
    'case_1_lbtc_event_only',
    'case_2_lbtc_full',
    'case_6_template'
];

// Case-specific entity names for record count queries
const CASE_CONFIG: Record<string, { entityName: string }> = {
    'case_1_lbtc_event_only': { entityName: 'TransparentUpgradeableProxy_Transfer' },
    'case_2_lbtc_full': { entityName: 'Accounts' },
    'case_6_template': { entityName: 'Swap' }
};

interface EnvioConfig {
    name: string;
    networks: Array<{
        id: number;
        start_block: number;
        end_block: number;
        contracts: Array<{
            name: string;
            address?: string | string[];
            events: Array<{ event: string }>;
        }>;
    }>;
}

interface PerformanceMetrics {
    rpcTime: number;
    storageTime: number;
    calcTime: number;
    operationCount: number;
}

interface BenchmarkResult {
    caseName: string;
    iteration: number;
    startTime: string;
    endTime: string;
    durationSeconds: number;
    startBlock: number;
    endBlock: number;
    totalBlocks: number;
    blocksPerSecond: number;
    recordCount: number;  // Data completeness - total records indexed
    performanceMetrics?: PerformanceMetrics;
    error?: string;
}

// Global state for log parsing
let lastPerformanceMetrics: PerformanceMetrics | undefined;

/**
 * Parse config.yaml to get block range and configuration
 */
function parseEnvioConfig(caseDir: string): EnvioConfig {
    const configPath = path.join(caseDir, 'config.yaml');
    const content = fs.readFileSync(configPath, 'utf-8');
    return parseYaml(content) as EnvioConfig;
}

/**
 * Install dependencies if needed
 */
async function installDependencies(caseDir: string): Promise<void> {
    const nodeModulesPath = path.join(caseDir, 'node_modules');

    if (fs.existsSync(nodeModulesPath)) {
        console.log(`[${path.basename(caseDir)}] Dependencies already installed, skipping...`);
        return;
    }

    console.log(`[${path.basename(caseDir)}] Installing dependencies...`);
    execSync(`${PM_CMD} install`, { cwd: caseDir, stdio: 'inherit', shell: true });
}

/**
 * Run codegen if generated folder doesn't exist or is outdated
 */
async function runCodegen(caseDir: string, forceClean: boolean = false): Promise<void> {
    const generatedPath = path.join(caseDir, 'generated');
    const caseName = path.basename(caseDir);

    // Critical files that must exist for a valid generated folder
    const criticalFiles = [
        'src/db/Migrations.bs.js',
        'src/Handlers.gen.ts'
    ];

    // Check if generated folder is complete (has all critical files)
    const isGeneratedComplete = () => {
        if (!fs.existsSync(generatedPath)) return false;
        return criticalFiles.every(file =>
            fs.existsSync(path.join(generatedPath, file))
        );
    };

    if (forceClean && fs.existsSync(generatedPath)) {
        console.log(`[${caseName}] Cleaning generated folder (--clean flag)...`);
        fs.rmSync(generatedPath, { recursive: true, force: true });
    }

    // Also clean if generated folder is incomplete/corrupted
    if (fs.existsSync(generatedPath) && !isGeneratedComplete()) {
        console.log(`[${caseName}] Generated folder is incomplete, cleaning and regenerating...`);
        fs.rmSync(generatedPath, { recursive: true, force: true });
    }

    if (!fs.existsSync(generatedPath)) {
        console.log(`[${caseName}] Running codegen...`);
        execSync(`${PM_CMD} codegen`, { cwd: caseDir, stdio: 'inherit', shell: true });
    } else {
        console.log(`[${caseName}] Generated folder exists and is complete, skipping codegen...`);
    }
}

/**
 * Clean up Docker environment before running a test
 * This ensures a fresh state for each benchmark run
 */
async function cleanupEnvironment(caseDir: string): Promise<void> {
    const caseName = path.basename(caseDir);
    console.log(`[${caseName}] Cleaning up Docker environment...`);

    // Ensure port 9898 is free (used by Envio indexer)
    freePort(9898);

    try {
        // Use envio CLI to properly shut down containers
        execSync(`${PM_CMD} envio local docker down`, {
            cwd: caseDir,
            stdio: 'inherit',
            shell: true
        });
        console.log(`[${caseName}] Docker cleanup complete`);
    } catch (error) {
        // Ignore errors - containers might not exist
        console.log(`[${caseName}] Docker cleanup skipped (no containers running)`);
    }
}

/**
 * Kill any process listening on the specified port
 */
function freePort(port: number): void {
    try {
        // Find process ID using the port and kill it
        // lsof -ti:9898 returns the PIDs
        const cmd = `lsof -ti:${port} | xargs -r kill -9`;
        execSync(cmd, { stdio: 'ignore' });
    } catch (e) {
        // Ignore errors if no process is running
    }
}

/**
 * Parse performance metrics from log line
 * Format: [PERFORMANCE METRICS] Ops: 1000 | RPC: 10.50s | Storage: 5.20s | Calc: 0.30s | Total: 16.00s
 */
function parsePerformanceLog(line: string): PerformanceMetrics | null {
    const match = line.match(/\[PERFORMANCE METRICS\] Ops: (\d+) \| RPC: ([\d.]+)s \| Storage: ([\d.]+)s \| Calc: ([\d.]+)s/);
    if (match) {
        return {
            operationCount: parseInt(match[1], 10),
            rpcTime: parseFloat(match[2]),
            storageTime: parseFloat(match[3]),
            calcTime: parseFloat(match[4])
        };
    }
    return null;
}

/**
 * Query GraphQL for current indexed block number
 */
async function queryCurrentBlock(entityName: string = 'Transfer'): Promise<number | null> {
    try {
        // Try querying _meta first (standard in many indexers)
        const metaQuery = `
            query {
                _meta {
                    block {
                        number
                    }
                }
            }
        `;

        try {
            const metaResponse = await axios.post(
                GRAPHQL_URL,
                { query: metaQuery },
                {
                    headers: {
                        'Content-Type': 'application/json',
                        'x-hasura-admin-secret': GRAPHQL_PASSWORD
                    },
                    timeout: 5000
                }
            );

            if (metaResponse.data?.data?._meta?.block?.number) {
                return metaResponse.data.data._meta.block.number;
            }
        } catch {
            // _meta might not be available, try entity query
        }

        // Fallback: query the entity with highest block number
        // Entity names in GraphQL are typically lowercase
        const entityQuery = `
            query {
                ${entityName.toLowerCase()}(order_by: {blockNumber: desc}, limit: 1) {
                    blockNumber
                }
            }
        `;

        const response = await axios.post(
            GRAPHQL_URL,
            { query: entityQuery },
            {
                headers: {
                    'Content-Type': 'application/json',
                    'x-hasura-admin-secret': GRAPHQL_PASSWORD
                },
                timeout: 5000
            }
        );

        const data = response.data?.data?.[entityName.toLowerCase()];
        if (data && data.length > 0) {
            return Number(data[0].blockNumber);
        }

        return null;
    } catch (error) {
        // GraphQL might not be ready yet
        return null;
    }
}

/**
 * Convert entity name to Hasura table name format
 * Envio uses Hasura which converts PascalCase to snake_case
 * e.g., TransparentUpgradeableProxy_Transfer -> TransparentUpgradeableProxy_Transfer (preserves underscore separators)
 */
function toHasuraTableName(entityName: string): string {
    // For Envio, the table name is typically the entity name with first letter lowercased
    // and underscores preserved as-is
    return entityName.charAt(0).toLowerCase() + entityName.slice(1);
}

/**
 * Query GraphQL for total record count (data completeness)
 */
async function queryRecordCount(entityName: string = 'Transfer'): Promise<number> {
    // Try different possible table name formats
    const tableNameVariants = [
        toHasuraTableName(entityName),  // camelCase with underscores preserved
        entityName,                      // exact match
        entityName.toLowerCase(),        // all lowercase
    ];

    for (const tableName of tableNameVariants) {
        try {
            const aggregateQuery = `
                query {
                    ${tableName}_aggregate {
                        aggregate {
                            count
                        }
                    }
                }
            `;

            const response = await axios.post(
                GRAPHQL_URL,
                { query: aggregateQuery },
                {
                    headers: {
                        'Content-Type': 'application/json',
                        'x-hasura-admin-secret': GRAPHQL_PASSWORD
                    },
                    timeout: 10000
                }
            );

            // Check for GraphQL errors
            if (response.data?.errors) {
                // Table name doesn't exist, try next variant
                continue;
            }

            const count = response.data?.data?.[`${tableName}_aggregate`]?.aggregate?.count;
            if (count !== undefined && count !== null) {
                console.log(`  [DEBUG] Found records using table name: ${tableName}`);
                return Number(count);
            }
        } catch (error) {
            // Connection error or timeout, continue to next variant
            continue;
        }
    }

    // If all variants failed, try introspection to find the actual table name
    try {
        const introspectionQuery = `
            query {
                __schema {
                    queryType {
                        fields {
                            name
                        }
                    }
                }
            }
        `;

        const response = await axios.post(
            GRAPHQL_URL,
            { query: introspectionQuery },
            {
                headers: {
                    'Content-Type': 'application/json',
                    'x-hasura-admin-secret': GRAPHQL_PASSWORD
                },
                timeout: 10000
            }
        );

        const fields = response.data?.data?.__schema?.queryType?.fields || [];
        const aggregateFields = fields
            .map((f: { name: string }) => f.name)
            .filter((name: string) => name.endsWith('_aggregate'));

        console.log(`  [DEBUG] Available aggregate tables: ${aggregateFields.join(', ') || 'none found'}`);

        // Try to find a matching table
        for (const field of aggregateFields) {
            const baseTableName = field.replace('_aggregate', '');
            if (baseTableName.toLowerCase().includes(entityName.toLowerCase().replace(/_/g, ''))) {
                const countQuery = `
                    query {
                        ${field} {
                            aggregate {
                                count
                            }
                        }
                    }
                `;

                const countResponse = await axios.post(
                    GRAPHQL_URL,
                    { query: countQuery },
                    {
                        headers: {
                            'Content-Type': 'application/json',
                            'x-hasura-admin-secret': GRAPHQL_PASSWORD
                        },
                        timeout: 10000
                    }
                );

                const count = countResponse.data?.data?.[field]?.aggregate?.count;
                if (count !== undefined && count !== null) {
                    console.log(`  [DEBUG] Found records using discovered table: ${baseTableName}`);
                    return Number(count);
                }
            }
        }
    } catch (error) {
        console.error('  [DEBUG] Introspection failed:', error instanceof Error ? error.message : error);
    }

    console.log(`  [DEBUG] Could not find matching table for entity: ${entityName}`);
    return 0;
}

/**
 * Start the Envio dev server and monitor progress
 */
async function runEnvioBenchmark(
    caseDir: string,
    caseDisplayName: string,
    config: EnvioConfig,
    iteration: number
): Promise<BenchmarkResult> {
    const caseName = caseDisplayName;
    const network = config.networks[0];
    const startBlock = network.start_block;
    const endBlock = network.end_block;
    const totalBlocks = endBlock - startBlock;
    // Get entity name from case config for accurate record count query
    const caseConfig = CASE_CONFIG[caseDisplayName];
    const entityName = caseConfig?.entityName || network.contracts?.[0]?.name || 'Transfer';

    console.log(`\n${'='.repeat(60)}`);
    console.log(`[${caseName}] Starting benchmark iteration ${iteration}`);
    console.log(`[${caseName}] Block range: ${startBlock} -> ${endBlock} (${totalBlocks} blocks)`);
    console.log(`${'='.repeat(60)}\n`);

    // Clean up environment before starting (fresh state)
    await cleanupEnvironment(caseDir);

    const startTime = new Date();
    lastPerformanceMetrics = undefined;

    // Create a log file to capture output for parsing
    const logFile = path.join(caseDir, 'benchmark.log');

    // Start the process with tee to show output in real-time AND capture to log file
    const command = `${PM_CMD} dev 2>&1 | tee "${logFile}"`;

    const devProcess: ChildProcess = spawn('sh', ['-c', command], {
        cwd: caseDir,
        stdio: 'inherit',
        env: { ...process.env }
    });

    let error: string | undefined;
    let processExited = false;

    // Track completion from logs (primary method)
    let indexingComplete = false;
    let lastBlockFromLog = 0;

    // Handle process exit
    devProcess.on('exit', (code) => {
        processExited = true;
        if (code !== 0 && code !== null) {
            error = `Process exited with code ${code}`;
        }
    });

    // Wait for services to start
    console.log(`[${caseName}] Waiting ${STARTUP_WAIT_MS / 1000}s for services to start...`);
    await sleep(STARTUP_WAIT_MS);

    // Poll for completion by reading log file
    const timeoutTime = Date.now() + TIMEOUT_MS;
    let currentBlock = 0;
    let lastLogTime = Date.now();

    while (!processExited && !indexingComplete && Date.now() < timeoutTime) {
        // Read log file to check progress
        try {
            if (fs.existsSync(logFile)) {
                const logContent = fs.readFileSync(logFile, 'utf-8');

                // Check for completion message
                if (logContent.includes('All chains are caught up') ||
                    logContent.includes('caught up to end blocks')) {
                    console.log(`\n[${caseName}] ✓ Detected completion from logs!`);
                    indexingComplete = true;
                }

                // Parse block progress - look for "blocks:  22,200,000" pattern
                const blockMatches = logContent.match(/blocks:\s*([\d,]+)/gi);
                if (blockMatches && blockMatches.length > 0) {
                    const lastMatch = blockMatches[blockMatches.length - 1];
                    const blockNum = parseInt(lastMatch.replace(/blocks:\s*/i, '').replace(/,/g, ''), 10);
                    if (!isNaN(blockNum) && blockNum > lastBlockFromLog) {
                        lastBlockFromLog = blockNum;
                        currentBlock = blockNum;
                    }
                }

                // Parse performance metrics from log
                const perfMatches = logContent.match(/\[PERFORMANCE METRICS\] Ops: (\d+) \| RPC: ([\d.]+)s \| Storage: ([\d.]+)s \| Calc: ([\d.]+)s/g);
                if (perfMatches && perfMatches.length > 0) {
                    const lastPerfMatch = perfMatches[perfMatches.length - 1].match(/\[PERFORMANCE METRICS\] Ops: (\d+) \| RPC: ([\d.]+)s \| Storage: ([\d.]+)s \| Calc: ([\d.]+)s/);
                    if (lastPerfMatch) {
                        lastPerformanceMetrics = {
                            operationCount: parseInt(lastPerfMatch[1], 10),
                            rpcTime: parseFloat(lastPerfMatch[2]),
                            storageTime: parseFloat(lastPerfMatch[3]),
                            calcTime: parseFloat(lastPerfMatch[4])
                        };
                    }
                }
            }
        } catch (e) {
            // Ignore file read errors
        }

        // Also try GraphQL as fallback
        if (currentBlock === 0) {
            const block = await queryCurrentBlock('Transfer');
            if (block !== null && block > currentBlock) {
                currentBlock = block;
            }
        }

        // Log progress every 30 seconds
        if (Date.now() - lastLogTime > 30000) {
            const elapsed = (Date.now() - startTime.getTime()) / 1000;
            const progress = currentBlock > 0 ? ((currentBlock - startBlock) / totalBlocks * 100).toFixed(2) : '0.00';
            console.log(`\n[${caseName}] Progress: ${progress}% | Block: ${currentBlock}/${endBlock} | Elapsed: ${elapsed.toFixed(0)}s`);
            lastLogTime = Date.now();
        }

        // Check if we've reached the end block
        if (currentBlock >= endBlock) {
            console.log(`\n[${caseName}] ✓ Indexing complete! Reached block ${currentBlock}`);
            indexingComplete = true;
            break;
        }

        await sleep(POLL_INTERVAL_MS);
    }

    // If completion was detected from logs, use lastBlockFromLog
    if (indexingComplete && lastBlockFromLog > currentBlock) {
        currentBlock = lastBlockFromLog;
    }

    const endTime = new Date();
    const durationSeconds = (endTime.getTime() - startTime.getTime()) / 1000;

    // Check for timeout
    if (currentBlock < endBlock && !error) {
        error = `Timeout: only reached block ${currentBlock}/${endBlock}`;
    }

    // Query record count for data completeness BEFORE stopping services
    console.log(`\n[${caseName}] Querying record count for data completeness...`);
    const recordCount = await queryRecordCount(entityName);
    console.log(`[${caseName}] Total records indexed: ${recordCount}`);

    // Clean up
    console.log(`[${caseName}] Stopping indexer...`);
    devProcess.kill('SIGTERM');

    // Wait a bit for graceful shutdown
    await sleep(2000);

    // Force kill if still running
    if (!processExited) {
        try {
            devProcess.kill('SIGKILL');
        } catch (e) {
            // Ignore if already dead
        }
    }

    // Ensure port is also freed for next run
    freePort(9898);

    // Clean up Docker containers
    await cleanupDocker(caseDir);

    const result: BenchmarkResult = {
        caseName,
        iteration,
        startTime: startTime.toISOString(),
        endTime: endTime.toISOString(),
        durationSeconds,
        startBlock,
        endBlock,
        totalBlocks,
        blocksPerSecond: totalBlocks / durationSeconds,
        recordCount,
        performanceMetrics: lastPerformanceMetrics,
        error
    };

    console.log(`\n[${caseName}] Benchmark Result:`);
    console.log(`  Duration: ${durationSeconds.toFixed(2)}s (${(durationSeconds / 60).toFixed(2)} min)`);
    console.log(`  Blocks/sec: ${result.blocksPerSecond.toFixed(2)}`);
    if (lastPerformanceMetrics) {
        console.log(`  RPC Time: ${lastPerformanceMetrics.rpcTime.toFixed(2)}s`);
        console.log(`  Storage Time: ${lastPerformanceMetrics.storageTime.toFixed(2)}s`);
        console.log(`  Operations: ${lastPerformanceMetrics.operationCount}`);
    }
    if (error) {
        console.log(`  Error: ${error}`);
    }

    return result;
}

/**
 * Clean up Docker containers started by Envio
 */
async function cleanupDocker(caseDir: string): Promise<void> {
    const caseName = path.basename(caseDir);
    console.log(`[${caseName}] Cleaning up Docker containers...`);

    try {
        // Envio typically uses docker-compose, try to stop containers
        execSync('docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true', {
            cwd: caseDir,
            stdio: 'pipe'
        });
    } catch {
        // Ignore errors - containers might not exist
    }

    // Also try to stop any envio-related containers by name pattern
    try {
        execSync('docker ps -q --filter "name=envio" | xargs -r docker stop 2>/dev/null || true', {
            stdio: 'pipe'
        });
    } catch {
        // Ignore errors
    }
}

/**
 * Sleep helper
 */
function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Save benchmark results to files
 */
function saveResults(results: BenchmarkResult[]): void {
    // Save JSON report
    const jsonPath = path.join(__dirname, 'envio_benchmark_report.json');
    fs.writeFileSync(jsonPath, JSON.stringify(results, null, 2));
    console.log(`\nSaved JSON report to: ${jsonPath}`);

    // Save Markdown report
    const mdPath = path.join(__dirname, 'envio_benchmark_report.md');
    let md = '# Envio Benchmark Report\n\n';
    md += `Generated: ${new Date().toISOString()}\n\n`;
    md += '| Case | Iteration | Duration (s) | Duration (min) | Blocks | Blocks/s | Records | Error |\n';
    md += '|------|-----------|--------------|----------------|--------|----------|---------|-------|\n';

    for (const r of results) {
        const error = r.error ? r.error.substring(0, 20) : '-';
        md += `| ${r.caseName} | ${r.iteration} | ${r.durationSeconds.toFixed(2)} | ${(r.durationSeconds / 60).toFixed(2)} | ${r.totalBlocks} | ${r.blocksPerSecond.toFixed(2)} | ${r.recordCount} | ${error} |\n`;
    }

    // Add summary statistics
    md += '\n## Summary Statistics\n\n';

    const caseNames = [...new Set(results.map(r => r.caseName))];
    for (const caseName of caseNames) {
        const caseResults = results.filter(r => r.caseName === caseName && !r.error);
        if (caseResults.length === 0) continue;

        const durations = caseResults.map(r => r.durationSeconds);
        const avgDuration = durations.reduce((a, b) => a + b, 0) / durations.length;
        const minDuration = Math.min(...durations);
        const maxDuration = Math.max(...durations);

        md += `### ${caseName}\n`;
        md += `- Iterations: ${caseResults.length}\n`;
        md += `- Average: ${avgDuration.toFixed(2)}s (${(avgDuration / 60).toFixed(2)} min)\n`;
        md += `- Min: ${minDuration.toFixed(2)}s\n`;
        md += `- Max: ${maxDuration.toFixed(2)}s\n\n`;
    }

    fs.writeFileSync(mdPath, md);
    console.log(`Saved Markdown report to: ${mdPath}`);
}

/**
 * Main benchmark runner
 */
async function main(): Promise<void> {
    const args = process.argv.slice(2);
    let casesToRun = ENVIO_CASES;
    let iterations = 1;
    let forceClean = false;

    // Parse arguments
    for (let i = 0; i < args.length; i++) {
        if (args[i] === '--case' && args[i + 1]) {
            casesToRun = [args[i + 1]];
            i++;
        } else if (args[i] === '--iterations' && args[i + 1]) {
            iterations = parseInt(args[i + 1], 10);
            i++;
        } else if (args[i] === '--clean') {
            forceClean = true;
        }
    }

    console.log('╔════════════════════════════════════════════════════════════╗');
    console.log('║            Envio Benchmark Automation Script               ║');
    console.log('╠════════════════════════════════════════════════════════════╣');
    console.log(`║ Cases to run: ${casesToRun.join(', ').padEnd(44)}║`);
    console.log(`║ Iterations: ${String(iterations).padEnd(47)}║`);
    console.log('╚════════════════════════════════════════════════════════════╝\n');

    const results: BenchmarkResult[] = [];

    for (const caseName of casesToRun) {
        const caseDir = path.join(__dirname, caseName, 'envio');

        // Check if envio directory exists
        if (!fs.existsSync(caseDir)) {
            console.log(`[${caseName}] Envio directory not found, skipping...`);
            continue;
        }

        try {
            // Parse config
            const config = parseEnvioConfig(caseDir);

            // Install dependencies
            await installDependencies(caseDir);

            // Run codegen (forceClean if --clean flag was passed)
            await runCodegen(caseDir, forceClean);

            // Run iterations
            for (let i = 1; i <= iterations; i++) {
                const result = await runEnvioBenchmark(caseDir, caseName, config, i);
                results.push(result);

                // Save intermediate results
                saveResults(results);

                // Wait between iterations
                if (i < iterations) {
                    console.log(`\n[${caseName}] Waiting 10s before next iteration...\n`);
                    await sleep(10000);
                }
            }
        } catch (err) {
            console.error(`[${caseName}] Fatal error:`, err);
            results.push({
                caseName,
                iteration: 0,
                startTime: new Date().toISOString(),
                endTime: new Date().toISOString(),
                durationSeconds: 0,
                startBlock: 0,
                endBlock: 0,
                totalBlocks: 0,
                blocksPerSecond: 0,
                recordCount: 0,
                error: err instanceof Error ? err.message : String(err)
            });
        }
    }

    // Final report
    console.log('\n' + '═'.repeat(60));
    console.log('BENCHMARK COMPLETE');
    console.log('═'.repeat(60));
    saveResults(results);

    // Print summary table
    console.log('\nResults Summary:');
    console.table(results.map(r => ({
        Case: r.caseName,
        Iter: r.iteration,
        'Duration (s)': r.durationSeconds.toFixed(2),
        'Duration (min)': (r.durationSeconds / 60).toFixed(2),
        'Blocks/s': r.blocksPerSecond.toFixed(2),
        Error: r.error ? 'Yes' : 'No'
    })));
}

// Run the benchmark
main().catch(console.error);
