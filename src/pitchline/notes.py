"""Shot Notes: short, factual notes written automatically from the stored matches.

Every note is a plain statement the data can back up: a count, a date window, a minimum-sample rule where one
applies. No note is invented or estimated. The notes act as the site's news feed.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

WINDOW_DAYS = 14
MIN_SHOTS_ACCURACY = 60
MIN_FORM_SOT = 3
KIND_ORDER = ("hattrick", "form", "heavy", "per90", "accuracy")


def _d(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def _nice(d: date) -> str:
    return f"{d:%b} {d.day}"


def _pid(players: dict, lg: str, p: dict, names: dict) -> str | None:
    aid = p.get("athlete_id")
    if aid and (lg, aid) in players:
        return aid
    ids = names.get((lg, p.get("player")), [])
    return ids[0] if len(ids) == 1 else None


def shot_notes(recs: list[dict], players: dict, lname, limit: int = 12) -> list[dict]:
    """Returns notes as dicts: kind, league, pid (or None), text, date. Newest and most varied first."""
    names: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (lg, pid), row in players.items():
        names[(lg, row["player"])].append(pid)
    latest: dict[str, date] = {}
    for r in recs:
        d = _d(r.get("date"))
        if d and (r["league"] not in latest or d > latest[r["league"]]):
            latest[r["league"]] = d

    notes: list[dict] = []
    # league -> player id -> [shots on target, name, team] inside the window
    form: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [0, "", ""]))
    for r in recs:
        lg, d = r["league"], _d(r.get("date"))
        if not d or lg not in latest or (latest[lg] - d).days >= WINDOW_DAYS:
            continue
        teams = [t["name"] for t in r.get("teams") or []]
        for p in r.get("players", []):
            pid = _pid(players, lg, p, names)
            if pid is None:
                continue
            f = form[lg][pid]
            f[0] += p.get("shots_on_target") or 0
            f[1], f[2] = p["player"], p["team"]
            if (p.get("goals") or 0) >= 3 and len(teams) == 2:
                opp = next((t for t in teams if t != p["team"]), "")
                notes.append({"kind": "hattrick", "league": lg, "pid": pid, "date": d.isoformat(),
                              "text": f"{p['player']} scored {p['goals']} goals for {p['team']} against {opp} "
                                      f"on {_nice(d)} ({lname(lg)})."})
        tot = r.get("boxscore_totals") or {}
        try:
            shots = {t: int(tot[t]["totalShots"]) for t in teams}
        except (KeyError, TypeError, ValueError):
            shots = {}
        if len(shots) == 2 and sum(shots.values()) >= 30:
            a, b = teams
            hi, lo = (a, b) if shots[a] >= shots[b] else (b, a)
            score = {t["name"]: t.get("score") for t in r.get("teams", [])}
            notes.append({"kind": "heavy", "league": lg, "pid": None, "date": d.isoformat(),
                          "weight": sum(shots.values()),
                          "text": f"{hi} had {shots[hi]} shots to {lo}'s {shots[lo]} on {_nice(d)} "
                                  f"(final score {score.get(a)}-{score.get(b)}, {lname(lg)})."})
    for lg, by in form.items():
        pid, (sot, name, team) = max(by.items(), key=lambda kv: (kv[1][0], kv[1][1]))
        if sot >= MIN_FORM_SOT:
            notes.append({"kind": "form", "league": lg, "pid": pid, "date": latest[lg].isoformat(),
                          "text": f"{name} ({team}) has {sot} shots on target in the {WINDOW_DAYS} days to "
                                  f"{_nice(latest[lg])}, the most in the {lname(lg)}."})

    by_league: dict[str, list[dict]] = defaultdict(list)
    for (lg, _), row in players.items():
        by_league[lg].append(row)
    for lg, rows in by_league.items():
        d = latest.get(lg)
        if not d:
            continue
        p90 = [r for r in rows if r.get("sot90") is not None]
        if p90:
            r = max(p90, key=lambda r: (r["sot90"], r["player"]))
            notes.append({"kind": "per90", "league": lg, "pid": r["pid"], "date": d.isoformat(),
                          "text": f"{r['player']} ({r['team']}) averages {r['sot90']:.2f} shots on target per 90 "
                                  f"minutes, the best in the {lname(lg)} among players with 900+ minutes."})
        acc = [r for r in rows if r["shots"] >= MIN_SHOTS_ACCURACY]
        if acc:
            r = max(acc, key=lambda r: (r["shots_on_target"] / r["shots"], r["player"]))
            notes.append({"kind": "accuracy", "league": lg, "pid": r["pid"], "date": d.isoformat(),
                          "text": f"{r['player']} ({r['team']}) puts {100 * r['shots_on_target'] / r['shots']:.0f}% of "
                                  f"shots on target ({r['shots_on_target']} of {r['shots']}), the best in the "
                                  f"{lname(lg)} among players with {MIN_SHOTS_ACCURACY}+ shots."})

    notes.sort(key=lambda n: (n["date"], n.get("weight", 0)), reverse=True)
    queues = {k: [n for n in notes if n["kind"] == k] for k in KIND_ORDER}
    picked: list[dict] = []
    while len(picked) < limit and any(queues.values()):
        for k in KIND_ORDER:
            if queues[k] and len(picked) < limit:
                picked.append(queues[k].pop(0))
    return picked
