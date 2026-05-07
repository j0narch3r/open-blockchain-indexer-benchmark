import * as fs from 'fs';
import * as path from 'path';
import { exec } from 'child_process';
import axios from 'axios';
import * as dotenv from 'dotenv';
import * as util from 'util';

import { fileURLToPath } from 'url';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const execPromise = util.promisify(exec);

const API_KEY = process.env.SENTIO_API_KEY;
const BASE_URL = 'https://api.sentio.xyz/v1';

if (!API_KEY) {
    console.error('Error: SENTIO_API_KEY is not set in .env file or environment variables.');
    process.exit(1);
}

const CASES = [
    {
        name: 'case_1_lbtc_event_only',
        sql: 'SELECT max(indexedAt) - min(indexedAt) as duration FROM Transfer',
        tableName: 'Transfer'
    },
    {
        name: 'case_2_lbtc_full',
        sql: 'SELECT max(ts) - min(ts) as duration FROM performance_metrics',
        tableName: 'Transfer'
    },
    {
        name: 'case_3_ethereum_block',
        sql: 'SELECT max(indexAt) - min(indexAt) as duration FROM Block',
        tableName: 'Block'
    },
    // {
    //     name: 'case_4_on_transaction',
    //     sql: 'SELECT max(indexedAt) - min(indexedAt) as duration FROM GasSpent',
    //     tableName: 'GasSpent'
    // },
    {
        name: 'case_5_on_trace',
        sql: 'SELECT max(indexedAt) - min(indexedAt) as duration FROM Swap',
        tableName: 'Swap'
    },
    {
        name: 'case_6_template',
        sql: 'SELECT max(indexedAt) - min(indexedAt) as duration FROM UniswapV2Event',
        tableName: 'UniswapV2Event'
    }
];

const ITERATIONS = 5;

interface ProcessorStatus {
    processorId: string;
    version: number;
    processorStatus: {
        state: string;
    };
    states: {
        processedBlockNumber: string;
        estimatedLatestBlockNumber: string;
        status: {
            state: string;
        }
    }[];
}

async function getProjectSlug(caseDir: string): Promise<string> {
    const yamlPath = path.join(caseDir, 'sentio', 'sentio.yaml');
    const content = fs.readFileSync(yamlPath, 'utf-8');
    const match = content.match(/project: ([^\s]+)/);
    if (!match) {
        throw new Error(`Could not find project slug in ${yamlPath}`);
    }
    return match[1]; // e.g., sentio/case_1_lbtc_event_only
}

async function installDependencies(caseDir: string): Promise<void> {
    const sentioDir = path.join(caseDir, 'sentio');
    const nodeModulesPath = path.join(sentioDir, 'node_modules');

    if (fs.existsSync(nodeModulesPath)) {
        return;
    }

    console.log(`Installing dependencies for ${caseDir}...`);
    try {
        await execPromise('yarn install', { cwd: sentioDir });
        console.log('Dependencies installed.');
    } catch (error) {
        console.error(`Failed to install dependencies in ${caseDir}:`, error);
        throw error;
    }
}

async function uploadProcessor(caseDir: string): Promise<{ version: number; processorId: string }> {
    console.log(`Uploading processor for ${caseDir}...`);
    const sentioDir = path.join(caseDir, 'sentio');
    try {
        const { stdout } = await execPromise('yarn sentio upload', { cwd: sentioDir });
        // Parse output to find version and processorId if possible, or just fetch latest status
        // The output usually contains a link, but we can just fetch the latest version from API after upload
        console.log('Upload command finished.');
        return { version: 0, processorId: '' }; // We will fetch the latest version from API
    } catch (error) {
        console.error(`Failed to upload processor in ${caseDir}:`, error);
        throw error;
    }
}

// Helper for sleep
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Retry wrapper for axios
async function fetchWithRetry<T>(fn: () => Promise<T>, retries = 5, delay = 2000): Promise<T> {
    try {
        return await fn();
    } catch (error: any) {
        if (retries === 0) throw error;

        const isRateLimit = error.response?.status === 429;
        const isNetworkError = error.code === 'ECONNRESET' || error.code === 'ETIMEDOUT';

        if (isRateLimit || isNetworkError) {
            const backoff = isRateLimit ? delay * 2 : delay;
            console.log(`Request failed (${isRateLimit ? '429' : error.code}), retrying in ${backoff}ms...`);
            await sleep(backoff);
            return fetchWithRetry(fn, retries - 1, backoff);
        }

        throw error;
    }
}


async function getLatestProcessorVersion(owner: string, slug: string): Promise<{ version: number; processorId: string }> {
    const url = `${BASE_URL}/processors/${owner}/${slug}/status?version=ALL`;
    try {
        const response = await fetchWithRetry(() => axios.get(url, {
            headers: { 'api-key': API_KEY }
        }));
        const processors = response.data.processors;
        if (!processors || processors.length === 0) {
            throw new Error('No processors found');
        }
        // Sort by version desc
        processors.sort((a: any, b: any) => b.version - a.version);
        return {
            version: processors[0].version,
            processorId: processors[0].processorId
        };
    } catch (error) {
        console.error(`Failed to get processor status for ${owner}/${slug}:`, error);
        throw error;
    }
}

const TIMEOUT_MS = 45 * 60 * 1000; // Increased to 45 minutes

async function saveReport(report: any[]) {
    fs.writeFileSync('benchmark_report.json', JSON.stringify(report, null, 2));

    let mdReport = '# Benchmark Report\n\n| Case | Iteration | Duration (s) | Duration (min) | Blocks | Blocks/s | Records | Progress | SQL Duration (s) | Error |\n|------|-----------|--------------|----------------|--------|----------|---------|----------|------------------|-------|\n';
    for (const row of report) {
        const durationSec = row.duration?.toFixed(2) || 'N/A';
        const durationMin = row.duration ? (row.duration / 60).toFixed(2) : 'N/A';
        const blocks = row.blocks || 'N/A';
        const blocksPerSec = (row.duration && row.blocks) ? (row.blocks / row.duration).toFixed(2) : 'N/A';
        const records = row.records ?? 'N/A';
        const progress = row.progress || '100%';
        const error = row.error || '';
        const sqlDuration = row.sqlDuration !== undefined ? row.sqlDuration.toFixed(2) : 'N/A';
        mdReport += `| ${row.case} | ${row.iteration} | ${durationSec} | ${durationMin} | ${blocks} | ${blocksPerSec} | ${records} | ${progress} | ${sqlDuration} | ${error} |\n`;
    }
    fs.writeFileSync('benchmark_report.md', mdReport);
}

async function waitForProcessor(owner: string, slug: string, version: number): Promise<void> {
    console.log(`Waiting for processor ${owner}/${slug} v${version} to catch up...`);
    const url = `${BASE_URL}/processors/${owner}/${slug}/status?version=ALL`;
    const startTime = Date.now();

    while (true) {
        if (Date.now() - startTime > TIMEOUT_MS) {
            throw new Error(`Timeout waiting for processor ${owner}/${slug} v${version}`);
        }

        try {
            const response = await fetchWithRetry(() => axios.get(url, {
                headers: { 'api-key': API_KEY }
            }), 3, 5000); // Retry 3 times with 5s initial delay for status check

            const processor = response.data.processors.find((p: any) => p.version === version);

            if (!processor) {
                console.log(`Version ${version} not found yet...`);
                await sleep(10000); // Wait 10s
                continue;
            }

            const state = processor.states[0]; // Assuming single chain for now
            if (!state) {
                console.log(`Waiting for state initialization...`);
                await sleep(10000);
                continue;
            }

            const processed = BigInt(state.processedBlockNumber || 0);
            const estimated = BigInt(state.estimatedLatestBlockNumber || 0);
            const statusState = state.status?.state;

            console.log(`[${owner}/${slug}] Status: ${statusState}, Processed: ${processed}, Estimated: ${estimated}`);

            if (statusState === 'PROCESSING' || statusState === 'PROCESSING_LATEST') {
                // Check if caught up (e.g. within 100 blocks)
                if (estimated > 0 && (estimated - processed) < 100n) {
                    console.log(`[${owner}/${slug}] Processor caught up!`);
                    break;
                }
            }

            if (statusState === 'FATAL' || statusState === 'ERROR') {
                throw new Error(`Processor failed with state: ${statusState}`);
            }

            // Increase polling interval to 20s to reduce load
            await sleep(20000);
        } catch (error) {
            console.error('Error polling status:', error);
            await sleep(20000);
        }
    }
}

async function getMetrics(owner: string, slug: string, version: number, sql: string): Promise<any> {
    console.log(`Querying metrics for ${owner}/${slug} v${version}...`);
    const url = `${BASE_URL}/analytics/${owner}/${slug}/sql/execute`;
    const query = {
        sqlQuery: {
            sql: sql
        },
        engine: "SMALL", // or whatever is appropriate
        version: version
    };

    try {
        const response = await fetchWithRetry(() => axios.post(url, query, {
            headers: {
                'api-key': API_KEY,
                'Content-Type': 'application/json'
            }
        }), 10, 2000);
        return response.data;
    } catch (error) {
        console.error('Failed to query metrics:', error);
        throw error;
    }
}

/**
 * Query record count using SQL COUNT(*)
 */
async function queryRecordCount(owner: string, slug: string, tableName: string): Promise<number> {
    console.log(`Querying record count for ${owner}/${slug} table ${tableName}...`);
    const url = `${BASE_URL}/analytics/${owner}/${slug}/sql/execute`;
    const query = {
        sqlQuery: {
            sql: `SELECT COUNT(*) as count FROM ${tableName}`
        }
    };

    try {
        const response = await fetchWithRetry(() => axios.post(url, query, {
            headers: {
                'api-key': API_KEY,
                'Content-Type': 'application/json'
            }
        }), 5, 2000);

        const rows = response.data?.result?.rows || [];
        if (rows.length > 0) {
            const count = parseInt(rows[0].count || rows[0].COUNT || '0');
            return count;
        }
        return 0;
    } catch (error) {
        console.error('Failed to query record count:', error);
        return 0;
    }
}

/**
 * Query-only mode: just query record counts for each case
 */
async function queryRecordsOnly(caseFilter?: string) {
    console.log('\n🔍 Query-only mode: Querying record counts...\n');

    const casesToQuery = caseFilter
        ? CASES.filter(c => c.name === caseFilter || c.name === `case_${caseFilter}` || c.name.includes(caseFilter))
        : CASES;

    if (casesToQuery.length === 0) {
        console.error(`No cases found matching: ${caseFilter}`);
        console.log('Available cases:', CASES.map(c => c.name).join(', '));
        return;
    }

    for (const caseConfig of casesToQuery) {
        const caseName = caseConfig.name;
        const caseDir = path.resolve(__dirname, caseName);

        if (!fs.existsSync(caseDir)) {
            console.warn(`[${caseName}] Directory not found, skipping.`);
            continue;
        }

        try {
            const projectSlug = await getProjectSlug(caseDir);
            const [owner, slug] = projectSlug.split('/');

            console.log(`[${caseName}] Project: ${owner}/${slug}`);
            console.log(`[${caseName}] Table: ${caseConfig.tableName}`);

            const count = await queryRecordCount(owner, slug, caseConfig.tableName);
            console.log(`[${caseName}] ✓ Record count: ${count}\n`);
        } catch (error) {
            console.error(`[${caseName}] Error:`, error instanceof Error ? error.message : error);
        }
    }

    console.log('Query completed.');
}

async function runBenchmark() {
    const report: any[] = [];

    const promises = CASES.map(async (caseConfig) => {
        const caseName = caseConfig.name;
        const caseDir = path.resolve(__dirname, caseName);

        try {
            console.log(`[${caseName}] Checking directory: ${caseDir}`);
            if (!fs.existsSync(caseDir)) {
                console.warn(`[${caseName}] Directory does not exist, skipping.`);
                return;
            }

            const projectSlug = await getProjectSlug(caseDir);
            const [owner, slug] = projectSlug.split('/');

            console.log(`[${caseName}] Starting benchmark for ${projectSlug}`);

            for (let i = 0; i < ITERATIONS; i++) {
                console.log(`[${caseName}] Iteration ${i + 1}/${ITERATIONS}`);

                try {
                    // 0. Install dependencies if needed
                    await installDependencies(caseDir);

                    // 1. Upload
                    await uploadProcessor(caseDir);

                    // 2. Get new version
                    // Wait a bit for the new version to appear in API
                    await new Promise(resolve => setTimeout(resolve, 5000));
                    const { version, processorId } = await getLatestProcessorVersion(owner, slug);
                    console.log(`[${caseName}] Detected new version: ${version}`);

                    // 3. Wait for completion
                    const startTime = Date.now();
                    await waitForProcessor(owner, slug, version);
                    const endTime = Date.now();
                    const duration = (endTime - startTime) / 1000;

                    // 4. Get metrics and record count
                    const metrics = await getMetrics(owner, slug, version, caseConfig.sql);
                    const records = await queryRecordCount(owner, slug, caseConfig.tableName);

                    // Parse SQL duration from metrics result
                    let sqlDuration: number | undefined;
                    try {
                        console.log(`[${caseName}] SQL metrics response:`, JSON.stringify(metrics, null, 2));
                        const rows = metrics?.result?.rows || [];
                        if (rows.length > 0) {
                            console.log(`[${caseName}] First row:`, JSON.stringify(rows[0]));
                            // The SQL returns 'duration' column (max(indexedAt) - min(indexedAt))
                            // Try different possible key names
                            const row = rows[0];
                            const value = row.duration ?? row.DURATION ?? row.Duration ?? Object.values(row)[0];
                            sqlDuration = value !== undefined ? parseFloat(String(value)) : undefined;
                            console.log(`[${caseName}] SQL duration result: ${sqlDuration}s`);
                        } else {
                            console.log(`[${caseName}] No rows returned from SQL query`);
                        }
                    } catch (e) {
                        console.error(`[${caseName}] Failed to parse SQL duration:`, e);
                    }

                    // Get block range from sentio.yaml to calculate blocks processed
                    const yamlPath = path.join(caseDir, 'sentio', 'sentio.yaml');
                    const yamlContent = fs.readFileSync(yamlPath, 'utf-8');
                    const startBlockMatch = yamlContent.match(/startBlock:\s*(\d+)/);
                    const endBlockMatch = yamlContent.match(/endBlock:\s*(\d+)/);
                    const startBlock = startBlockMatch ? parseInt(startBlockMatch[1]) : 0;
                    const endBlock = endBlockMatch ? parseInt(endBlockMatch[1]) : 0;
                    const blocks = endBlock > startBlock ? endBlock - startBlock : 0;

                    const result = {
                        case: caseName,
                        iteration: i + 1,
                        version,
                        duration,
                        blocks,
                        records,
                        progress: '100%',
                        sqlDuration
                    };
                    report.push(result);
                    saveReport(report);

                } catch (error) {
                    console.error(`[${caseName}] Iteration ${i + 1} failed:`, error);
                    const errorResult = {
                        case: caseName,
                        iteration: i + 1,
                        error: error instanceof Error ? error.message : String(error)
                    };
                    report.push(errorResult);
                    saveReport(report);
                }
            }
        } catch (outerError) {
            console.error(`[${caseName}] Case failed:`, outerError);
            report.push({
                case: caseName,
                iteration: 0,
                error: outerError instanceof Error ? outerError.message : String(outerError)
            });
            saveReport(report);
        }
    });

    await Promise.all(promises);

    console.log('Benchmark completed.');
    console.table(report);
}

// Parse command line arguments
const args = process.argv.slice(2);

if (args.includes('--help') || args.includes('-h')) {
    console.log('\nUsage: bun benchmark.ts [case] [options]');
    console.log('\nArguments:');
    console.log('  case            Run specific case (e.g., case1, case_1_lbtc_event_only)');
    console.log('                  Use "all" or omit to run all cases');
    console.log('\nOptions:');
    console.log('  --query-only    Only query record counts, skip benchmarking');
    console.log('  --help, -h      Show this help message');
    console.log('\nAvailable cases:');
    CASES.forEach(c => console.log(`  ${c.name}`));
    console.log('\nExamples:');
    console.log('  bun benchmark.ts                     # Run all cases');
    console.log('  bun benchmark.ts case1               # Run only case 1');
    console.log('  bun benchmark.ts case_1_lbtc_event_only');
    console.log('  bun benchmark.ts --query-only        # Query all cases');
    console.log('  bun benchmark.ts case1 --query-only  # Query specific case');
    process.exit(0);
}

// Filter out options to get case argument
const isQueryOnly = args.includes('--query-only');
const caseArg = args.find(a => !a.startsWith('--'));

// Helper to filter cases
function filterCases(filter?: string) {
    if (!filter || filter === 'all') {
        return CASES;
    }
    const filtered = CASES.filter(c =>
        c.name === filter ||
        c.name === `case_${filter}` ||
        c.name.includes(filter) ||
        c.name.replace('case_', '').startsWith(filter.replace('case', ''))
    );
    if (filtered.length === 0) {
        console.error(`No cases found matching: ${filter}`);
        console.log('Available cases:', CASES.map(c => c.name).join(', '));
        process.exit(1);
    }
    return filtered;
}

if (isQueryOnly) {
    queryRecordsOnly(caseArg).catch(console.error);
} else {
    // Make a copy to avoid reference issues when filterCases returns CASES directly
    const casesToRun = [...filterCases(caseArg)];
    console.log(`\nRunning benchmarks for: ${casesToRun.map(c => c.name).join(', ')}\n`);

    // Override CASES temporarily for the benchmark
    CASES.length = 0;
    CASES.push(...casesToRun);

    runBenchmark().catch(console.error);
}
