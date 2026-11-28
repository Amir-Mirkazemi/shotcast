import json

import pytest

duckdb = pytest.importorskip("duckdb")

from pitchline.storage import LocalStore  # noqa: E402
from pitchline.warehouse_espn import load  # noqa: E402


def rec(eid, date="2025-09-01T14:00Z", goals=1, **over):
    r = {"league": "eng.1", "event_id": eid, "date": date, "coverage": "ok", "season": 2026, "schema": 2,
         "teams": [{"name": "A", "home": True, "score": "2"}, {"name": "B", "home": False, "score": "1"}],
         "players": [{"team": "A", "player": "X", "athlete_id": "1", "shots": 3, "shots_on_target": 2,
                      "goals": goals, "assists": 0, "own_goals": 0}],
         "lineup": [{"team": "A", "athlete_id": "1", "player": "X", "starter": True, "minutes": 90}],
         "shots": [{"play_id": "p1", "team": "A", "player": "X", "outcome": "goal", "on_target": True,
                    "x": 0.1, "y": 0.5, "seconds": 600, "body_part": "right_foot", "situation": "open_play"}]}
    r.update(over)
    return r


def put(store, r):
    store.write_bytes(f"espn/matches/{r['league']}/{r['event_id']}.json", json.dumps(r).encode())


def count(db, table):
    con = duckdb.connect(db)
    try:
        return con.execute(f"select count(*) from espn_raw.{table}").fetchone()[0]
    finally:
        con.close()


def test_first_load_fills_all_tables(tmp_path):
    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    put(s, rec("1"))
    put(s, rec("2"))
    stats = load(s, db)
    assert (stats["seen"], stats["loaded"], stats["unchanged"]) == (2, 2, 0)
    assert [count(db, t) for t in ("matches", "players", "lineups", "shots")] == [2, 2, 2, 2]


def test_second_load_reads_nothing_when_nothing_changed(tmp_path):
    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    put(s, rec("1"))
    load(s, db)
    again = load(s, db)
    assert (again["loaded"], again["unchanged"]) == (0, 1)
    assert count(db, "matches") == 1                       # and no duplicates


def test_changed_file_replaces_its_rows_and_new_file_is_added(tmp_path):
    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    put(s, rec("1", goals=1))
    load(s, db)
    put(s, rec("1", goals=2, shots=[]))                    # re-downloaded with --retry-bad / --upgrade
    put(s, rec("2"))
    stats = load(s, db)
    assert (stats["loaded"], stats["unchanged"]) == (2, 0)
    con = duckdb.connect(db)
    assert con.execute("select goals from espn_raw.players where event_id='1'").fetchall() == [(2,)]
    assert con.execute("select count(*) from espn_raw.shots where event_id='1'").fetchone()[0] == 0
    con.close()


def test_removed_file_is_removed_from_the_warehouse(tmp_path):
    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    put(s, rec("1"))
    put(s, rec("2"))
    load(s, db)
    (tmp_path / "d" / "espn/matches/eng.1/2.json").unlink()
    stats = load(s, db)
    assert stats["removed"] == 1 and count(db, "matches") == 1


def test_invalid_file_is_not_loaded_and_not_retried_until_it_changes(tmp_path):
    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    put(s, rec("1"))
    put(s, rec("2", date="not-a-date"))
    first = load(s, db)
    assert (first["loaded"], first["invalid"]) == (1, 1)
    second = load(s, db)
    assert (second["loaded"], second["invalid"], second["unchanged"]) == (0, 0, 2)
    assert count(db, "matches") == 1


def test_warehouse_totals_match_the_site_leaderboard_numbers(tmp_path):
    from pitchline.leaderboard import read_records, totals_from_records

    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    for i in range(6):
        put(s, rec(str(i), date=f"2025-09-0{i + 1}T14:00Z", goals=i % 2))
    load(s, db)
    players, _ = totals_from_records(read_records(s))
    site = {v["athlete_id"]: (v["shots"], v["shots_on_target"], v["goals"], v["minutes"]) for v in players.values()}
    con = duckdb.connect(db)
    wh = {a: (sh, sot, g, m) for a, sh, sot, g, m in con.execute(
        "select p.athlete_id, sum(p.shots), sum(p.shots_on_target), sum(p.goals), sum(l.minutes) "
        "from espn_raw.players p join espn_raw.lineups l using (league, event_id, athlete_id) group by 1").fetchall()}
    con.close()
    assert wh == site


def test_repeated_play_ids_get_a_suffix_and_a_loader_version_bump_reloads_everything(tmp_path, monkeypatch):
    from pitchline import warehouse_espn as w

    s, db = LocalStore(tmp_path / "d"), str(tmp_path / "w.duckdb")
    m = rec("1")
    m["shots"] = [m["shots"][0], dict(m["shots"][0]), dict(m["shots"][0])]
    put(s, m)
    load(s, db)
    con = duckdb.connect(db)
    assert sorted(r[0] for r in con.execute("select play_id from espn_raw.shots").fetchall()) == ["p1", "p1~1", "p1~2"]
    con.close()
    assert load(s, db)["loaded"] == 0
    monkeypatch.setattr(w, "LOADER_VERSION", w.LOADER_VERSION + 1)
    assert load(s, db)["loaded"] == 1
