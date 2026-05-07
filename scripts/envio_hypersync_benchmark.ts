/**
 * Envio HyperSync Benchmark Automation Script
 * 
 * This script automates running Envio HyperSync benchmarks (Cases 3, 4, 5) by:
 * 1. Installing dependencies
 * 2. Running the fetch-data.js script
 * 3. Capturing output and parsing metrics
 * 4. Generating benchmark reports
 */

import * as fs from 'fs';
import * as path from 'path';
import { spawn, ChildProcess, execSync } from 'child_process';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Constants
const STARTUP_WAIT_MS = 1000;
const TIMEOUT_MS = 60 * 60 * 1000; // 60 minutes

// Package manager command - use bun for best performance
const PM_CMD = process.env.ENVIO_PM_CMD || 'bun';

// HyperSync case directories
const HYPERSYNC_CASES = [
    'case_3_ethereum_block',
    'case_4_on_transaction',
    'case_5_on_trace'
];

// Case-specific configuration for record counting
const CASE_CONFIG: Record<string, { recordsPattern: RegExp; fallbackToBlocks?: boolean }> = {
    'case_3_ethereum_block': {
        // Output: "Fetched X blocks out of Y expected blocks"
        recordsPattern: /Fetched\s+([\d,]+)\s+blocks/i,
        fallbackToBlocks: true // blocks = records for this case
    },
    'case_4_on_transaction': {
        // Output: "Gas records collected: X"
        recordsPattern: /Gas records collected[:\s]+([\d,]+)/i
    },
    'case_5_on_trace': {
        // Output: "Collected X swap records"
        recordsPattern: /Collected\s+([\d,]+)\s+swap records/i
    }
};

interface BenchmarkResult {
    caseName: string;
    iteration: number;
    startTime: string;
    endTime: string;
    durationSeconds: number;
    blocksPerSecond: number;
    totalBlocks: number;
    totalRecords: number;
    error?: string;
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
    execSync(`${PM_CMD} install`, { cwd: caseDir, stdio: 'inherit', shell: '/bin/bash' });
}

/**
 * Run the HyperSync benchmark script
 */
async function runHypersyncBenchmark(
    caseDir: string,
    iteration: number
): Promise<BenchmarkResult> {
    const caseName = path.basename(caseDir);
    const scriptPath = path.join(caseDir, 'envio/fetch-data.js');

    console.log(`\n${'='.repeat(60)}`);
    console.log(`[${caseName}] Starting benchmark iteration ${iteration}`);
    console.log(`${'='.repeat(60)}\n`);

    const startTime = new Date();

    // Command to run the script
    // Note: We use 'node' to run the script as it's the standard runtime, 
    // but relies on dependencies installed via PM_CMD
    const command = `node "${scriptPath}"`;

    // Ensure HYPERSYNC_URL is set
    const env = { ...process.env };
    if (!env.HYPERSYNC_URL) {
        console.log(`[${caseName}] HYPERSYNC_URL not found in env, using default: https://eth.hypersync.xyz`);
        env.HYPERSYNC_URL = "https://eth.hypersync.xyz";
    }

    return new Promise((resolve) => {
        let output = '';
        let errorOutput = '';

        const childProcess = spawn('sh', ['-c', command], {
            cwd: path.join(caseDir, 'envio'),
            env: env
        });

        childProcess.stdout.on('data', (data) => {
            const chunk = data.toString();
            output += chunk;
            process.stdout.write(chunk); // Stream to console
        });

        childProcess.stderr.on('data', (data) => {
            const chunk = data.toString();
            errorOutput += chunk;
            process.stderr.write(chunk); // Stream to console
        });

        childProcess.on('close', (code) => {
            const endTime = new Date();
            const durationSeconds = (endTime.getTime() - startTime.getTime()) / 1000;

            let totalBlocks = 0;
            let totalRecords = 0;
            let error: string | undefined;

            if (code !== 0) {
                error = `Process exited with code ${code}`;
            } else {
                // Parse output for metrics
                // Case 3: "Fetched 100000 blocks out of 100000 expected blocks"
                // Case 4: "Total gas value: ..." and "Total blocks processed: 100"
                // Case 5: "Processed 99000 blocks", "Collected 45895 swap records"

                // Try to match "Processed X blocks" or "Fetched X blocks"
                const blocksMatch = output.match(/(?:Processed|Fetched)\s+([\d,]+)\s+blocks/i);
                if (blocksMatch) {
                    totalBlocks = parseInt(blocksMatch[1].replace(/,/g, ''), 10);
                }

                // If not found, try output summary format from Case 4/5
                if (totalBlocks === 0) {
                    const summaryBlocksMatch = output.match(/Total blocks processed:\s*([\d,]+)/i);
                    if (summaryBlocksMatch) {
                        totalBlocks = parseInt(summaryBlocksMatch[1].replace(/,/g, ''), 10);
                    }
                }

                // Try to find record counts using case-specific patterns
                const caseConfig = CASE_CONFIG[caseName];
                if (caseConfig) {
                    const recordsMatch = output.match(caseConfig.recordsPattern);
                    if (recordsMatch) {
                        totalRecords = parseInt(recordsMatch[1].replace(/,/g, ''), 10);
                    }
                }

                // Fallback patterns for record detection
                if (totalRecords === 0) {
                    // Case 4: "Gas records collected: 1696641"
                    const gasMatch = output.match(/Gas records collected\s*:?\s*([\d,]+)/i);
                    if (gasMatch) {
                        totalRecords = parseInt(gasMatch[1].replace(/,/g, ''), 10);
                    }
                }

                if (totalRecords === 0) {
                    // Case 5: "Collected 50191 swap records"
                    const swapMatch = output.match(/Collected\s+([\d,]+)\s+swap records/i);
                    if (swapMatch) {
                        totalRecords = parseInt(swapMatch[1].replace(/,/g, ''), 10);
                    }
                }

                // If records is still 0 and case uses blocks as records
                if (totalRecords === 0 && caseConfig?.fallbackToBlocks) {
                    totalRecords = totalBlocks;
                }
            }

            resolve({
                caseName,
                iteration,
                startTime: startTime.toISOString(),
                endTime: endTime.toISOString(),
                durationSeconds,
                blocksPerSecond: durationSeconds > 0 ? totalBlocks / durationSeconds : 0,
                totalBlocks,
                totalRecords,
                error
            });
        });
    });
}

/**
 * Save benchmark results to files
 */
function saveResults(results: BenchmarkResult[]): void {
    // Save JSON report
    const jsonPath = path.join(__dirname, 'envio_hypersync_benchmark_report.json');
    fs.writeFileSync(jsonPath, JSON.stringify(results, null, 2));
    console.log(`\nSaved JSON report to: ${jsonPath}`);

    // Save Markdown report
    const mdPath = path.join(__dirname, 'envio_hypersync_benchmark_report.md');
    let md = '# Envio HyperSync Benchmark Report\n\n';
    md += `Generated: ${new Date().toISOString()}\n\n`;
    md += '| Case | Iteration | Duration (s) | Blocks | Records | Blocks/s | Records/s | Error |\n';
    md += '|------|-----------|--------------|--------|---------|----------|-----------|-------|\n';

    for (const r of results) {
        const error = r.error ? r.error.substring(0, 20) : '-';
        const recordsPerSec = r.durationSeconds > 0 ? (r.totalRecords / r.durationSeconds).toFixed(2) : '0';

        md += `| ${r.caseName} | ${r.iteration} | ${r.durationSeconds.toFixed(2)} | ${r.totalBlocks} | ${r.totalRecords} | ${r.blocksPerSecond.toFixed(2)} | ${recordsPerSec} | ${error} |\n`;
    }

    fs.writeFileSync(mdPath, md);
    console.log(`Saved Markdown report to: ${mdPath}`);
}

/**
 * Main benchmark runner
 */
async function main(): Promise<void> {
    const args = process.argv.slice(2);
    let casesToRun = HYPERSYNC_CASES;
    let iterations = 1;

    // Parse arguments
    for (let i = 0; i < args.length; i++) {
        if (args[i] === '--case' && args[i + 1]) {
            casesToRun = [args[i + 1]];
            i++;
        } else if (args[i] === '--iterations' && args[i + 1]) {
            iterations = parseInt(args[i + 1], 10);
            i++;
        }
    }

    console.log('╔════════════════════════════════════════════════════════════╗');
    console.log('║        Envio HyperSync Benchmark Automation Script         ║');
    console.log('╠════════════════════════════════════════════════════════════╣');
    console.log(`║ Cases to run: ${casesToRun.join(', ').padEnd(44)}║`);
    console.log(`║ Iterations: ${String(iterations).padEnd(47)}║`);
    console.log('╚════════════════════════════════════════════════════════════╝\n');

    const results: BenchmarkResult[] = [];

    for (const caseName of casesToRun) {
        const caseDir = path.join(__dirname, caseName);
        const envioDir = path.join(caseDir, 'envio');

        // Check if envio directory exists
        if (!fs.existsSync(envioDir)) {
            console.log(`[${caseName}] Envio directory not found, skipping...`);
            continue;
        }

        try {
            // Install dependencies
            await installDependencies(envioDir);

            // Run iterations
            for (let i = 1; i <= iterations; i++) {
                const result = await runHypersyncBenchmark(caseDir, i);
                results.push(result);

                // Save intermediate results
                saveResults(results);

                // Wait between iterations
                if (i < iterations) {
                    console.log(`\n[${caseName}] Waiting 5s before next iteration...\n`);
                    await new Promise(resolve => setTimeout(resolve, 5000));
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
                blocksPerSecond: 0,
                totalBlocks: 0,
                totalRecords: 0,
                error: err instanceof Error ? err.message : String(err)
            });
        }
    }

    // Final report
    console.log('\n' + '═'.repeat(60));
    console.log('BENCHMARK COMPLETE');
    console.log('═'.repeat(60));
    saveResults(results);
}

// Run the benchmark
main().catch(console.error);
