#!/usr/bin/env python3
"""
Auto-updater for the 2026 World Cup Pool site.

Pulls all 104 fixtures + live scores from ESPN's public scoreboard API
(no API key required) and rewrites the `matches`, `status`, and `probs`
sections of data.json. Entries, teams, multiples, and meta are never touched
(except meta.lastUpdated). Run by the GitHub Action, or by hand:

    python3 scripts/update_results.py

Match odds (devigged DraftKings 1X2 from the same ESPN payload) are attached
to unfinished matches, and probs.py Monte Carlos advancement probabilities
from them plus Polymarket title odds. Both inputs use hysteresis (2pts / 1pt)
so routine line drift never causes a commit.
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probs as probs_mod  # noqa: E402

DATA_PATH = Path(__file__).resolve().parent.parent / "data.json"
API_URL = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
           "fifa.world/scoreboard?dates=20260611-20260719&limit=400")

# Knockout rounds identified by official calendar windows (UTC cutoffs at
# 06:00Z absorb late local kickoffs that spill past midnight UTC).
STAGE_WINDOWS = [
    ("2026-06-28T06", "Group", 0),
    ("2026-07-04T06", "Round of 32", 1),
    ("2026-07-08T06", "Round of 16", 2),
    ("2026-07-12T06", "Quarter-final", 3),
    ("2026-07-17T06", "Semi-final", 4),
    ("2026-07-19T06", "Third-place match", 5),
    ("9999",          "Final", 6),
]
WIN_ACH = {1: "w32", 2: "w16", 3: "wqf", 4: "wsf", 6: "wf"}
APPEAR_ACH = {1: ["advanced"], 2: ["advanced", "w32"], 3: ["advanced", "w32", "w16"],
              4: ["advanced", "w32", "w16", "wqf"], 6: ["advanced", "w32", "w16", "wqf", "wsf"]}
ACH_ORDER = ["advanced", "w32", "w16", "wqf", "wsf", "w3rd", "wf"]


def fetch_events() -> list:
    req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0 (pool tracker)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp).get("events", [])
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        print(f"ERROR: ESPN API returned HTTP {e.code}: {body}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    valid_codes = {t[0] for t in data["teams"]}
    group_label = {t[0]: f"Group {t[2]}" for t in data["teams"]}

    events = fetch_events()
    if not events:
        print("ESPN returned no events; leaving data.json untouched.")
        print("LIVE_STATE: idle")
        return 0

    matches, status, third_event = [], {}, None
    unmapped = set()
    old_odds = {(m["date"], m["home"], m["away"]): m["odds"]
                for m in data.get("matches", []) if m.get("odds")}

    def code_of(team: dict) -> str:
        ab = (team.get("abbreviation") or "").strip()
        if ab in valid_codes:
            return ab
        # Not one of the 48 -> it's a bracket placeholder (1A, 2B, RD32 W1...)
        return ab or team.get("displayName", "TBD")

    def add_ach(c: str, a: str):
        st = status.setdefault(c, {"state": "alive", "ach": []})
        if a not in st["ach"]:
            st["ach"].append(a)

    for e in events:
        comp = e["competitions"][0]
        date = e.get("date", "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", date):
            date = date[:-1] + ":00Z"          # normalize to full ISO seconds
        stage, depth = next((s, d) for cutoff, s, d in STAGE_WINDOWS if date < cutoff)

        home = away = None
        hs = as_ = None
        winner = None
        for t in comp.get("competitors", []):
            c = code_of(t.get("team", {}))
            if t.get("team", {}).get("displayName") and c not in valid_codes and len(c) == 3 and c.isalpha():
                unmapped.add(t["team"]["displayName"])
            score = t.get("score")
            sc = int(score) if score not in (None, "") else None
            if t.get("homeAway") == "home":
                home, hs = c, sc
            else:
                away, as_ = c, sc
            if t.get("winner"):
                winner = c

        state = (e.get("status", {}).get("type", {}) or {}).get("state", "pre")
        ui_status = {"pre": "scheduled", "in": "live", "post": "finished"}.get(state, "scheduled")
        if ui_status == "scheduled":
            hs = as_ = None                     # ESPN pre-match zeros -> blank

        if stage == "Group" and home in group_label:
            stage = group_label[home]

        match = {
            "date": date, "stage": stage, "home": home or "TBD", "away": away or "TBD",
            "hs": hs, "as": as_, "status": ui_status,
            "venue": (comp.get("venue") or {}).get("fullName", "") or "",
        }
        if ui_status != "finished":
            new_odds = probs_mod.implied_from_event(e)
            old = old_odds.get((match["date"], match["home"], match["away"]))
            if new_odds is None:
                if old:                          # ESPN dropped the line (e.g. live) - keep last seen
                    match["odds"] = old
            elif old and max(abs(n - o) for n, o in zip(new_odds, old)) < 0.02:
                match["odds"] = old              # hysteresis: ignore routine drift
            else:
                match["odds"] = new_odds
        matches.append(match)

        # ----- derive team progress from knockout appearances/results -----
        real_h, real_a = home in valid_codes, away in valid_codes
        if depth == 5:
            third_event = (home if real_h else None, away if real_a else None,
                           ui_status, winner)
            continue
        if depth == 0:
            continue
        for c, real in ((home, real_h), (away, real_a)):
            if real:
                for a in APPEAR_ACH[depth]:
                    add_ach(c, a)
        if ui_status == "finished" and winner in valid_codes:
            add_ach(winner, WIN_ACH[depth])
            loser = away if winner == home else home
            if loser in valid_codes:
                if depth == 4:                  # SF loser plays 3rd-place match
                    status.setdefault(loser, {"state": "alive", "ach": []})["state"] = "third"
                else:
                    status.setdefault(loser, {"state": "alive", "ach": []})["state"] = "out"

    # Third-place match: both teams are done after it; winner banks w3rd
    if third_event:
        h, a, ui, winner = third_event
        for c in (h, a):
            if c:
                st = status.setdefault(c, {"state": "alive", "ach": []})
                if ui == "finished":
                    st["state"] = "out"
        if ui == "finished" and winner:
            add_ach(winner, "w3rd")

    # Once the full R32 lineup is known, group-stage casualties are out
    r32_teams = {c for m in matches if m["stage"] == "Round of 32"
                 for c in (m["home"], m["away"]) if c in valid_codes}
    if len(r32_teams) >= 32:
        for c in valid_codes - r32_teams:
            status.setdefault(c, {"state": "alive", "ach": []})["state"] = "out"

    for st in status.values():
        st["ach"] = [a for a in ACH_ORDER if a in st["ach"]]
    matches.sort(key=lambda x: x["date"])

    # ----- advancement probabilities (T3) -----
    old_probs = data.get("probs") or {}
    name_to_code = {t[1]: t[0] for t in data["teams"]}
    champ = probs_mod.fetch_champ(name_to_code)
    if champ is None:
        champ = old_probs.get("champ") or {}
    else:
        champ = probs_mod.apply_hysteresis(champ, old_probs.get("champ"), 0.01)
    stage_new = probs_mod.fetch_stage_markets(name_to_code)
    old_stage = old_probs.get("stage") or {}
    stage = {}
    for ms in probs_mod.STAGE_MARKETS:            # per-market fallback to stored
        if ms in stage_new:
            stage[ms] = probs_mod.apply_hysteresis(stage_new[ms], old_stage.get(ms), 0.01)
        elif ms in old_stage:
            stage[ms] = old_stage[ms]
    probs = old_probs
    if champ:
        h = probs_mod.inputs_hash(matches, status, champ, stage)
        if h != old_probs.get("inputsHash"):
            result = probs_mod.compute(data["teams"], matches, status, champ, stage)
            probs = {**result, "champ": champ, "stage": stage, "inputsHash": h,
                     "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            print(f"Recomputed probs ({result['nSims']} sims, BT k={result['btK']}, "
                  f"market-anchored={result['anchored']}).")

    # Only rewrite (-> commit -> Pages redeploy) when something real changed
    changed = (matches != data.get("matches")) or (status != data.get("status")) \
        or (probs != data.get("probs"))
    if changed:
        data["matches"] = matches
        data["status"] = status
        if probs:
            data["probs"] = probs
        data["meta"]["lastUpdated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(matches)} matches, {len(status)} team statuses.")
    else:
        print("No changes.")
    if unmapped:
        print("NOTE — teams matched by placeholder only (check codes):", sorted(unmapped))

    # Signal to the workflow whether to keep polling every minute
    now = datetime.now(timezone.utc)
    live = any(m["status"] == "live" for m in matches)
    soon = False
    if not live:
        for m in matches:
            if m["status"] == "scheduled":
                ko = datetime.fromisoformat(m["date"].replace("Z", "+00:00"))
                if 0 <= (ko - now).total_seconds() <= 25 * 60:
                    soon = True
                    break
    print("LIVE_STATE:", "live" if live else "soon" if soon else "idle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
