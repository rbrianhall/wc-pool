# TODO

Backlog for the pool tracker. Each item notes the relevant files/functions so a
session can start cold. Ordered roughly by priority.

## Must do

(nothing — see Done at the bottom)

## Want to do

### T3 — Advancement probabilities per team ("live-ish" odds)
Goal: P(advance from group) per team, eventually P(reach each scoring stage),
updated at least after every completed match.
- **MVP (recommended):** model it rather than scrape it. Derive team strengths
  once from the pool multiples (they're championship odds: strength ∝
  1/multiple is a serviceable prior), then Monte Carlo the remaining group
  fixtures (10k sims) using actual results so far + the top-2-plus-8-best-
  thirds advancement rule. Pure Python in the updater, stdlib only; write
  `probs: {CODE: {advance: 0.87, r16: 0.55, ...}}` into data.json on each run.
- **Market-based alternative:** The Odds API (free tier ~500 req/mo) has match
  and outright markets; prediction markets (Kalshi/Polymarket) have public
  read APIs with team advancement contracts. More accurate, more moving parts,
  rate limits during 4-match days. Could blend: market match-odds feeding the
  same Monte Carlo.
- **In-game (stretch):** ESPN's per-event summary endpoint exposes live win
  probability for some soccer events — investigate
  `site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={id}`.
- UI: probability column in the Teams tab; per-pick percentages in the
  leaderboard expansion (see T6). Keep the displayed precision honest (whole
  percentages).

### Ideas / later
- `scripts/sync_default_data.py` — regenerate `DEFAULT_DATA` in index.html
  from data.json instead of hand-syncing (removes gotcha #1 in CLAUDE.md).
- Freeze script for July 19: final standings banner + payout amounts.
- Small node/jsdom smoke test for the scoring engine (CI on PRs).
- Group-stage standings mini-tables on the Teams tab (W-D-L, pts, GD) —
  computable from `matches`.

## Done

### T6 — Leaderboard expansion: per-pick result chips (2026-06-12)
Expanded rows now show each pick's results so far — `W 2–1 CZE`-style chips
(green/yellow/red for W/D/L, opponent's perspective-correct score) under the
team label in `renderLeaderboard()`'s detail block. Chips wrap inside the
label column; verified no overflow at 375 px. The probabilities half of the
original T6 folds into T3's UI work.

### T5 — Matches tab: rebalance live / recent / upcoming (2026-06-12)
`renderMatches()` now orders sections Live → Results → Upcoming. Results
expands today + yesterday's day-groups; Upcoming expands today through +2
days; everything else stays collapsed. A sticky mini-nav (Live/Results/
Upcoming anchors) sits under the tab bar; `scroll-margin-top` keeps jumped-to
headings clear of the sticky stack. Live matches stay pinned outside the day
groups, so the "day-group containing a live match" case can't occur.

### T4 — Ticker drops finished matches (2026-06-12)
`renderTicker()` now inserts `FT MEX 2–0 RSA` items between the live and
upcoming sections — finished matches from the current day plus the previous
2 days (viewer-local), most recent first, so the crawl stays bounded.

### T1 — Import late entries from Pete's updated workbook (2026-06-12)
`scripts/import_entries.py <xlsx> [--dry-run] [--force]` — stdlib-only parser
for the Standings/Countries sheets; maps names → codes with aliases,
cross-checks pick counts + multiples against the Countries sheet, prints an
added/changed/removed diff, rewrites `entries` in both `data.json` and
`DEFAULT_DATA`. Imported 5 late entries (Kevin B., Marcus SK. #1–3, Steve K.)
→ 38 total, 36 paid, pot est. $1,260. `meta.potUSD` still unset — waiting on
Pete to confirm the exact pot. NOTE: Pete's Pot sheet shows a 55/30/15 payout
split vs our 50/35/15 — confirm with Pete before any payout math goes live.

### T2 — Rewrite README.md as repo documentation (2026-06-12)
README is now visitor-facing (what it is, screenshot at `docs/screenshot.png`,
architecture diagram, scoring table, repo map); the commissioner material
moved to `OPERATIONS.md` (manual-scoring vocabulary incl. the achievement-code
table, automation behavior + red-workflow recovery, late-entry import, pot,
post-final freeze). README no longer mentions the abandoned football-data.org
token setup.
