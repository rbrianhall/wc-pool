#!/usr/bin/env python3
"""
Auto-updater for the 2026 World Cup Pool site.

Pulls the full World Cup fixture list + live scores from the free
football-data.org API and rewrites the `matches` and `status` sections
of data.json. Entries, teams, multiples, and meta are never touched
(except meta.lastUpdated).

Setup:
  1. Get a free API token at https://www.football-data.org/client/register
     (free tier includes the FIFA World Cup; 10 calls/min is plenty).
  2. export FOOTBALL_DATA_TOKEN=your_token
  3. python3 scripts/update_results.py

Run it on a schedule with the included GitHub Action, or by hand.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data.json"
API_URL = "https://api.football-data.org/v4/competitions/WC/matches"

# football-data.org team name -> pool team code.
# If the script reports unmapped names, add aliases here.
NAME_MAP = {
    "Mexico": "MEX", "Czechia": "CZE", "Czech Republic": "CZE",
    "Korea Republic": "KOR", "South Korea": "KOR", "South Africa": "RSA",
    "Switzerland": "SUI", "Canada": "CAN",
    "Bosnia and Herzegovina": "BIH", "Bosnia-Herzegovina": "BIH", "Qatar": "QAT",
    "Brazil": "BRA", "Morocco": "MAR", "Scotland": "SCO", "Haiti": "HAI",
    "Turkey": "TUR", "Türkiye": "TUR", "Turkiye": "TUR",
    "United States": "USA", "USA": "USA", "Paraguay": "PAR", "Australia": "AUS",
    "Germany": "GER", "Ecuador": "ECU",
    "Ivory Coast": "CIV", "Côte d'Ivoire": "CIV", "Cote d'Ivoire": "CIV",
    "Curaçao": "CUW", "Curacao": "CUW",
    "Netherlands": "NED", "Japan": "JPN", "Sweden": "SWE", "Tunisia": "TUN",
    "Belgium": "BEL", "Egypt": "EGY", "Iran": "IRN", "IR Iran": "IRN",
    "New Zealand": "NZL",
    "Spain": "ESP", "Uruguay": "URU",
    "Cape Verde": "CPV", "Cape Verde Islands": "CPV", "Cabo Verde": "CPV",
    "Saudi Arabia": "KSA",
    "France": "FRA", "Norway": "NOR", "Senegal": "SEN", "Iraq": "IRQ",
    "Argentina": "ARG", "Austria": "AUT", "Algeria": "ALG", "Jordan": "JOR",
    "Portugal": "POR", "Colombia": "COL",
    "DR Congo": "COD", "Congo DR": "COD", "Democratic Republic of the Congo": "COD",
    "Uzbekistan": "UZB",
    "England": "ENG", "Croatia": "CRO", "Panama": "PAN", "Ghana": "GHA",
}

# API stage -> (display label, knockout depth). Depth orders the bracket;
# group stage is depth 0. THIRD_PLACE handled specially.
STAGE_INFO = {
    "GROUP_STAGE":    ("Group", 0),
    "LAST_32":        ("Round of 32", 1),
    "ROUND_OF_32":    ("Round of 32", 1),
    "PLAYOFF_ROUND":  ("Round of 32", 1),
    "LAST_16":        ("Round of 16", 2),
    "ROUND_OF_16":    ("Round of 16", 2),
    "QUARTER_FINALS": ("Quarter-final", 3),
    "SEMI_FINALS":    ("Semi-final", 4),
    "THIRD_PLACE":    ("Third-place match", 5),
    "FINAL":          ("Final", 6),
}

# Achievement earned by WINNING at each knockout depth.
WIN_ACH = {1: "w32", 2: "w16", 3: "wqf", 4: "wsf", 6: "wf"}
# Achievements implied just by APPEARING at each knockout depth.
APPEAR_ACH = {1: ["advanced"], 2: ["advanced", "w32"], 3: ["advanced", "w32", "w16"],
              4: ["advanced", "w32", "w16", "wqf"], 6: ["advanced", "w32", "w16", "wqf", "wsf"]}
ACH_ORDER = ["advanced", "w32", "w16", "wqf", "wsf", "w3rd", "wf"]


def fetch_matches(token: str) -> list:
    req = urllib.request.Request(API_URL, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("matches", [])


def code_for(team: dict, unmapped: set) -> str | None:
    for key in (team.get("name"), team.get("shortName")):
        if key in NAME_MAP:
            return NAME_MAP[key]
    if team.get("name"):
        unmapped.add(team["name"])
    return None


def main() -> int:
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not token:
        print("ERROR: set FOOTBALL_DATA_TOKEN (free at football-data.org)", file=sys.stderr)
        return 1

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    api_matches = fetch_matches(token)
    if not api_matches:
        print("API returned no matches; leaving data.json untouched.")
        return 0

    unmapped: set = set()
    matches, status = [], {}
    group_label = {t[0]: f"Group {t[2]}" for t in data["teams"]}
    sf_losers, third_match = [], None

    def add_ach(code: str, ach: str):
        st = status.setdefault(code, {"state": "alive", "ach": []})
        if ach not in st["ach"]:
            st["ach"].append(ach)

    for m in api_matches:
        h = code_for(m.get("homeTeam") or {}, unmapped)
        a = code_for(m.get("awayTeam") or {}, unmapped)
        stage_label, depth = STAGE_INFO.get(m.get("stage", ""), (m.get("stage", "?"), 0))
        if stage_label == "Group" and h:
            stage_label = group_label.get(h, "Group")
        api_status = m.get("status", "")
        ui_status = ("live" if api_status in ("IN_PLAY", "PAUSED", "LIVE")
                     else "finished" if api_status == "FINISHED"
                     else "scheduled")
        score = (m.get("score") or {}).get("fullTime") or {}

        matches.append({
            "date": m.get("utcDate"),
            "stage": stage_label,
            "home": h or (m.get("homeTeam") or {}).get("tla", "TBD"),
            "away": a or (m.get("awayTeam") or {}).get("tla", "TBD"),
            "hs": score.get("home"), "as": score.get("away"),
            "status": ui_status,
            "venue": m.get("venue") or "",
        })

        # ----- derive team progress from knockout appearances/results -----
        if depth == 0 or not h or not a:
            if m.get("stage") == "THIRD_PLACE" and h and a:
                third_match = m  # handled below
            continue
        if m.get("stage") == "THIRD_PLACE":
            third_match = m
            continue
        for code in (h, a):
            for ach in APPEAR_ACH.get(depth, []):
                add_ach(code, ach)
        if ui_status == "finished":
            winner_side = (m.get("score") or {}).get("winner")
            winner = h if winner_side == "HOME_TEAM" else a if winner_side == "AWAY_TEAM" else None
            loser = a if winner == h else h if winner == a else None
            if winner and depth in WIN_ACH:
                add_ach(winner, WIN_ACH[depth])
            if loser:
                if depth == 4:           # SF loser plays the 3rd-place match
                    sf_losers.append(loser)
                    status.setdefault(loser, {"state": "alive", "ach": []})["state"] = "third"
                else:
                    status.setdefault(loser, {"state": "alive", "ach": []})["state"] = "out"

    # Third-place match resolution
    if third_match:
        h = code_for(third_match.get("homeTeam") or {}, unmapped)
        a = code_for(third_match.get("awayTeam") or {}, unmapped)
        if h and a and (third_match.get("status") == "FINISHED"):
            w = (third_match.get("score") or {}).get("winner")
            winner = h if w == "HOME_TEAM" else a if w == "AWAY_TEAM" else None
            for code in (h, a):
                st = status.setdefault(code, {"state": "alive", "ach": []})
                st["state"] = "out"
            if winner:
                add_ach(winner, "w3rd")

    # Once the R32 lineup is fully known, teams not in it are out.
    r32 = {c for m, raw in zip(matches, api_matches)
           if STAGE_INFO.get(raw.get("stage", ""), ("", 0))[1] == 1
           for c in (m["home"], m["away"]) if c in group_label}
    if len(r32) >= 32:
        for t in data["teams"]:
            code = t[0]
            if code not in r32:
                status.setdefault(code, {"state": "alive", "ach": []})["state"] = "out"

    # Keep achievement lists in canonical order
    for st in status.values():
        st["ach"] = [a for a in ACH_ORDER if a in st["ach"]]

    matches.sort(key=lambda x: x["date"] or "")

    # Only rewrite data.json when something real changed (so the workflow
    # only commits — and GitHub Pages only redeploys — on actual updates).
    changed = (matches != data.get("matches")) or (status != data.get("status"))
    if changed:
        data["matches"] = matches
        data["status"] = status
        data["meta"]["lastUpdated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(matches)} matches, {len(status)} team statuses.")
    else:
        print("No changes.")
    if unmapped:
        print("WARNING — unmapped API team names (add to NAME_MAP):", sorted(unmapped))

    # Signal to the workflow whether to keep polling every minute:
    #   live = a match is in play (incl. halftime, ET, pens)
    #   soon = a match kicks off within the next 25 minutes
    #   idle = nothing happening; sleep until the next cron tick
    now = datetime.now(timezone.utc)
    live = any(m.get("status") in ("IN_PLAY", "PAUSED", "LIVE") for m in api_matches)
    soon = False
    if not live:
        for m in api_matches:
            if m.get("status") in ("TIMED", "SCHEDULED") and m.get("utcDate"):
                ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                if 0 <= (ko - now).total_seconds() <= 25 * 60:
                    soon = True
                    break
    print("LIVE_STATE:", "live" if live else "soon" if soon else "idle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
