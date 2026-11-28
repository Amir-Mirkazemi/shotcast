"""Player leaderboards from the match files stored by the ESPN backfill.

Shots on Target uses ESPN's own per-player match stats (stored under ``players`` in each match file),
because ESPN counts some blocked shots as on target and the commentary cannot reproduce that.
Players are keyed by (league, ESPN athlete id). Match files stored before ids existed are linked to an id by
name when that name maps to exactly one id in the league, otherwise they stay keyed by name until the files
are re-downloaded with ``--upgrade``. Apps and Minutes come from stored lineups ("-" where none is stored).
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .storage import Store, get_store

PREFIX = "espn/matches/"


def _new_row(lg: str, name: str, team: str, athlete_id: str = "") -> dict:
    return {"league": lg, "player": name, "team": team, "athlete_id": athlete_id, "matches": 0,
            "shots": 0, "shots_on_target": 0, "goals": 0, "assists": 0, "apps": 0, "minutes": 0,
            "lineup_matches": 0,
            # the same stats, but only over matches where the player's minutes are known (for per-90 rates)
            "lc_shots": 0, "lc_sot": 0, "lc_goals": 0, "lc_assists": 0,
            "last_date": ""}        # newest stored match date: used to find a player's current club


def read_keyed(store: Store, league: str | None = None, workers: int = 16) -> list[tuple[str, dict]]:
    """(key, record) for every stored match file; reads in parallel because S3 is slow one at a time."""
    prefix = PREFIX + (f"{league}/" if league else "")
    keys = sorted(k for k in store.list_keys(prefix) if k.endswith(".json"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        recs = list(pool.map(lambda k: json.loads(store.read_bytes(k)), keys))
    return list(zip(keys, recs, strict=True))


def read_records(store: Store, league: str | None = None, workers: int = 16) -> list[dict]:
    """Every stored match file, oldest first."""
    recs = [r for _, r in read_keyed(store, league, workers)]
    recs.sort(key=lambda r: r.get("date") or "")          # oldest first, so "team" ends as the latest
    return recs


def read_valid(store: Store, league: str | None = None) -> tuple[list[dict], list[dict]]:
    """(records meeting the data contract, oldest first; problems). Failing files are quarantined."""
    from .schemas import split_valid

    good, bad = split_valid(store, read_keyed(store, league))
    good.sort(key=lambda r: r.get("date") or "")
    return good, bad


def load_totals(store: Store, league: str | None = None) -> tuple[dict, dict]:
    """Return (players, coverage). players[(league, athlete id or name)] -> running totals."""
    return totals_from_records(read_records(store, league))


def totals_from_records(recs: list[dict]) -> tuple[dict, dict]:
    coverage: dict[str, dict] = defaultdict(
        lambda: {"matches": 0, "ok": 0, "with_lineup": 0, "first": None, "last": None})
    # Matches stored before athlete ids existed are linked by name, but only when the name maps to one id.
    ids_by_name: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rec in recs:
        for p in rec.get("players", []) + rec.get("lineup", []):
            if p.get("athlete_id"):
                ids_by_name[(rec["league"], p["player"])].add(p["athlete_id"])

    def pid(lg: str, p: dict) -> str:
        if p.get("athlete_id"):
            return p["athlete_id"]
        ids = ids_by_name.get((lg, p["player"]), set())
        return next(iter(ids)) if len(ids) == 1 else p["player"]

    players: dict[tuple[str, str], dict] = {}
    for rec in recs:
        lg = rec["league"]
        cov = coverage[lg]
        cov["matches"] += 1
        cov["ok"] += rec.get("coverage") == "ok"
        cov["with_lineup"] += bool(rec.get("lineup"))
        d = (rec.get("date") or "")[:10]
        if d:
            cov["first"] = min(cov["first"] or d, d)
            cov["last"] = max(cov["last"] or d, d)
        in_lineup = {pid(lg, p) for p in rec.get("lineup", [])}
        for p in rec.get("players", []):
            who = pid(lg, p)
            row = players.setdefault((lg, who),
                                     _new_row(lg, p["player"], p["team"], p.get("athlete_id", "")))
            if d >= row["last_date"]:
                row["team"], row["last_date"] = p["team"], d       # newest match wins, any file order
            row["matches"] += 1
            for k in ("shots", "shots_on_target", "goals", "assists"):
                row[k] += int(p.get(k) or 0)
            if who in in_lineup:
                for k, lk in (("shots", "lc_shots"), ("shots_on_target", "lc_sot"),
                              ("goals", "lc_goals"), ("assists", "lc_assists")):
                    row[lk] += int(p.get(k) or 0)
        for p in rec.get("lineup", []):
            row = players.setdefault((lg, pid(lg, p)),
                                     _new_row(lg, p["player"], p["team"], p.get("athlete_id", "")))
            row["team"] = p["team"]
            row["apps"] += 1
            row["minutes"] += int(p.get("minutes") or 0)
            row["lineup_matches"] += 1
    return players, dict(coverage)


def top(players: dict, league: str, n: int = 10, key: str = "shots_on_target") -> list[dict]:
    rows = [r for r in players.values() if r["league"] == league]
    return sorted(rows, key=lambda r: (-r[key], -r["shots"], r["player"]))[:n]


def _rank(players: dict, row: dict, keys: tuple[str, ...]) -> tuple[int, int]:
    """(rank, field size) of one player within his league; ties share the better rank (1, 2, 2, 4)."""
    league = [r for r in players.values() if r["league"] == row["league"]]
    mine = tuple(row[k] for k in keys)
    return 1 + sum(1 for r in league if tuple(r[k] for k in keys) > mine), len(league)


def find_players(players: dict, query: str, n: int = 25) -> list[dict]:
    """Players whose name contains the query (case and accent insensitive), best by shots on target first.
    Each row gets its rank within its league by shots on target and by goals."""
    import unicodedata

    def fold(t: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))

    q = fold(query.strip())
    rows = [r for r in players.values() if q and q in fold(r["player"])]
    rows = sorted(rows, key=lambda r: (-r["shots_on_target"], -r["shots"], r["player"]))[:n]
    out = []
    for r in rows:
        sot_rank, size = _rank(players, r, ("shots_on_target", "shots"))
        goal_rank, _ = _rank(players, r, ("goals", "shots_on_target"))
        out.append({**r, "sot_rank": sot_rank, "goal_rank": goal_rank, "field": size})
    return out


def _apps(r: dict):
    return r["apps"] if r["lineup_matches"] else "-"      # "-" = no lineup data stored yet for this player


def _mins(r: dict):
    return r["minutes"] if r["lineup_matches"] else "-"


def player_markdown(rows: list[dict], query: str) -> str:
    if not rows:
        return f"No player matching '{query}' in the stored matches."
    out = [f"### Players matching '{query}'\n",
           "| Player | League | Team | Rank: on target | Rank: goals | Apps | Minutes | Shots "
           "| On target | Goals | Assists |",
           "|---|---|---|---|---|--:|--:|--:|--:|--:|--:|"]
    out += [f"| {r['player']} | {r['league']} | {r['team']} | #{r['sot_rank']} of {r['field']} | "
            f"#{r['goal_rank']} of {r['field']} | {_apps(r)} | {_mins(r)} | {r['shots']} | "
            f"{r['shots_on_target']} | {r['goals']} | {r['assists']} |" for r in rows]
    return "\n".join(out) + "\n"


def markdown(players: dict, coverage: dict, n: int = 10) -> str:
    out = []
    for lg in sorted(coverage):
        c = coverage[lg]
        out.append(f"### {lg}: top {n} by shots on target "
                   f"({c['matches']} matches stored, {c['ok']} reconcile on total shots, "
                   f"{c['with_lineup']} with minutes played)\n")
        out.append("| # | Player | Team | Apps | Minutes | Shots | On target | Goals | Assists |")
        out.append("|--:|---|---|--:|--:|--:|--:|--:|--:|")
        for i, r in enumerate(top(players, lg, n), 1):
            out.append(f"| {i} | {r['player']} | {r['team']} | {_apps(r)} | {_mins(r)} | {r['shots']} | "
                       f"{r['shots_on_target']} | {r['goals']} | {r['assists']} |")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root")
    ap.add_argument("--league")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--player", help="show every league row for players whose name contains this text")
    a = ap.parse_args(argv)
    players, coverage = load_totals(get_store(a.root), a.league)
    if a.player:
        text = player_markdown(find_players(players, a.player), a.player)
    else:
        text = markdown(players, coverage, a.top) if coverage else "No matches stored yet."
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:  # shows the tables on the workflow run page
        with open(summary, "a") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
