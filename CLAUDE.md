# CLAUDE.md — 2026 World Cup Pool Tracker

## What this is

A zero-backend static site tracking a 33-entry World Cup pool (5 country picks
each, points × country multiple). Hosted on GitHub Pages. All scoring math runs
client-side in the browser from one file: `data.json`. A GitHub Action polls
ESPN's public scoreboard and commits score/status updates; the live site
refetches `data.json` every 60s. Tournament runs June 11 – July 19, 2026.

Live site: https://<username>.github.io/wc-pool/  (deploy = commit to main)

## File map

- `index.html` — the entire app: CSS + markup + vanilla JS, no build step, no
  framework. Embedded `DEFAULT_DATA` is a fallback used only when `data.json`
  can't be fetched (e.g., opening the file directly).
- `data.json` — the single source of truth: `meta`, `teams`, `status`,
  `entries`, `matches`, `probs`.
- `scripts/update_results.py` — stdlib-only poller; rewrites `matches` +
  `status` + `probs` from ESPN/Polymarket, never touches
  `entries`/`teams`/`meta` (except `meta.lastUpdated`).
- `scripts/probs.py` — market-implied probabilities: devigs the DraftKings
  moneylines embedded in the ESPN payload + Polymarket title odds, Monte
  Carlos the remaining tournament (verified FIFA bracket in `BRACKET`),
  emits `probs.teams`. Once the R16 bracket is set, per-round strengths are
  calibrated to Polymarket's stage markets (reach-QF / reach-SF / champion —
  no market exists for reach-final or the 3rd-place match; those interpolate
  between anchors). Deterministic (input-hash seed); bump `MODEL_VERSION`
  to force a recompute after code changes.
- `scripts/import_entries.py` — imports entries from Pete's xlsx; updates
  `data.json` AND the `DEFAULT_DATA` entries block in index.html.
- `.github/workflows/update-results.yml` — two-speed poller (see below).
- `OPERATIONS.md` — commissioner runbook (manual scoring, automation,
  recovery).
- `TODO.md` — current backlog. Read it before proposing work.

## Scoring rules (the pool's, fixed — never change these)

Points × team multiple: advance from group **3**, R32 win **10**, R16 win
**10**, QF win **25**, SF win **50**, third-place-match win **25**, Final win
**100**. Payouts 50/35/15% of pot. Entry fee $35 USD.

## Data model invariants

- `status[CODE] = { state, ach }`. `ach` ⊆ [advanced, w32, w16, wqf, wsf,
  w3rd, wf] in that canonical order. `state` ∈ alive | third (lost SF,
  3rd-place match pending) | out. Teams absent from `status` = alive, no points.
- Entries may carry `"paid": false` (excluded from pot estimate) and
  `"ai": true` (ChatGPT/Gemini exhibition entries: shown in the leaderboard but
  excluded from ranks, payouts, pot, and the ticker LEADER line).
- Team codes are FIFA-style 3-letter codes; they intentionally match ESPN's
  `team.abbreviation` for all 48 teams. Unknown codes in `matches`
  (e.g. `1A`, `2B`, `QFW1`) are bracket placeholders and render as-is.
- "Best case" on the leaderboard is a constrained optimizer (`bestCase()` in
  index.html): picks are assigned the best legal combination of finishes —
  one champion (198 raw), one runner-up (98), one third (73), one fourth (48),
  QF exits (23) for the rest — respecting banked points and eliminations.
- `probs.teams[CODE] = {advanced, w32, w16, wqf, wsf, w3rd, wf, exp}` —
  market-implied P(milestone achieved by tournament end); banked milestones
  are 1.0, `exp` = expected raw points (the client multiplies by the team
  multiple for "Proj"). Unfinished `matches` may carry `odds: [pH, pD, pA]`
  (devigged DraftKings). `probs` is NOT mirrored in `DEFAULT_DATA` — the UI
  shows "—" when it's absent, by design.

## Critical gotchas (each of these bit us once — don't regress them)

1. **`DEFAULT_DATA` in index.html must mirror `data.json`** for `entries` and
   `teams` whenever they change. `import_entries.py` handles `entries`
   automatically; `teams` edits are still manual discipline (see TODO).
2. **Pages does not rebuild on bot commits.** Commits pushed with
   `GITHUB_TOKEN` don't trigger the Pages deploy workflow, so the Action
   explicitly POSTs to `/repos/{repo}/pages/builds` after each push. Never
   remove that curl step or the `pages: write` permission.
3. **football-data.org free tier serves stale WC data** — that's why we use
   ESPN (`site.api.espn.com/.../fifa.world/scoreboard?dates=20260611-20260719`,
   no key). ESPN is unofficial: if it breaks, the workflow goes red after 5
   consecutive failures, and the manual `data.json` editing path is the
   fallback.
4. **Change detection**: the script only writes `data.json` (→ commit → Pages
   redeploy) when `matches` or `status` actually changed. `meta.lastUpdated`
   is excluded from the comparison; keep it that way or every poll commits.
5. **Stage classification is by UTC datetime windows** (cutoffs at 06:00Z) in
   `STAGE_WINDOWS`, because group finales at 02:00Z spill past midnight UTC.
6. **The workflow's two speeds**: cron fires every 30 min; if a match is live
   or kicks off within 35 min, the job loops (sleep 60) through the whole
   window — ET and penalties included — capped at 350 min with a `concurrency`
   group preventing overlap. The 35-min window exists so the :30 cron tick
   latches onto top-of-hour kickoffs despite GitHub cron jitter.
7. **Odds churn must not become commit churn.** Match odds update only on a
   ≥2-point move, Polymarket title odds on ≥1 point (`apply_hysteresis`), and
   the Monte Carlo is seeded from `inputs_hash(...)` so identical inputs give
   byte-identical `probs`. Don't add `Date.now()`-style nondeterminism to
   probs.py, and after changing the model bump `MODEL_VERSION` — otherwise
   the cached `inputsHash` skips the recompute.

## Commands

```bash
python3 -m http.server 8000        # local preview at localhost:8000
python3 scripts/update_results.py  # one real poll against ESPN (safe; writes data.json only on change)
python3 -m json.tool data.json >/dev/null && echo OK   # validate after hand-edits
```

There is no test suite yet. When changing scoring logic, verify against hand
math (e.g., a team with `ach:[advanced,w32]` and multiple 10 banks 130) before
committing — and consider adding a small node/jsdom test as you go.

## Conventions

- Keep it one HTML file, vanilla JS, CSS variables, no dependencies, no build
  step. The design system: night-pitch greens, chalk lines, yellow/red "card"
  status colors, Saira Condensed display + IBM Plex Mono numerals.
- Python: stdlib only in `scripts/` (the Action runs it with no pip installs).
- Commit messages: short imperative ("Add advancement probabilities to teams
  tab"). The bot uses "Live update <timestamp>".
- After the July 19 final: delete the cron line from the workflow and leave the
  site up as the permanent record.
