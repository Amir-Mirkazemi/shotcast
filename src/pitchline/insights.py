"""Plain-statistics views over the stored match files, used by the website's feature pages.

Nothing here writes HTML. Each function takes the match records (and, for match pages, saved forecasts) and returns
dicts and lists: rolling form per player, career rows by season / club / competition, per-season rows for the
competition filters, and a match view with predicted versus actual shots on target.
"""
from __future__ import annotations

from collections import defaultdict

from .sitedata import pitch_coords

ROLL_WINDOWS = (5, 10)
MIN_SERIES = 5          # a form chart needs at least this many matches with a stored lineup


def season_of(date: str) -> str:
    """European-style season label: July starts a new season. '2025-09-14' -> '2025-26'."""
    try:
        y, m = int(date[:4]), int(date[5:7])
    except (ValueError, IndexError):
        return "unknown"
    start = y if m >= 7 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def rolling(values: list[float], n: int) -> list[float | None]:
    """Mean of the last n values at each point; None until n values exist."""
    out: list[float | None] = []
    for i in range(len(values)):
        out.append(None if i + 1 < n else round(sum(values[i + 1 - n:i + 1]) / n, 3))
    return out


def _blank() -> dict:
    return {"apps": 0, "minutes": 0, "shots": 0, "sot": 0, "goals": 0, "assists": 0, "matches": 0}


def player_history(recs: list[dict]) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """(series, career) keyed by ESPN athlete id.

    series[aid]: one entry per match with a stored lineup, oldest first: date, competition, team, opponent, shots
    on target, shots, goals, assists and minutes. Players with no stat line in a match they played count as zeros,
    which is why only matches with a lineup are used: without one the zeros are unknown.
    career[aid]: {"name", "rows": {(season, team, league): totals}} over every stored match."""
    series: dict[str, list[dict]] = defaultdict(list)
    career: dict[str, dict] = {}
    for rec in sorted(recs, key=lambda r: (r.get("date") or "", str(r.get("event_id")))):
        date, lg = (rec.get("date") or "")[:10], rec["league"]
        season = season_of(date)
        names = [t["name"] for t in rec.get("teams") or []]
        lineup = {p["athlete_id"]: p for p in rec.get("lineup") or [] if p.get("athlete_id")}
        stats = {p["athlete_id"]: p for p in rec.get("players") or [] if p.get("athlete_id")}
        for aid in lineup.keys() | stats.keys():
            lp, sp = lineup.get(aid), stats.get(aid) or {}
            team = (lp or sp)["team"]
            sot, shots = int(sp.get("shots_on_target") or 0), int(sp.get("shots") or 0)
            goals, assists = int(sp.get("goals") or 0), int(sp.get("assists") or 0)
            mins = int(lp.get("minutes") or 0) if lp else 0
            entry = career.setdefault(aid, {"name": (lp or sp)["player"], "rows": {}})
            row = entry["rows"].setdefault((season, team, lg), _blank())
            row["matches"] += 1
            row["apps"] += bool(lp)
            row["minutes"] += mins
            row["shots"] += shots
            row["sot"] += sot
            row["goals"] += goals
            row["assists"] += assists
            if lineup:
                series[aid].append({"date": date, "league": lg, "team": team, "event_id": str(rec["event_id"]),
                                    "opp": next((n for n in names if n != team), ""), "sot": sot, "shots": shots,
                                    "goals": goals, "assists": assists, "minutes": mins})
    return series, career


def form_chart(series: list[dict]) -> dict | None:
    """Data for the rolling-form chart, or None when there are too few matches to say anything."""
    if len(series) < MIN_SERIES:
        return None
    sot = [float(m["sot"]) for m in series]
    out = {"dates": [m["date"] for m in series], "sot": sot,
           "labels": [f"{m['team']} v {m['opp']}" if m["opp"] else m["team"] for m in series]}
    for n in ROLL_WINDOWS:
        out[f"r{n}"] = rolling(sot, n)
    return out


def career_table(career: dict) -> list[dict]:
    """Rows by season, club and competition, newest season first."""
    rows = []
    for (season, team, lg), t in career["rows"].items():
        rows.append({"season": season, "team": team, "league": lg, **t,
                     "sot90": round(t["sot"] * 90 / t["minutes"], 2) if t["minutes"] >= 600 else None})
    rows.sort(key=lambda r: (r["season"], r["minutes"], r["sot"]), reverse=True)
    return rows


def club_stints(career: dict) -> list[dict]:
    """Clubs in the order the player appears for them in the stored matches. This is read off match files, so it
    shows moves between clubs inside the stored date range, not transfer fees or a full transfer history."""
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    for (season, team, _lg) in career["rows"]:
        first[team] = min(first.get(team, season), season)
        last[team] = max(last.get(team, season), season)
    return [{"team": t, "from": first[t], "to": last[t]} for t in sorted(first, key=lambda t: (first[t], last[t]))]


def season_rows(career: dict[str, dict], league: str, linked: set[str], per_season: int = 60, overall: int = 300) -> dict:
    """Rows for one competition, for the season / team filters on its page.

    Keeps each season's top players and the all-time top players with every one of their rows, so the 'all seasons'
    totals of the players shown are complete."""
    by_player: dict[str, list[tuple[tuple, dict]]] = defaultdict(list)
    for aid, c in career.items():
        for key, t in c["rows"].items():
            if key[2] == league:
                by_player[aid].append((key, t))
    total = {aid: sum(t["sot"] for _, t in rows) for aid, rows in by_player.items()}
    keep = set(sorted(total, key=lambda a: -total[a])[:overall])
    seasons = sorted({k[0] for rows in by_player.values() for k, _ in rows}, reverse=True)
    for s in seasons:
        top = sorted(((t["sot"], aid) for aid, rows in by_player.items() for k, t in rows if k[0] == s), reverse=True)
        keep |= {aid for _, aid in top[:per_season]}
    rows = [{"id": aid, "n": career[aid]["name"], "t": k[1], "s": k[0], "a": t["apps"], "m": t["minutes"],
             "sh": t["shots"], "so": t["sot"], "g": t["goals"], "as": t["assists"], "u": int(aid in linked)}
            for aid in keep for k, t in by_player[aid]]
    rows.sort(key=lambda r: (-r["so"], r["n"]))
    return {"seasons": seasons, "teams": sorted({r["t"] for r in rows}), "rows": rows}


# ------------------------------------------------------------------ match pages
def latest_predictions(forecasts: list[dict]) -> dict[tuple[str, str], dict[str, dict]]:
    """{(league, event_id): {athlete_id: forecast row}} using the newest forecast made before kickoff."""
    out: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for fc in sorted(forecasts, key=lambda f: f["asof"]):
        for p in fc.get("matchday", []):
            if p["kickoff"] > fc["asof"]:
                out[(p["league"], str(p["event_id"]))][p["athlete_id"]] = p
    return out


def match_view(rec: dict, preds: dict[str, dict] | None = None) -> dict | None:
    """Everything a match page shows: score, team totals, player lines, shots per team, predicted vs actual."""
    teams = rec.get("teams") or []
    if len(teams) != 2:
        return None
    teams = sorted(teams, key=lambda t: not t.get("home"))
    lineup = {p["athlete_id"]: p for p in rec.get("lineup") or [] if p.get("athlete_id")}
    coords = pitch_coords(rec.get("shots", []))
    totals = rec.get("boxscore_totals") or {}
    sides = []
    for t in teams:
        tt = totals.get(t["name"]) or {}
        players = []
        for p in rec.get("players") or []:
            if p["team"] != t["name"]:
                continue
            pr = (preds or {}).get(p.get("athlete_id") or "")
            players.append({"name": p["player"], "shots": p["shots"], "sot": p["shots_on_target"], "goals": p["goals"],
                            "assists": p["assists"], "minutes": (lineup.get(p.get("athlete_id")) or {}).get("minutes"),
                            "pred": pr["pred"] if pr else None, "base": pr["baseline"] if pr else None})
        players.sort(key=lambda p: (-p["sot"], -p["shots"], p["name"]))
        shots = [{"x": None if c is None else round(c[0], 1), "y": None if c is None else round(c[1], 1),
                  "o": s.get("outcome"), "b": s.get("body_part"), "s": s.get("situation"),
                  "m": None if s.get("seconds") is None else int(s["seconds"] // 60), "d": ""}
                 for s, c in zip(rec.get("shots", []), coords, strict=True) if s.get("team") == t["name"]]
        sides.append({"name": t["name"], "home": bool(t.get("home")), "score": t.get("score"),
                      "total_shots": tt.get("totalShots"), "on_target": tt.get("shotsOnTarget"),
                      "players": players, "shots": shots})
    graded = [(p["pred"], p["sot"]) for s in sides for p in s["players"] if p["pred"] is not None]
    # forecast rows for players who did not appear in the stat lines (no shots) count as an actual of 0
    seen = {p["name"] for s in sides for p in s["players"]}
    missing = [pr for aid, pr in (preds or {}).items() if pr["player"] not in seen]
    graded += [(pr["pred"], 0) for pr in missing]
    return {"league": rec["league"], "event_id": str(rec["event_id"]), "date": (rec.get("date") or "")[:10],
            "sides": sides, "missing": [{"name": pr["player"], "team": pr["team"], "pred": pr["pred"]} for pr in missing],
            "graded": {"n": len(graded), "pred": round(sum(p for p, _ in graded), 1), "actual": sum(a for _, a in graded)}
            if graded else None}
