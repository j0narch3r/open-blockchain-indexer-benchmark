# CLAUDE.md

Agent operating instructions for Contour. This is §9 of `docs/SPEC.md`, reproduced verbatim.

## 9. Agent operating instructions (`CLAUDE.md` content)

Write these into `CLAUDE.md` at repo root and follow them.

### 9.1 Loop
For every ticket: **read spec section → write failing test → implement → run `make verify` → commit → append to `docs/DECISIONS.md` → next ticket.**

`make verify` must run: lint, typecheck, unit tests, and (from Milestone 3 onward) `make eval`. **Do not commit with `make verify` red.** Do not disable, skip, or loosen a test to make it pass — if a test is wrong, fix it in a separate commit with a written justification in `DECISIONS.md`.

### 9.2 Branch/commit discipline
- One branch per milestone: `m1-scaffold`, `m2-elevation`, …
- Conventional commits. Small commits.
- PR per milestone with a body containing: what changed, eval scorecard delta, open risks.

### 9.3 When to stop and ask (hard stops)
Stop and write `docs/BLOCKED.md`, then halt, if:
- A paid API key is required that isn't already in `.env`
- A decision would exceed **$50/month** in recurring cost
- The Wiggle test cannot be made to pass after **three** distinct approaches
- A dependency requires a license incompatible with commercial use
- Anything requires publishing to an app store, registering a domain, or making a payment

### 9.4 Guardrails
- **Never commit secrets.** `.env` is gitignored; `.env.example` is committed with empty values.
- **Never call paid APIs from tests.** Tests use fixtures in `services/api/tests/fixtures/`.
- Rate-limit all outbound data downloads; cache aggressively; never re-download an unchanged extract.
- Do not add a dependency without recording why in `DECISIONS.md`.
- Do not expand scope. If an idea is good but out of scope, write it to `docs/BACKLOG.md` and move on.

### 9.5 Verify before you trust your memory
For GraphHopper custom-model syntax, Valhalla costing options, MapLibre React Native props, and Expo config plugins: **fetch the current official documentation** before writing code against them. These APIs have changed. A confident wrong call here costs a whole milestone.
