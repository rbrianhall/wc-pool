# Operations

The commissioner runbook. The site needs no attention while the automation is
green — this is everything for when it isn't, plus the recurring chores.

## Manual scoring (the fallback that always works)

Edit `data.json` in the GitHub web editor and commit; the live site updates
within a minute. Two blocks matter:

**Match scores** — in `matches`: update `hs` / `as` and set `status` to
`"live"` or `"finished"`.

**Team progress** — the `status` object is the scoring source of truth:

```json
"status": {
  "MAR": { "state": "alive", "ach": ["advanced", "w32"] },
  "BRA": { "state": "out",   "ach": ["advanced"] },
  "FRA": { "state": "third", "ach": ["advanced", "w32", "w16", "wqf"] }
}
```

Achievement codes (each worth its points × the team's multiple), in canonical
order:

| Code | Meaning | Points |
| --- | --- | ---: |
| `advanced` | Advanced from group | 3 |
| `w32` | Won round-of-32 match | 10 |
| `w16` | Won round-of-16 match | 10 |
| `wqf` | Won quarterfinal | 25 |
| `wsf` | Won semifinal | 50 |
| `w3rd` | Won third-place match | 25 |
| `wf` | Won the final | 100 |

States: `alive` (default — teams omitted from `status` are alive with no
points), `third` (lost the semi, third-place match pending — renders as a
yellow card with +25 × multiple still possible), `out` (eliminated).

After hand-editing, validate:

```bash
python3 -m json.tool data.json >/dev/null && echo OK
```

## How the automation behaves

The Action (`update-results.yml`) polls ESPN's public scoreboard — no API key,
no secrets:

- **Cron fires every 30 minutes.** Each run polls once; with no live or
  imminent match it exits immediately.
- **If a match is live or kicks off within 35 minutes**, the job loops (sleep
  60) through the whole window — stoppage time, extra time, penalties — capped
  at 350 minutes, with a `concurrency` group preventing overlapping runs. The
  35-minute look-ahead exists so the :30 cron tick latches onto top-of-hour
  kickoffs despite GitHub's cron jitter.
- **Commits only on real change**: the script rewrites `data.json` only when
  `matches` or `status` actually changed (`meta.lastUpdated` is excluded from
  the comparison, deliberately — otherwise every poll would commit).
- **After each push it POSTs to `/repos/{repo}/pages/builds`.** Bot commits
  made with `GITHUB_TOKEN` do **not** trigger the Pages deploy on their own.
  Never remove that curl step or the `pages: write` permission.
- The updater never touches `entries`, `teams`, or `meta` (except
  `meta.lastUpdated`).

Manual refresh: Actions tab → "Update World Cup results" → Run workflow.

### Probabilities (the `probs` block and "Proj" column)

Each run also recomputes market-implied advancement probabilities
(`scripts/probs.py`): devigged DraftKings 1X2 lines (already inside the ESPN
payload) + Polymarket title odds (`gamma-api.polymarket.com`, free, no key)
feed an 8,000-run Monte Carlo of the remaining tournament, written to
`data.json` under `probs`. Things to know:

- **Polymarket down or changed?** The script warns and reuses the stored
  `probs.champ` values — the site keeps working with slightly stale title
  odds. Nothing goes red.
- **Commit churn is suppressed by design**: match odds only update when an
  outcome probability moves ≥ 2 points, title odds ≥ 1 point, and the Monte
  Carlo is seeded from a hash of its inputs (identical inputs → identical
  output → no commit). Expect a handful of odds-drift commits per day, not
  one per poll.
- **Changed the model code?** Bump `MODEL_VERSION` in `scripts/probs.py`,
  or the cached `inputsHash` will skip the recompute until the next real
  result.
- **Want it gone?** Delete the `probs` key from `data.json` and the probs
  section of `update_results.py`; the UI degrades gracefully (Proj shows "—").

### When the workflow goes red

It fails on purpose after 5 consecutive ESPN failures. ESPN's endpoint
(`site.api.espn.com/.../fifa.world/scoreboard`) is unofficial; if it changes
shape or disappears:

1. Run `python3 scripts/update_results.py` locally and read the error.
2. If it's a team-name mapping gap, extend the mapping in the script.
3. If ESPN is gone for good, fall back to manual scoring (above) — the site
   works fine without the Action.

(football-data.org was abandoned as a source: its free tier serves stale World
Cup data.)

## Late entries

When Pete sends an updated workbook:

```bash
python3 scripts/import_entries.py "World Cup Standings 2026 (3).xlsx"            # or --dry-run first
```

It parses the `Standings` sheet (Entrant, Team 1–5, PAID), maps names → codes
(aliases handle Turkey/TUR, Côte d'Ivoire/CIV, Bosnia/BIH, South Korea/KOR,
etc.), cross-checks per-team pick counts and multiples against the `Countries`
sheet, prints an added/changed/removed diff, and rewrites the `entries` block
in **both** `data.json` and `DEFAULT_DATA` in `index.html`. It refuses to
write if the cross-check fails (`--force` overrides). `PAID = N` becomes
`"paid": false` (excluded from the pot estimate); the `"ai": true` flags are
preserved.

## Pot and payouts

- The site estimates the pot as paid entries × `meta.entryFeeUSD` and labels
  it "est." — set `meta.potUSD` to the exact figure once Pete confirms it, and
  the label disappears.
- Payout split lives in `meta.payoutSplit`.

## After the final (July 19)

1. Delete the `cron:` line from `.github/workflows/update-results.yml` (leave
   `workflow_dispatch` for one-off corrections).
2. Verify the final `status` of every team scored correctly against hand math.
3. Leave the site up as the permanent record.
