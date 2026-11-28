"""Turn raw StatsBomb JSON into flat, typed Parquet (the "bronze" layer).

Raw JSON is deeply nested. We flatten only the fields the warehouse needs, with an explicit
schema, so downstream dbt models can read it with plain SQL.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.parquet as pq

from .config import Competition
from .storage import Store

log = logging.getLogger("shotcast.bronze")


def _g(d, *path):
    for p in path:
        if d is None:
            return None
        d = d.get(p) if isinstance(d, dict) else None
    return d


def _xy(loc, i):
    return float(loc[i]) if isinstance(loc, list) and len(loc) > i else None


MATCH_SCHEMA = pa.schema([
    ("match_id", pa.int64()), ("match_date", pa.string()), ("kick_off", pa.string()),
    ("competition_id", pa.int64()), ("competition_name", pa.string()),
    ("season_id", pa.int64()), ("season_name", pa.string()), ("stage", pa.string()),
    ("home_team_id", pa.int64()), ("home_team", pa.string()),
    ("away_team_id", pa.int64()), ("away_team", pa.string()),
    ("home_score", pa.int64()), ("away_score", pa.int64()),
    ("stadium", pa.string()), ("referee", pa.string()), ("match_status", pa.string()),
])

EVENT_SCHEMA = pa.schema([
    ("match_id", pa.int64()), ("event_id", pa.string()), ("event_index", pa.int64()),
    ("period", pa.int64()), ("minute", pa.int64()), ("second", pa.int64()),
    ("type", pa.string()), ("team_id", pa.int64()), ("team", pa.string()),
    ("player_id", pa.int64()), ("player", pa.string()), ("position", pa.string()),
    ("play_pattern", pa.string()), ("possession", pa.int64()),
    ("x", pa.float64()), ("y", pa.float64()), ("under_pressure", pa.bool_()),
    ("pass_recipient_id", pa.int64()), ("pass_recipient", pa.string()),
    ("pass_length", pa.float64()), ("pass_height", pa.string()),
    ("pass_end_x", pa.float64()), ("pass_end_y", pa.float64()),
    ("pass_outcome", pa.string()), ("pass_type", pa.string()),
    ("pass_goal_assist", pa.bool_()), ("pass_shot_assist", pa.bool_()),
    ("shot_xg", pa.float64()), ("shot_outcome", pa.string()), ("shot_type", pa.string()),
    ("shot_body_part", pa.string()), ("shot_key_pass_id", pa.string()),
    ("shot_end_x", pa.float64()), ("shot_end_y", pa.float64()),
    ("carry_end_x", pa.float64()), ("carry_end_y", pa.float64()),
    ("sub_replacement_id", pa.int64()), ("sub_replacement", pa.string()),
])

LINEUP_SCHEMA = pa.schema([
    ("match_id", pa.int64()), ("team_id", pa.int64()), ("team", pa.string()),
    ("player_id", pa.int64()), ("player", pa.string()),
])


def flatten_match(m: dict) -> dict:
    return {
        "match_id": m["match_id"], "match_date": m.get("match_date"), "kick_off": m.get("kick_off"),
        "competition_id": _g(m, "competition", "competition_id"),
        "competition_name": _g(m, "competition", "competition_name"),
        "season_id": _g(m, "season", "season_id"), "season_name": _g(m, "season", "season_name"),
        "stage": _g(m, "competition_stage", "name"),
        "home_team_id": _g(m, "home_team", "home_team_id"),
        "home_team": _g(m, "home_team", "home_team_name"),
        "away_team_id": _g(m, "away_team", "away_team_id"),
        "away_team": _g(m, "away_team", "away_team_name"),
        "home_score": m.get("home_score"), "away_score": m.get("away_score"),
        "stadium": _g(m, "stadium", "name"), "referee": _g(m, "referee", "name"),
        "match_status": m.get("match_status"),
    }


def flatten_event(match_id: int, e: dict) -> dict:
    p, s, c = e.get("pass") or {}, e.get("shot") or {}, e.get("carry") or {}
    sub = e.get("substitution") or {}
    loc = e.get("location")
    return {
        "match_id": match_id, "event_id": e.get("id"), "event_index": e.get("index"),
        "period": e.get("period"), "minute": e.get("minute"), "second": e.get("second"),
        "type": _g(e, "type", "name"), "team_id": _g(e, "team", "id"), "team": _g(e, "team", "name"),
        "player_id": _g(e, "player", "id"), "player": _g(e, "player", "name"),
        "position": _g(e, "position", "name"), "play_pattern": _g(e, "play_pattern", "name"),
        "possession": e.get("possession"), "x": _xy(loc, 0), "y": _xy(loc, 1),
        "under_pressure": bool(e.get("under_pressure", False)),
        "pass_recipient_id": _g(p, "recipient", "id"), "pass_recipient": _g(p, "recipient", "name"),
        "pass_length": p.get("length"), "pass_height": _g(p, "height", "name"),
        "pass_end_x": _xy(p.get("end_location"), 0), "pass_end_y": _xy(p.get("end_location"), 1),
        "pass_outcome": _g(p, "outcome", "name"), "pass_type": _g(p, "type", "name"),
        "pass_goal_assist": bool(p.get("goal_assist", False)),
        "pass_shot_assist": bool(p.get("shot_assist", False)),
        "shot_xg": s.get("statsbomb_xg"), "shot_outcome": _g(s, "outcome", "name"),
        "shot_type": _g(s, "type", "name"), "shot_body_part": _g(s, "body_part", "name"),
        "shot_key_pass_id": s.get("key_pass_id"),
        "shot_end_x": _xy(s.get("end_location"), 0), "shot_end_y": _xy(s.get("end_location"), 1),
        "carry_end_x": _xy(c.get("end_location"), 0), "carry_end_y": _xy(c.get("end_location"), 1),
        "sub_replacement_id": _g(sub, "replacement", "id"),
        "sub_replacement": _g(sub, "replacement", "name"),
    }


def extract_lineup(match_id: int, events: list[dict]) -> list[dict]:
    rows = []
    for e in events:
        if _g(e, "type", "name") != "Starting XI":
            continue
        for pl in _g(e, "tactics", "lineup") or []:
            rows.append({
                "match_id": match_id, "team_id": _g(e, "team", "id"), "team": _g(e, "team", "name"),
                "player_id": _g(pl, "player", "id"), "player": _g(pl, "player", "name"),
            })
    return rows


def _parquet_bytes(rows: list[dict], schema: pa.Schema) -> bytes:
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


@dataclass
class BronzeResult:
    matches: int = 0
    events_built: int = 0
    events_skipped: int = 0
    missing_raw: list[int] = field(default_factory=list)


def build_bronze(store: Store, competitions: tuple[Competition, ...], rebuild: bool = False) -> BronzeResult:
    result = BronzeResult()
    match_rows: dict[int, dict] = {}
    for comp in competitions:
        key = f"raw/matches/{comp.competition_id}/{comp.season_id}.json"
        if not store.exists(key):
            continue
        for m in json.loads(store.read_bytes(key)):
            match_rows[m["match_id"]] = flatten_match(m)
    if not match_rows:
        raise RuntimeError("no raw match files found; run ingest first")

    store.write_bytes("bronze/matches/matches.parquet",
                      _parquet_bytes(list(match_rows.values()), MATCH_SCHEMA))
    result.matches = len(match_rows)

    for match_id in match_rows:
        ev_key, lu_key = f"bronze/events/{match_id}.parquet", f"bronze/lineups/{match_id}.parquet"
        raw_key = f"raw/events/{match_id}.json"
        if not store.exists(raw_key):
            result.missing_raw.append(match_id)
            continue
        if not rebuild and store.exists(ev_key) and store.exists(lu_key):
            result.events_skipped += 1
            continue
        events = json.loads(store.read_bytes(raw_key))
        store.write_bytes(ev_key, _parquet_bytes([flatten_event(match_id, e) for e in events], EVENT_SCHEMA))
        store.write_bytes(lu_key, _parquet_bytes(extract_lineup(match_id, events), LINEUP_SCHEMA))
        result.events_built += 1
    return result
