import io
import json

import pyarrow.parquet as pq
from conftest import make_events, make_match

from shotcast.bronze import build_bronze, extract_lineup, flatten_event, flatten_match
from shotcast.config import Competition
from shotcast.storage import LocalStore

COMP = (Competition(99, 1, "Test Cup"),)


def test_flatten_match():
    m = flatten_match(make_match(7, hs=2, as_=1))
    assert m["match_id"] == 7 and m["home_team"] == "Aland" and m["stage"] == "Final"
    assert (m["home_score"], m["away_score"]) == (2, 1)


def test_flatten_pass_and_shot():
    ev = make_events()
    p = flatten_event(1, ev[1])
    assert p["type"] == "Pass" and p["pass_recipient"] == "Bea" and p["pass_shot_assist"] is True
    assert (p["x"], p["pass_end_x"]) == (60.0, 80.0) and p["pass_outcome"] is None  # None = completed
    s = flatten_event(1, ev[2])
    assert s["shot_xg"] == 0.31 and s["shot_key_pass_id"] == "e1" and s["shot_end_x"] == 120.0


def test_flatten_substitution_and_missing_fields():
    sub = flatten_event(1, make_events()[3])
    assert sub["sub_replacement"] == "Cal" and sub["pass_length"] is None


def test_extract_lineup():
    rows = extract_lineup(1, make_events())
    assert [r["player"] for r in rows] == ["Ann", "Bea"] and rows[0]["team"] == "Aland"


def seed(store):
    store.write_bytes("raw/matches/99/1.json", json.dumps([make_match(1)]).encode())
    store.write_bytes("raw/events/1.json", json.dumps(make_events()).encode())


def test_build_bronze_writes_readable_parquet(tmp_path):
    store = LocalStore(tmp_path)
    seed(store)
    res = build_bronze(store, COMP)
    assert res.matches == 1 and res.events_built == 1
    t = pq.read_table(io.BytesIO(store.read_bytes("bronze/events/1.parquet")))
    assert t.num_rows == 4 and "shot_xg" in t.column_names
    assert pq.read_table(io.BytesIO(store.read_bytes("bronze/lineups/1.parquet"))).num_rows == 2


def test_build_bronze_is_incremental(tmp_path):
    store = LocalStore(tmp_path)
    seed(store)
    build_bronze(store, COMP)
    again = build_bronze(store, COMP)
    assert again.events_built == 0 and again.events_skipped == 1
    assert build_bronze(store, COMP, rebuild=True).events_built == 1


def test_missing_raw_events_are_reported(tmp_path):
    store = LocalStore(tmp_path)
    store.write_bytes("raw/matches/99/1.json", json.dumps([make_match(1), make_match(2)]).encode())
    store.write_bytes("raw/events/1.json", json.dumps(make_events()).encode())
    assert build_bronze(store, COMP).missing_raw == [2]
