# 2026 World Cup Pool Tracker

A zero-backend, free-to-host site for tracking the pool: live leaderboard, match tracker with "who has skin in this game" stakes, all 48 teams with multiples, and pool stats. All scoring math (points, ceilings, payouts) is computed in the browser from one file: `data.json`.

## Hosting on GitHub Pages (free, ~5 minutes)

1. Create a new GitHub repo (public), e.g. `wc-pool`.
2. Upload everything in this folder (drag-and-drop works in the GitHub web UI; include the `.github` folder if you want auto-updating).
3. Repo **Settings → Pages → Source: Deploy from a branch → main / (root)**.
4. Your site is live at `https://<username>.github.io/wc-pool/`. Share that link with the league.

Alternatives: drag the folder onto [Netlify Drop](https://app.netlify.com/drop) or a Cloudflare Pages project — both free. GitHub Pages is recommended because updating `data.json` *is* the deploy.

## Before kickoff

1. **Entries** — replace the sample `entries` in `data.json` with the real participants and their five picks (use the team codes from the `teams` array). Duplicate entries from the same person are fine — just name them "Pete #1", "Pete #2".
2. **Pot** — set `meta.potUSD` to the real pot once entries close (the site otherwise estimates entries × $35 and labels it "est.").

## Keeping scores updated — two options

### Option A: Automatic (recommended)

The full 104-match official schedule ships baked into `data.json`, and a GitHub Action keeps it live. The Action runs on a two-speed loop: a cron dispatcher fires every 30 minutes and polls the free [football-data.org](https://www.football-data.org/client/register) API once. If nothing is happening it exits immediately. But if a match is live — or kicks off within 25 minutes — the job stays alive and re-polls **every 60 seconds**, through stoppage time, extra time, and penalties, committing `data.json` only when a score or status actually changed. The site refetches every 60 seconds, so viewers see updates roughly 1–2.5 minutes behind real time (poll interval + GitHub Pages redeploy).

Setup:
1. Register for a free API token at football-data.org (the free tier includes the World Cup; the loop's 1 call/min is well under the 10/min limit).
2. Repo **Settings → Secrets and variables → Actions → New repository secret**: name `FOOTBALL_DATA_TOKEN`, value = your token.
3. Go to the **Actions** tab, enable workflows, and run "Update World Cup results" once manually to verify (green check + "No changes." or a commit).
4. If the run log warns about unmapped team names, add them to `NAME_MAP` in `scripts/update_results.py`.

Notes on the loop: GitHub cron ticks can fire several minutes late, but that only delays *entering* a match window — once in, updates flow every 60s on the wall clock. The `concurrency` group guarantees only one poller runs at a time, and each job self-terminates when the last match of the window ends (or at the 350-minute safety cap). Public-repo Actions minutes are free and unlimited. Delete the cron line after the final on July 19.

### Option B: Manual (no API, works from a phone)

Edit `data.json` in the GitHub web editor after each match:

- **Match scores** — update `hs` / `as` and set `status` to `"live"` or `"finished"`.
- **Team progress** — the `status` object is the scoring source of truth:

```json
"status": {
  "MAR": { "state": "alive", "ach": ["advanced", "w32"] },
  "BRA": { "state": "out",   "ach": ["advanced"] },
  "FRA": { "state": "third", "ach": ["advanced", "w32", "w16", "wqf"] }
}
```

Achievement codes (each is worth its points × the team's multiple):
`advanced` (3) · `w32` (10) · `w16` (10) · `wqf` (25) · `wsf` (50) · `w3rd` (25) · `wf` (100)

States: `alive` (default — teams omitted from `status` are alive with no points), `third` (lost the semi, third-place match pending), `out` (eliminated).

Commit the edit and the live site updates within a minute.

## Local preview

```bash
cd wc-pool && python3 -m http.server 8000
# open http://localhost:8000
```

Opening `index.html` directly also works — it falls back to the data embedded in the file, so edit `data.json` and use a local server (or the live site) to see real data.

## Notes

- "Best case" slots each entry's picks into the best legal combination of finishes (one champion, one runner-up, one third, one fourth, QF exits beyond that), respecting banked points and eliminations. It still ignores bracket geometry, so it remains a tight upper bound. Entries flagged "ai": true are exhibition entries excluded from ranks, payouts, and the pot.
- The third-place match is handled: a semifinal loser shows as a yellow card with +25 × multiple still possible.
- Times display in each viewer's local timezone automatically.
