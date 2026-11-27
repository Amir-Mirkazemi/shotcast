"""Turns stored match records into the numbers and rows the website shows (no HTML in here)."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

MIN_MINUTES_PER90 = 900          # per-90 rates need at least this many minutes (ten full matches)
# leaders on each board get their own page
FEATURED = {"shots_on_target": 100, "goals": 50, "assists": 50, "sot90": 50}


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-") or "x"


def pitch_coords(shots: list[dict]) -> list[tuple[float, float] | None]:
    """One (x, y) per shot on a 0-100 grid with the attacking goal at x=100, or None when unknown.

    ESPN uses two coordinate schemes: 0-100 with the goal at 100 (older feeds) and 0-1 with the goal at 0
    (newer feeds). The scheme is detected per match: when every located shot lies within 0-1 it is the second.
    A shot at exactly (0, 0) is ESPN's way of saying "no location"."""
    def located(s):
        x, y = s.get("x"), s.get("y")
        return x is not None and y is not None and not (x == 0 and y == 0)

    pts = [(s["x"], s["y"]) for s in shots if located(s)]
    unit = bool(pts) and max(max(x for x, _ in pts), max(y for _, y in pts)) <= 1.0
    out: list[tuple[float, float] | None] = []
    for s in shots:
        if not located(s):
            out.append(None)
            continue
        x, y = (((1 - s["x"]) * 100, s["y"] * 100) if unit else (s["x"], s["y"]))
        out.append((min(max(x, 0.0), 100.0), min(max(y, 0.0), 100.0)))
    return out


def rank_map(rows: list[dict], keys: tuple[str, ...]) -> dict[str, int]:
    """pid -> rank by the given keys, best first; ties share the better rank (1, 2, 2, 4)."""
    ordered = sorted(rows, key=lambda r: tuple(-r[k] for k in keys))
    out: dict[str, int] = {}
    prev, rank = None, 0
    for i, r in enumerate(ordered, 1):
        cur = tuple(r[k] for k in keys)
        if cur != prev:
            rank, prev = i, cur
        out[r["pid"]] = rank
    return out


def _per90(row: dict, key: str) -> float | None:
    if row["lineup_matches"] and row["minutes"] >= MIN_MINUTES_PER90:
        return row[key] * 90 / row["minutes"]
    return None


def build_model(recs: list[dict], players: dict, coverage: dict) -> dict:
    leagues = {lg: {"cov": cov, "rows": [], "teams": {}, "recent": []} for lg, cov in coverage.items()}
    name_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (lg, pid), row in players.items():
        row["pid"] = pid
        row["sot90"] = _per90(row, "lc_sot")
        row["goals90"] = _per90(row, "lc_goals")
        leagues[lg]["rows"].append(row)
        name_index[(lg, row["player"])].append(pid)

    shots_by_pid: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in recs:
        lg = rec["league"]
        for s, c in zip(rec.get("shots", []), pitch_coords(rec.get("shots", [])), strict=True):
            ids = name_index.get((lg, s.get("player") or ""), [])
            if len(ids) != 1:                                   # unknown or ambiguous name: do not guess
                continue
            sec = s.get("seconds")
            shots_by_pid[(lg, ids[0])].append({
                "x": None if c is None else round(c[0], 1), "y": None if c is None else round(c[1], 1),
                "o": s.get("outcome"), "b": s.get("body_part"), "s": s.get("situation"),
                "m": None if sec is None else int(sec // 60), "d": (rec.get("date") or "")[:10]})
        teams = rec.get("teams") or []
        try:
            scores = [int(t["score"]) for t in teams]
        except (KeyError, TypeError, ValueError):
            continue
        if len(teams) != 2:
            continue
        (a, b), (sa, sb) = teams, scores
        home = next((t["name"] for t in teams if t.get("home")), a["name"])
        leagues[lg]["recent"].append({"league": lg, "event_id": str(rec["event_id"]),
                                      "date": (rec.get("date") or "")[:10], "home": home,
                                      "teams": teams, "scores": scores})
        for me, gf, ga in ((a, sa, sb), (b, sb, sa)):
            t = leagues[lg]["teams"].setdefault(me["name"], {"name": me["name"], "p": 0, "w": 0, "d": 0,
                                                             "l": 0, "gf": 0, "ga": 0})
            t["p"] += 1
            t["gf"] += gf
            t["ga"] += ga
            t["w" if gf > ga else "l" if gf < ga else "d"] += 1
    for lg in leagues.values():
        lg["recent"].sort(key=lambda m: m["date"], reverse=True)
        lg["rows"].sort(key=lambda r: (-r["shots_on_target"], -r["shots"], r["player"]))
    return {"leagues": leagues, "shots": shots_by_pid}


def featured_ids(rows: list[dict]) -> set[str]:
    """pids of the players that get a page: league leaders on each board."""
    keep: set[str] = set()
    for key, n in FEATURED.items():
        pool = [r for r in rows if r.get(key) is not None]
        pool.sort(key=lambda r: (-r[key], -r["shots"], r["player"]))
        keep |= {r["pid"] for r in pool[:n] if r[key] > 0}
    return keep


MIN_MINUTES_BEST = 1800          # the all-competitions per-90 board needs 20 full matches of known minutes
_SUMS = ("shots", "shots_on_target", "goals", "assists", "apps", "minutes", "lineup_matches", "matches",
         "lc_shots", "lc_sot", "lc_goals", "lc_assists")


def combined_rows(players: dict) -> list[dict]:
    """One row per player across every competition (domestic league, continental cups, World Cup).

    Rows are merged by ESPN athlete id. A row without an id is never merged, because two different
    players can share a name. The team is the club of the player's newest stored match, so a player who
    moved clubs shows the current one; the page link goes to the competition where he played the most
    matches for that club."""
    groups: dict[str, list[tuple[str, str, dict]]] = {}
    for (lg, pid), r in players.items():
        groups.setdefault(r["athlete_id"] or f"{lg}:{pid}", []).append((lg, pid, r))
    merged: dict[str, dict] = {}
    for key, items in groups.items():
        newest = max(items, key=lambda t: (t[2].get("last_date", ""), t[2]["matches"]))
        team = newest[2]["team"]
        lg, pid, top = max((t for t in items if t[2]["team"] == team), key=lambda t: t[2]["matches"])
        m = {k: 0 for k in _SUMS} | {"player": top["player"], "team": team, "athlete_id": top["athlete_id"],
                                     "leagues": {}, "best_league": lg, "best_pid": pid}
        for lg_, _, r in items:
            for k in _SUMS:
                m[k] += r[k]
            m["leagues"][lg_] = m["leagues"].get(lg_, 0) + r["shots_on_target"]
        merged[key] = m
    rows = list(merged.values())
    for m in rows:
        ok = m["lineup_matches"] and m["minutes"] >= MIN_MINUTES_BEST
        m["sot90"] = m["lc_sot"] * 90 / m["minutes"] if ok else None
        m["pid"] = m["athlete_id"] or m["best_pid"]
    return rows
