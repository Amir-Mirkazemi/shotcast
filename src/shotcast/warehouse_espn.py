"""Incremental load of the stored ESPN match files into DuckDB (the `espn_raw` schema).

The JSON match files are the system of record. This module copies them into four tables that dbt then
models (`dbt/models/espn`). It only touches what changed:

* every file has a fingerprint (S3 ETag, or size + mtime locally) taken from the directory listing alone;
* a manifest table remembers the fingerprint each file was loaded with;
* new or changed files are re-read, validated and swapped in (old rows for that match deleted, new rows
  inserted, in one transaction); unchanged files are never downloaded; files that disappeared are removed.

Run: python -m shotcast.warehouse_espn --root s3://bucket/shotcast --db warehouse/shotcast.duckdb
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .logs import configure
from .schemas import validate_record
from .storage import Store, get_store

PREFIX = "espn/matches/"
LOADER_VERSION = 2          # bump when the loaded rows change shape: every file is then reloaded once
log = logging.getLogger("shotcast.warehouse")

DDL = """
create schema if not exists espn_raw;
create table if not exists espn_raw.load_manifest (
    key varchar primary key, fingerprint varchar not null, status varchar not null, loaded_at timestamp not null);
create table if not exists espn_raw.matches (
    league varchar, event_id varchar, match_date date, season_year integer, home_team varchar, away_team varchar,
    home_score integer, away_score integer, coverage varchar, schema_version integer, shot_count integer,
    loaded_at timestamp);
create table if not exists espn_raw.players (
    league varchar, event_id varchar, team varchar, athlete_id varchar, player varchar,
    shots integer, shots_on_target integer, goals integer, assists integer, own_goals integer);
create table if not exists espn_raw.lineups (
    league varchar, event_id varchar, team varchar, athlete_id varchar, player varchar,
    starter boolean, minutes integer);
create table if not exists espn_raw.shots (
    league varchar, event_id varchar, play_id varchar, team varchar, player varchar, assist_player varchar,
    outcome varchar, on_target boolean, period integer, seconds double, x double, y double,
    body_part varchar, situation varchar);
create table if not exists espn_raw.load_runs (
    ts timestamp, seen integer, loaded integer, unchanged integer, removed integer, invalid integer,
    seconds double);
"""
CHILD_TABLES = ("matches", "players", "lineups", "shots")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def unique_play_ids(shots: list[dict]) -> list[str]:
    """ESPN sometimes repeats a play id inside one match (and text-derived ids can collide). The second and later
    use of an id gets a "~n" suffix, so every shot has a unique key, and dbt can still warn about the repeats."""
    seen: dict[str, int] = {}
    out = []
    for i, s in enumerate(shots):
        pid = str(s.get("play_id", i))
        n = seen.get(pid, 0)
        seen[pid] = n + 1
        out.append(pid if n == 0 else f"{pid}~{n}")
    return out


def rows_for(rec: dict, now: datetime) -> dict[str, list[tuple]]:
    """One match record -> the rows for each raw table."""
    lg, eid = rec["league"], str(rec["event_id"])
    play_ids = unique_play_ids(rec.get("shots") or [])
    home = next((t for t in rec["teams"] if t.get("home")), rec["teams"][0])
    away = next(t for t in rec["teams"] if t is not home)
    return {
        "matches": [(lg, eid, rec["date"][:10], _int(rec.get("season")), home["name"], away["name"],
                     _int(home.get("score")), _int(away.get("score")), rec.get("coverage"), _int(rec.get("schema")),
                     len(rec.get("shots") or []), now)],
        "players": [(lg, eid, p["team"], p.get("athlete_id") or None, p.get("player"), p["shots"],
                     p["shots_on_target"], p["goals"], p["assists"], p.get("own_goals", 0))
                    for p in rec.get("players") or []],
        "lineups": [(lg, eid, p["team"], p.get("athlete_id") or None, p.get("player"), p.get("starter"),
                     p.get("minutes")) for p in rec.get("lineup") or []],
        "shots": [(lg, eid, play_ids[i], s.get("team"), s.get("player"), s.get("assist_player"),
                   s["outcome"], s.get("on_target"), _int(s.get("period")), s.get("seconds"),
                   s.get("x"), s.get("y"), s.get("body_part"), s.get("situation"))
                  for i, s in enumerate(rec.get("shots") or [])],
    }


def load(store: Store, db_path: str, workers: int = 16, now: datetime | None = None) -> dict:
    import duckdb

    t0 = datetime.now(timezone.utc)
    now = now or t0
    prints = store.fingerprints(PREFIX)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = duckdb.connect(db_path)
    try:
        con.execute(DDL)
        known = dict(con.execute("select key, fingerprint from espn_raw.load_manifest").fetchall())
        tag = lambda fp: f"{fp}|v{LOADER_VERSION}"  # noqa: E731
        todo = sorted(k for k, fp in prints.items() if known.get(k) != tag(fp) and k.endswith(".json"))
        gone = sorted(set(known) - set(prints))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            recs = list(pool.map(lambda k: json.loads(store.read_bytes(k)), todo))
        stats = {"seen": len(prints), "loaded": 0, "unchanged": len(prints) - len(todo), "removed": len(gone),
                 "invalid": 0}
        con.execute("begin")
        for key in gone:
            _delete_match(con, key)
            con.execute("delete from espn_raw.load_manifest where key = ?", [key])
        for key, rec in zip(todo, recs, strict=True):
            reasons = validate_record(rec)
            if reasons:
                log.warning("not loaded, fails the data contract: %s (%s)", key, reasons[0])
                stats["invalid"] += 1
                status = "invalid"
            else:
                _delete_match(con, key)
                for table, rows in rows_for(rec, now).items():
                    if rows:
                        con.executemany(f"insert into espn_raw.{table} values ({','.join('?' * len(rows[0]))})", rows)
                stats["loaded"] += 1
                status = "loaded"
            con.execute("insert or replace into espn_raw.load_manifest values (?, ?, ?, ?)",
                        [key, tag(prints[key]), status, now])
        stats["seconds"] = round((datetime.now(timezone.utc) - t0).total_seconds(), 2)
        con.execute("insert into espn_raw.load_runs values (?, ?, ?, ?, ?, ?, ?)",
                    [now, stats["seen"], stats["loaded"], stats["unchanged"], stats["removed"], stats["invalid"],
                     stats["seconds"]])
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    finally:
        con.close()
    log.info("warehouse load: %s", stats, extra=stats)
    return stats


def _delete_match(con, key: str) -> None:
    league, name = key.split("/")[-2], key.rsplit("/", 1)[-1].removesuffix(".json")
    for table in CHILD_TABLES:
        con.execute(f"delete from espn_raw.{table} where league = ? and event_id = ?", [league, name])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="storage root (default: SHOTCAST_ROOT)")
    ap.add_argument("--db", default=os.environ.get("SHOTCAST_WAREHOUSE", "warehouse/shotcast.duckdb"))
    a = ap.parse_args(argv)
    configure()
    store = get_store(a.root)
    stats = load(store, a.db)
    store.write_bytes("health/warehouse_load.json",
                      json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **stats}).encode())
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
