/**
 * Goldsky Turbo benchmark runner.
 *
 * Deploys each case's pipeline (job: true), times it wall-clock to completion, counts
 * indexed records for completeness, and writes a JSON + Markdown report into scripts/.
 *
 * ── Setup (one-time) ────────────────────────────────────────────────────────
 *   1. Install bun (the runner uses Bun's built-in SQL client; same as the other
 *      benchmark runners in this repo):
 *        curl -fsSL https://bun.sh/install | bash
 *   2. Install the Goldsky CLI and log in to the project you'll deploy into:
 *        curl https://goldsky.com | sh
 *        goldsky login
 *   3. Create the Postgres sink secret the pipelines write through, named
 *      MY_POSTGRES_SECRET, pointing at your database (paste the connection
 *      string when prompted):
 *        goldsky secret create --name MY_POSTGRES_SECRET
 *
 * ── Environment variables (bun auto-loads a .env file, which is gitignored) ──
 *   BENCH_PG_URL          REQUIRED. Connection string to the SAME Postgres as
 *                         MY_POSTGRES_SECRET. The pipeline WRITES through the Goldsky secret;
 *                         the runner connects here directly to COUNT(*) the sink tables and
 *                         measure timing — one database, two access paths.
 *                         e.g. postgresql://user:pass@host:5432/db?sslmode=require
 *   GOLDSKY_EDGE_SECRET   case2 only. Your Goldsky Edge RPC secret, injected into the
 *                         <YOUR_GOLDSKY_EDGE_SECRET> placeholder in goldsky-case2.yaml at deploy.
 *
 * ── Run ─────────────────────────────────────────────────────────────────────
 *   bun scripts/goldsky_turbo_benchmark.ts <caseId|all> [--iterations N]
 *     caseId: case1 | case2 | case3 | case4 | case5 | case6
 *   e.g.  bun scripts/goldsky_turbo_benchmark.ts case3
 *         bun scripts/goldsky_turbo_benchmark.ts all --iterations 3
 *   Report: scripts/goldsky_turbo_benchmark_report.{json,md}
 *
 * How it works: each run rewrites the pipeline YAML to a unique pipeline name + sink table(s)
 * so re-runs never collide; completion = sink row count stops growing AND the turbo job is no
 * longer ACTIVE; wall-clock end is anchored to last-row arrival (includes cloud provisioning,
 * the same basis as other cloud products' upload-inclusive number).
 */

import { execSync } from "child_process";
import { readFileSync, writeFileSync, existsSync, mkdtempSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { tmpdir } from "os";

// Bun runtime global (no bun-types in this repo; runner is invoked via `bun`).
declare const Bun: any;

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..");

// ---- tunables --------------------------------------------------------------
const POLL_INTERVAL_MS = 10_000;   
const SETTLE_POLLS = 9;            
const TIMEOUT_MS = 45 * 60 * 1000; 
const SCHEMA = "public";

// ---- per-case config -------------------------------------------------------
// Block ranges mirror each pipeline's source `filter:`; expected counts are the
// repo's published targets. `tables` lists every sink-table token rewritten per run
// (the `table:` value is the records table that gets counted).
interface CaseConfig {
    id: string;
    caseName: string;
    yaml: string;             
    pipelineName: string;     
    startBlock: number;
    endBlock: number;
    expected: number;
    skipValidation?: boolean; // postgres_aggregate sinks can't validate vs Neon pooler
    needsEdgeSecret?: boolean;
    // Exact column DDL of the COUNT table (the postgres `table:` sink), matching what the
    // sink INSERTs — pulled from the live sink-created schema. The runner pre-creates this
    // table (with `id` PK + an extra `inserted_at timestamptz DEFAULT now()`) so timing is
    // the exact DB-side MAX-MIN(inserted_at) span, not a ±poll estimate. `id` is the upsert
    // key for every case; reserved words (case1 `from`/`to`) are quoted; types match the
    // sink.
    timingDDL: string;
}

const CASES: Record<string, CaseConfig> = {
    case1: {
        id: "case1", caseName: "LBTC Event Only",
        yaml: "case_1_lbtc_event_only/goldsky/goldsky-case1.yaml",
        pipelineName: "goldsky-case1",
        startBlock: 0, endBlock: 22_200_000, expected: 294_278,
        timingDDL: `id text PRIMARY KEY, "from" text, "to" text, value text, block_number bigint, block_timestamp bigint, transaction_hash text`,
    },
    case2: {
        id: "case2", caseName: "LBTC Full (balances + points)",
        yaml: "case_2_lbtc_full/goldsky/goldsky-case2.yaml",
        pipelineName: "goldsky-case2",
        startBlock: 22_400_000, endBlock: 22_500_000, expected: 7_634,
        skipValidation: true, needsEdgeSecret: true,
        timingDDL: `id text PRIMARY KEY, lbtc_balance text, last_block text, last_ts text`,
    },
    case3: {
        id: "case3", caseName: "Ethereum Block",
        yaml: "case_3_ethereum_block/goldsky/goldsky-case3.yaml",
        pipelineName: "goldsky-case3",
        startBlock: 0, endBlock: 100_000, expected: 100_001,
        timingDDL: `id text PRIMARY KEY, number bigint, hash text, parent_hash text, block_timestamp bigint`,
    },
    case4: {
        id: "case4", caseName: "Transaction Gas",
        yaml: "case_4_on_transaction/goldsky/goldsky-case4.yaml",
        pipelineName: "goldsky-case4",
        startBlock: 22_280_000, endBlock: 22_290_000, expected: 1_696_641,
        skipValidation: true,
        timingDDL: `id text PRIMARY KEY, from_addr text, to_addr text, gas_value text, gas_used text, gas_price text, effective_gas_price text, block_number bigint, transaction_hash text`,
    },
    case5: {
        id: "case5", caseName: "Uniswap V2 Trace",
        yaml: "case_5_on_trace/goldsky/goldsky-case5.yaml",
        pipelineName: "goldsky-case5",
        startBlock: 22_200_000, endBlock: 22_290_000, expected: 50_191,
        skipValidation: true,
        timingDDL: `id text PRIMARY KEY, amount_in text, amount_out_min text, block_number text, deadline text, from_addr text, path text, path_length text, to_addr text, transaction_hash text`,
    },
    case6: {
        id: "case6", caseName: "Uniswap V2 Template (Factory)",
        yaml: "case_6_template/goldsky/goldsky-case6.yaml",
        pipelineName: "goldsky-case6",
        startBlock: 19_000_000, endBlock: 19_010_000, expected: 35_039,
        timingDDL: `id text PRIMARY KEY, pair text, block_number text, transaction_hash text, sender text, to_addr text, amount0_in text, amount1_in text, amount0_out text, amount1_out text`,
    },
};

interface BenchmarkResult {
    caseName: string;
    caseId: string;
    iteration: number;
    slug: string;                
    startTime: string;
    endTime: string;
    durationSeconds: number;     
    indexingSeconds: number;     
    startBlock: number;
    endBlock: number;
    totalBlocks: number;
    blocksPerSecond: number;
    recordCount: number;
    expectedCount: number;
    complete: boolean;           
    imageTag: string;            
    error?: string;
}

// ---- helpers ---------------------------------------------------------------
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const sh = (s: number) => `${(s / 60).toFixed(2)}m`;


function prepareRun(cfg: CaseConfig, iteration: number, imageTag = ""): { path: string; name: string; countTable: string } {
    const stamp = Date.now().toString(36);
    const sfxName = `-i${iteration}-${stamp}`;     
    const sfxTable = `_i${iteration}_${stamp}`;    

    let text = readFileSync(join(REPO_ROOT, cfg.yaml), "utf8");
    const name = `${cfg.pipelineName}${sfxName}`;

    const overrideBlock = (imageTag && !/^config_overrides:/m.test(text))
        ? `\nconfig_overrides:\n  image_tag: ${imageTag}` : "";
    text = text.replace(/^name:\s*.+$/m, `name: ${name}${overrideBlock}`);

    let countTable = "";
    text = text.replace(/^(\s*table:\s*)(\S+)\s*$/gm, (_m, k: string, v: string) => {
        const nt = `${v}${sfxTable}`;
        if (!countTable) countTable = nt;
        return `${k}${nt}`;
    });

    text = text.replace(/^(\s*(?:agg_table|landing_table):\s*)(\S+)\s*$/gm,
        (_m, k: string, v: string) => `${k}${v}${sfxTable}`);

    if (cfg.needsEdgeSecret) {
        const secret = process.env.GOLDSKY_EDGE_SECRET;
        if (!secret) throw new Error(`${cfg.id} needs GOLDSKY_EDGE_SECRET in the environment`);
        text = text.split("<YOUR_GOLDSKY_EDGE_SECRET>").join(secret);
    }

    if (!countTable) throw new Error(`${cfg.id}: no postgres sink table found to count in ${cfg.yaml}`);

    const dir = mkdtempSync(join(tmpdir(), "goldsky-turbo-"));
    const path = join(dir, `${name}.yaml`);
    writeFileSync(path, text);
    return { path, name, countTable };
}

function deploy(path: string, skipValidation: boolean): void {
    const flag = skipValidation ? " --skip-validation" : "";
    execSync(`goldsky turbo apply ${path}${flag}`, { stdio: "pipe", encoding: "utf8" });
}

/** Best-effort terminal-state check via the CLI. Returns the status string or null. */
function turboStatus(name: string): string | null {
    try {
        const out = execSync(`goldsky turbo get ${name} -o json`, { stdio: "pipe", encoding: "utf8" });
        const obj = JSON.parse(out);
        return (obj.status ?? obj.state ?? null) as string | null;
    } catch {
        return null;
    }
}

function cleanup(name: string): void {
    try { execSync(`goldsky turbo delete ${name}`, { stdio: "pipe" }); } catch { /* job auto-deletes anyway */ }
}

async function runBenchmark(sql: any, cfg: CaseConfig, iteration: number, imageTag = ""): Promise<BenchmarkResult> {
    const totalBlocks = cfg.endBlock - cfg.startBlock;
    console.log(`\n[${cfg.id}] iter ${iteration} — ${cfg.caseName} | blocks ${cfg.startBlock}->${cfg.endBlock} (${totalBlocks})`);

    const startTime = new Date();
    const base: BenchmarkResult = {
        caseName: cfg.caseName, caseId: cfg.id, iteration, slug: "",
        startTime: startTime.toISOString(), endTime: startTime.toISOString(),
        durationSeconds: 0, indexingSeconds: 0, startBlock: cfg.startBlock, endBlock: cfg.endBlock, totalBlocks,
        blocksPerSecond: 0, recordCount: 0, expectedCount: cfg.expected, complete: false,
        imageTag: imageTag || "default",
    };

    let name = "";
    try {
        const prep = prepareRun(cfg, iteration, imageTag);
        name = prep.name; base.slug = name;
        const countTable = prep.countTable;

        await sql.unsafe(`CREATE TABLE IF NOT EXISTS ${SCHEMA}."${countTable}" (${cfg.timingDDL}, inserted_at timestamptz DEFAULT now())`);

        deploy(prep.path, !!cfg.skipValidation);
        console.log(`[${cfg.id}] deployed ${name}; counting ${SCHEMA}.${countTable}`);

        const startMs = startTime.getTime();
        // Only accept "stable count" as completion once we're near the expected total.
        // Otherwise a slow ramp (sparse bloom-scan, xl provisioning, serial-read stalls)
        // plateaus early and we'd quit at a tiny count. Timeout is the real backstop.
        const nearDone = Math.floor(cfg.expected * 0.98);
        let lastCount = -1, stable = 0, lastChange = startMs, failed = false;
        let lastLog = startMs;

        while (Date.now() - startMs < TIMEOUT_MS) {
            await sleep(POLL_INTERVAL_MS);
            let n = -1;
            try {
                const rows = await sql.unsafe(`SELECT count(*)::int AS n FROM ${SCHEMA}."${countTable}"`);
                n = rows[0].n;
            } catch { /* table not created yet */ }

            const now = Date.now();
            if (n > lastCount) { lastCount = n; lastChange = now; stable = 0; }
            else if (n >= 0 && n === lastCount) { stable++; }

            const status = turboStatus(name);
            if (status === "Failed") { failed = true; break; }

            if (now - lastLog > 30_000) {
                console.log(`[${cfg.id}] rows=${lastCount} (${((now - startMs) / 1000).toFixed(0)}s, status=${status ?? "?"})`);
                lastLog = now;
            }

            if (lastCount >= cfg.expected) break;

            if (stable >= SETTLE_POLLS && lastCount >= nearDone) break;

            if (lastCount >= nearDone && status && !["ACTIVE", "STARTING", "PENDING", "RUNNING"].includes(status)) break;
        }

        const durationSeconds = (lastChange - startMs) / 1000;

        let indexingSeconds = 0;
        try {
            const r = await sql.unsafe(`SELECT COALESCE(EXTRACT(EPOCH FROM (max(inserted_at) - min(inserted_at))), 0) AS s FROM ${SCHEMA}."${countTable}"`);
            indexingSeconds = Number(r[0].s) || 0;
        } catch { /* table missing / empty */ }
        const recordCount = Math.max(lastCount, 0);
        const result: BenchmarkResult = {
            ...base,
            endTime: new Date(lastChange).toISOString(),
            durationSeconds,
            indexingSeconds,
            blocksPerSecond: indexingSeconds > 0 ? totalBlocks / indexingSeconds : 0,
            recordCount,
            complete: recordCount >= cfg.expected,
        };
        if (failed) result.error = "pipeline entered Failed state";
        else if (recordCount === 0) result.error = "no rows indexed (timeout?)";
        else if (recordCount < nearDone) result.error = `incomplete: ${recordCount}/${cfg.expected} at timeout`;

        console.log(`[${cfg.id}] done: ${recordCount}/${cfg.expected} rows — wall-clock ${durationSeconds.toFixed(1)}s (${sh(durationSeconds)}), indexing ${indexingSeconds.toFixed(1)}s (${sh(indexingSeconds)})${result.error ? " — " + result.error : ""}`);
        return result;
    } catch (e: any) {
        console.error(`[${cfg.id}] error: ${e.message}`);
        return { ...base, error: e.message };
    } finally {
        if (name) cleanup(name);
    }
}

// ---- report output ---------------------------------------------------------
function saveResults(results: BenchmarkResult[]): void {
    const jsonPath = join(__dirname, "goldsky_turbo_benchmark_report.json");
    const mdPath = join(__dirname, "goldsky_turbo_benchmark_report.md");

    // merge with any prior report so invocations accumulate. Key = caseId + iteration.
    let merged: BenchmarkResult[] = [];
    if (existsSync(jsonPath)) {
        try { merged = JSON.parse(readFileSync(jsonPath, "utf8")); } catch { merged = []; }
    }
    for (const r of results) {
        const i = merged.findIndex((m) => m.caseId === r.caseId && m.iteration === r.iteration);
        if (i >= 0) merged[i] = r; else merged.push(r);
    }
    merged.sort((a, b) => a.caseId.localeCompare(b.caseId) || a.iteration - b.iteration);
    for (const m of merged) delete (m as any).imageTag;
    writeFileSync(jsonPath, JSON.stringify(merged, null, 2));

    const rows = merged.map((r) =>
        `| ${r.caseId} | ${r.iteration} | ${r.durationSeconds.toFixed(1)} | ${(r.durationSeconds / 60).toFixed(2)} | ${(r.indexingSeconds ?? 0).toFixed(1)} | ${((r.indexingSeconds ?? 0) / 60).toFixed(2)} | ${r.blocksPerSecond.toFixed(0)} | ${r.recordCount} | ${r.expectedCount} | ${r.complete ? "✅" : "❌"} | ${r.error ?? "-"} |`
    ).join("\n");
    const md = `# Goldsky Turbo Benchmark Report

Generated: ${new Date().toISOString()}

**Two timings** (the benchmark mixes cloud and local indexers, measured differently):
- **Wall-clock** = \`apply\` → last row, **including cloud provisioning**. Compare against other
  **cloud** tools (Sentio, The Graph Subgraph), which include their deploy/upload the same way.
- **Indexing** = exact DB-side \`MAX-MIN(inserted_at)\` (first write → last write, both on the sink's
  clock — skew-free, provisioning excluded). Compare against **local** tools (Envio, Ponder,
  Subsquid), whose published times are spawn→done with negligible provisioning.

| Case | Iter | Wall-clock (s) | Wall-clock (min) | Indexing (s) | Indexing (min) | Idx Blocks/s | Records | Expected | Complete | Error |
|------|------|----------------|------------------|--------------|----------------|--------------|---------|----------|----------|-------|
${rows}
`;
    writeFileSync(mdPath, md);
    console.log(`\nreports written:\n  ${jsonPath}\n  ${mdPath}`);
}

// ---- main ------------------------------------------------------------------
async function main() {
    const argv = process.argv.slice(2);
    const sel = argv.find((a) => !a.startsWith("--")) ?? "";
    const iterFlag = argv.indexOf("--iterations");
    const iterations = iterFlag >= 0 ? parseInt(argv[iterFlag + 1], 10) || 1 : 1;
    // --image-tag <tag>: inject `config_overrides: { image_tag: <tag> }` at deploy time. 
    // Tagged in the report + part of the merge key, so an override run sits 
    // beside the baseline for comparison. "" = default image.
    const imgFlag = argv.indexOf("--image-tag");
    const imageTag = imgFlag >= 0 ? (argv[imgFlag + 1] ?? "") : "";

    if (!sel) {
        console.error("usage: bun scripts/goldsky_turbo_benchmark.ts <caseId|all> [--iterations N] [--image-tag <tag>]");
        console.error(`       caseId one of: ${Object.keys(CASES).join(", ")}`);
        process.exit(1);
    }
    const dryRun = argv.includes("--dry-run");
    const selected = sel === "all" ? Object.keys(CASES) : [sel];
    for (const c of selected) if (!CASES[c]) { console.error(`unknown case '${c}'. valid: ${Object.keys(CASES).join(", ")}, all`); process.exit(1); }

    if (dryRun) {
        // show what each case WOULD deploy (rewritten name + sink table), no deploy / no DB
        for (const c of selected) {
            const cfg = CASES[c];
            try {
                const prep = prepareRun(cfg, 1, imageTag);
                console.log(`[${cfg.id}] name=${prep.name}  countTable=${prep.countTable}  image_tag=${imageTag || "(default)"}  skipValidation=${!!cfg.skipValidation}  cmd: goldsky turbo apply ${prep.path}${cfg.skipValidation ? " --skip-validation" : ""}`);
            } catch (e: any) {
                console.log(`[${cfg.id}] PREP ERROR: ${e.message}`);
            }
        }
        return;
    }

    const pgUrl = process.env.BENCH_PG_URL;
    if (!pgUrl) { console.error("BENCH_PG_URL env var is required (postgres connection string to the sink DB)"); process.exit(1); }

    const sql = new Bun.SQL(pgUrl);
    const results: BenchmarkResult[] = [];
    try {
        for (const c of selected) {
            for (let i = 1; i <= iterations; i++) {
                const r = await runBenchmark(sql, CASES[c], i, imageTag);
                results.push(r);
                saveResults([r]); // intermediate save, like the canonical runners
            }
        }
    } finally {
        try { await sql.end(); } catch { /* noop */ }
    }

    console.log("\n==== summary (wall-clock = cloud incl. provisioning | indexing = processing only) ====");
    for (const r of results) {
        console.log(`${r.caseId} i${r.iteration}: ${r.recordCount}/${r.expectedCount} ${r.complete ? "✅" : "❌"} — wall-clock ${sh(r.durationSeconds)}, indexing ${sh(r.indexingSeconds)}${r.error ? " [" + r.error + "]" : ""}`);
    }
}

main().catch((e) => { console.error(e); process.exit(1); });
