#!/usr/bin/env python3
"""
Market-fed advancement probabilities (T3).

Two free, keyless market sources feed a Monte Carlo of the remaining
tournament:

  * ESPN scoreboard odds — every event in the scoreboard payload the updater
    already fetches carries a DraftKings 1X2 moneyline. Devigged, these give
    P(home/draw/away) for every remaining group match (and knockout matches
    once their teams are known).
  * Polymarket gamma API — the `world-cup-winner` event prices P(champion)
    per team. Those anchor a Bradley-Terry strength per team (exponent
    calibrated against the ESPN match odds) used for matches the sportsbook
    hasn't priced yet, i.e. future knockout rounds.

compute() replays the tournament from the current matches/status state
N_SIMS times and returns, per team, P(reaching each scoring milestone) plus
expected raw points "exp" (multiply by the pool multiple client-side).

Deterministic: the RNG seed is a hash of the inputs, so identical inputs
always produce identical output and never trigger a data.json commit.

Stdlib only — runs in the GitHub Action with no pip installs.
"""

import hashlib
import json
import random
import sys
import urllib.request

POLYMARKET_URL = "https://gamma-api.polymarket.com/events?slug=world-cup-winner"

# Polymarket question names that differ from data.json team names
PM_ALIASES = {
    "Bosnia-Herzegovina": "Bosnia",
    "Turkiye": "Türkiye",
    "Ivory Coast": "Côte d'Ivoire",
    "Congo DR": "DR Congo",
    "Cape Verde": "Cabo Verde",
}

PTS = {"advanced": 3, "w32": 10, "w16": 10, "wqf": 25, "wsf": 50, "w3rd": 25, "wf": 100}
MILESTONES = ["advanced", "w32", "w16", "wqf", "wsf", "w3rd", "wf"]
WIN_ACH = {"Round of 32": "w32", "Round of 16": "w16", "Quarter-final": "wqf",
           "Semi-final": "wsf", "Final": "wf"}

CHAMP_FLOOR = 0.001     # strength floor for teams the market prices at ~0
DRAW_SHARE = 0.24       # group-match draw probability when no market odds exist
N_SIMS = 8000
MODEL_VERSION = 2       # bump to force a recompute after model/code changes

# Knockout bracket wiring, FIFA matches 73–104, verified against the official
# schedule (Wikipedia "2026 FIFA World Cup knockout stage" + fifa.com, June
# 2026; all 16 R32 pairings cross-checked against ESPN's fixture list). Each
# round lists (a, b) feeder indices into the previous round's DATE-SORTED
# match list: e.g. R16 game 0 (Houston, Jul 4 = FIFA M90) is played between
# the winners of R32 games 0 (2A v 2B) and 3 (1F v 2C).
BRACKET = {
    "Round of 16":   [(0, 3), (2, 5), (1, 4), (6, 7), (11, 10), (9, 8), (14, 13), (12, 15)],
    "Quarter-final": [(1, 0), (4, 5), (2, 3), (6, 7)],
    "Semi-final":    [(0, 1), (2, 3)],
    "Final":         [(0, 1)],
}

# Score sampling for simulated matches (only feeds GD/GF tiebreakers).
WIN_MARGINS = [(1, 0.55), (2, 0.28), (3, 0.12), (4, 0.05)]
LOSER_GOALS = [(0, 0.55), (1, 0.33), (2, 0.12)]
DRAW_GOALS = [(0, 0.28), (1, 0.47), (2, 0.20), (3, 0.05)]


def _american_to_prob(odds_str):
    """'+130' / '-160' / 250 -> implied probability (vig included)."""
    try:
        v = int(str(odds_str).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    return 100.0 / (v + 100.0) if v > 0 else -v / (-v + 100.0)


def implied_from_event(event):
    """Devigged [home, draw, away] probabilities from an ESPN scoreboard
    event, or None if the event carries no usable moneyline."""
    comp = (event.get("competitions") or [{}])[0]
    arr = comp.get("odds") or []
    odds = arr[0] if arr and isinstance(arr[0], dict) else {}
    ml = odds.get("moneyline") or {}
    h = _american_to_prob(((ml.get("home") or {}).get("close") or {}).get("odds"))
    a = _american_to_prob(((ml.get("away") or {}).get("close") or {}).get("odds"))
    d_ml = (odds.get("drawOdds") or {}).get("moneyLine")
    d = _american_to_prob(d_ml)
    if h is None or a is None or d is None:
        return None
    s = h + d + a
    return [round(h / s, 3), round(d / s, 3), round(a / s, 3)] if s > 0 else None


def fetch_champ(name_to_code):
    """{code: P(champion)} from Polymarket's world-cup-winner event, or None
    on any failure (caller keeps the previously stored values)."""
    lookup = dict(name_to_code)
    for pm_name, our_name in PM_ALIASES.items():
        if our_name in name_to_code:
            lookup[pm_name] = name_to_code[our_name]
    try:
        req = urllib.request.Request(
            POLYMARKET_URL, headers={"User-Agent": "Mozilla/5.0 (pool tracker)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            markets = json.load(resp)[0]["markets"]
    except Exception as e:                                    # noqa: BLE001
        print(f"WARN: Polymarket fetch failed ({e}); keeping stored champ odds.",
              file=sys.stderr)
        return None
    champ = {}
    for m in markets:
        q = m.get("question", "")
        if not q.startswith("Will ") or " win the 2026 FIFA World Cup" not in q:
            continue
        code = lookup.get(q[5:q.index(" win the 2026")])
        if not code:
            continue
        try:
            champ[code] = round(float(json.loads(m["outcomePrices"])[0]), 3)
        except (KeyError, ValueError, IndexError, TypeError):
            continue
    return champ if len(champ) >= 24 else None


def apply_hysteresis(new, old, threshold):
    """Per-key: keep the old value unless it moved >= threshold, so constant
    line drift doesn't produce a commit every poll."""
    if not old:
        return new
    return {k: (old[k] if k in old and abs(v - old[k]) < threshold else v)
            for k, v in new.items()}


def fit_strengths(champ, market_matches):
    """Bradley-Terry strength per team: champ_prob ** (1/k), k chosen so BT
    win shares best match the market's decisive-outcome shares across all
    priced matches. Returns ({code: strength}, k)."""
    best_k, best_err = 7.0, float("inf")
    for k10 in range(10, 121, 2):                # k = 1.0 .. 12.0 step 0.2
        k = k10 / 10.0
        r = {c: max(p, CHAMP_FLOOR) ** (1.0 / k) for c, p in champ.items()}
        err, n = 0.0, 0
        for ch, ca, h, _d, a in market_matches:
            if ch in r and ca in r and (h + a) > 0:
                err += (h / (h + a) - r[ch] / (r[ch] + r[ca])) ** 2
                n += 1
        if n and err / n < best_err:
            best_err, best_k = err / n, k
    return ({c: max(p, CHAMP_FLOOR) ** (1.0 / best_k) for c, p in champ.items()},
            best_k)


def _sample(rng, pairs):
    x = rng.random()
    acc = 0.0
    for v, p in pairs:
        acc += p
        if x < acc:
            return v
    return pairs[-1][0]


def inputs_hash(matches, status, champ):
    sig = json.dumps([
        MODEL_VERSION,
        [[m["date"], m["stage"], m["home"], m["away"], m["hs"], m["as"],
          m["status"], m.get("odds")] for m in matches],
        status, champ,
    ], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def compute(teams, matches, status, champ):
    """Monte Carlo the remaining tournament. Returns the data.json `probs`
    payload (without bookkeeping fields, which the caller adds)."""
    codes = [t[0] for t in teams]
    code_set = set(codes)
    group_of = {t[0]: t[2] for t in teams}
    groups = sorted({t[2] for t in teams})

    champ_full = {c: max(champ.get(c, 0.0), CHAMP_FLOOR) for c in codes}
    market_matches = [(m["home"], m["away"], *m["odds"]) for m in matches
                      if m.get("odds") and m["home"] in code_set and m["away"] in code_set]
    strength, bt_k = fit_strengths(champ_full, market_matches)

    def bt(a, b):
        ra, rb = strength[a], strength[b]
        return ra / (ra + rb)

    # ---- static prep ------------------------------------------------------
    base_tables = {g: {} for g in groups}            # code -> [pts, gd, gf]
    for c in codes:
        base_tables[group_of[c]][c] = [0, 0, 0]
    pending_group = []                               # (home, away, pH, pD)
    for m in matches:
        if not m["stage"].startswith("Group"):
            continue
        h, a = m["home"], m["away"]
        if h not in code_set or a not in code_set:
            continue
        if m["status"] == "finished" and m["hs"] is not None:
            hs, as_ = m["hs"], m["as"]
            th, ta = base_tables[group_of[h]][h], base_tables[group_of[a]][a]
            th[1] += hs - as_; th[2] += hs
            ta[1] += as_ - hs; ta[2] += as_
            if hs > as_:   th[0] += 3
            elif hs < as_: ta[0] += 3
            else:          th[0] += 1; ta[0] += 1
        else:
            if m.get("odds"):
                ph, pd = m["odds"][0], m["odds"][1]
            else:
                pb = bt(h, a)
                ph, pd = (1 - DRAW_SHARE) * pb, DRAW_SHARE
            pending_group.append((h, a, ph, pd))

    ko = {s: [] for s in list(WIN_ACH) + ["Third-place match"]}
    for m in matches:
        if m["stage"] in ko:
            ko[m["stage"]].append(m)
    for s in ko:
        ko[s].sort(key=lambda m: m["date"])

    # Constrained best-third slots in the R32, e.g. "3ABCDF"
    third_slots = []                                 # allowed-group sets
    third_slot_idx = {}                              # (date, side) -> index
    for m in ko["Round of 32"]:
        for side in ("home", "away"):
            slot = m[side]
            if slot not in code_set and slot.startswith("3") and len(slot) > 1:
                third_slot_idx[(m["date"], side)] = len(third_slots)
                third_slots.append(set(slot[1:]))

    def ko_winner(m, ach_key):
        """Winner of a finished knockout match (status carries pens results)."""
        if m["hs"] is not None and m["hs"] != m["as"]:
            return m["home"] if m["hs"] > m["as"] else m["away"]
        for c in (m["home"], m["away"]):
            if c in status and ach_key in status[c].get("ach", []):
                return c
        return None

    def p_advance(h, a, odds):
        """P(home advances) in a knockout tie; draws resolved by BT share."""
        if odds:
            ph, pd = odds[0], odds[1]
            return min(1.0, ph + pd * bt(h, a))
        return bt(h, a)

    def assign_thirds(best8, rng):
        """Match the 8 advancing third-place groups to constrained slots via
        backtracking; random relaxed fill if no perfect matching exists."""
        order = sorted(range(len(third_slots)), key=lambda i: len(third_slots[i]))
        used, out = set(), {}

        def go(i):
            if i == len(order):
                return True
            si = order[i]
            cands = [g for g in best8 if g in third_slots[si] and g not in used]
            rng.shuffle(cands)
            for g in cands:
                used.add(g); out[si] = g
                if go(i + 1):
                    return True
                used.discard(g); del out[si]
            return False

        if third_slots and not go(0):
            out.clear()
            pool = list(best8)
            rng.shuffle(pool)
            for si in range(len(third_slots)):
                out[si] = pool[si % len(pool)]
        return out

    rng = random.Random(int(inputs_hash(matches, status, champ_full), 16))
    counts = {c: {ms: 0 for ms in MILESTONES} for c in codes}

    # ---- simulate ----------------------------------------------------------
    for _ in range(N_SIMS):
        tables = {g: {c: row[:] for c, row in base_tables[g].items()} for g in groups}
        for h, a, ph, pd in pending_group:
            x = rng.random()
            th, ta = tables[group_of[h]][h], tables[group_of[a]][a]
            if x < ph:
                th[0] += 3
                mgn = _sample(rng, WIN_MARGINS); lg = _sample(rng, LOSER_GOALS)
                th[1] += mgn; th[2] += lg + mgn; ta[1] -= mgn; ta[2] += lg
            elif x < ph + pd:
                th[0] += 1; ta[0] += 1
                g = _sample(rng, DRAW_GOALS)
                th[2] += g; ta[2] += g
            else:
                ta[0] += 3
                mgn = _sample(rng, WIN_MARGINS); lg = _sample(rng, LOSER_GOALS)
                ta[1] += mgn; ta[2] += lg + mgn; th[1] -= mgn; th[2] += lg

        pos = {}                                     # "1A"/"2A" -> code
        thirds = []
        for g in groups:
            ranked = sorted(tables[g].items(),
                            key=lambda kv: (kv[1][0], kv[1][1], kv[1][2], rng.random()),
                            reverse=True)
            pos["1" + g], pos["2" + g] = ranked[0][0], ranked[1][0]
            c3, row3 = ranked[2]
            thirds.append(((row3[0], row3[1], row3[2], rng.random()), g, c3))
        thirds.sort(reverse=True)
        best8 = [g for _k, g, _c in thirds[:8]]
        third_code = {g: c for _k, g, c in thirds}
        slot_assign = assign_thirds(best8, rng)

        for c in set(pos.values()) | {third_code[g] for g in best8}:
            counts[c]["advanced"] += 1

        def resolve(m, side):
            slot = m[side]
            if slot in code_set:
                return slot
            if slot in pos:
                return pos[slot]
            si = third_slot_idx.get((m["date"], side))
            if si is not None and si in slot_assign:
                return third_code[slot_assign[si]]
            return None

        winners_prev, sf_losers = [], []
        for stage in WIN_ACH:                        # R32 .. Final, in order
            ach = WIN_ACH[stage]
            winners = []
            for gi, m in enumerate(ko[stage]):
                if stage == "Round of 32":
                    h, a = resolve(m, "home"), resolve(m, "away")
                else:
                    feeders = BRACKET.get(stage) or []
                    fa, fb = feeders[gi] if gi < len(feeders) else (None, None)
                    h = m["home"] if m["home"] in code_set else \
                        (winners_prev[fa] if fa is not None and fa < len(winners_prev) else None)
                    a = m["away"] if m["away"] in code_set else \
                        (winners_prev[fb] if fb is not None and fb < len(winners_prev) else None)
                if h is None or a is None:
                    winners.append(None)
                    continue
                if m["status"] == "finished":
                    w = ko_winner(m, ach)
                    if w is None:
                        w = h if rng.random() < p_advance(h, a, m.get("odds")) else a
                else:
                    odds = m.get("odds") if (m["home"] == h and m["away"] == a) else None
                    w = h if rng.random() < p_advance(h, a, odds) else a
                counts[w][ach] += 1
                winners.append(w)
                if stage == "Semi-final":
                    sf_losers.append(a if w == h else h)
            winners_prev = winners

        tp = ko["Third-place match"][0] if ko["Third-place match"] else None
        if tp is not None:
            if tp["home"] in code_set and tp["away"] in code_set:
                h, a = tp["home"], tp["away"]
            elif len(sf_losers) == 2 and all(sf_losers):
                h, a = sf_losers
            else:
                h = a = None
            if h and a:
                if tp["status"] == "finished":
                    w = ko_winner(tp, "w3rd")
                else:
                    odds = tp.get("odds") if (tp["home"] == h and tp["away"] == a) else None
                    w = h if rng.random() < p_advance(h, a, odds) else a
                if w:
                    counts[w]["w3rd"] += 1

    # ---- aggregate ---------------------------------------------------------
    out = {}
    for c in codes:
        st = status.get(c, {})
        banked = set(st.get("ach", []))
        probs = {}
        for ms in MILESTONES:
            if ms in banked:
                p = 1.0
            elif st.get("state") == "out":
                p = 0.0
            elif st.get("state") == "third" and ms != "w3rd":
                p = 0.0
            else:
                p = counts[c][ms] / N_SIMS
            probs[ms] = round(p, 3)
        probs["exp"] = round(sum(PTS[ms] * probs[ms] for ms in MILESTONES), 1)
        out[c] = probs
    return {"teams": out, "btK": bt_k, "nSims": N_SIMS}
