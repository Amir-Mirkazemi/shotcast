import json

from shotcast.schemas import quarantine, scan, validate_record
from shotcast.storage import LocalStore


def rec(**over):
    base = {"league": "eng.1", "event_id": "1", "date": "2025-09-01T14:00Z", "coverage": "ok",
            "teams": [{"name": "A", "home": True, "score": "2"}, {"name": "B", "home": False, "score": "1"}],
            "players": [{"team": "A", "player": "X", "athlete_id": "1", "shots": 3, "shots_on_target": 2,
                         "goals": 1, "assists": 0, "own_goals": 0}],
            "lineup": [{"team": "A", "athlete_id": "1", "player": "X", "minutes": 90}], "shots": []}
    base.update(over)
    return base


def test_a_good_record_passes():
    assert validate_record(rec()) == []


def test_old_schema_without_lineup_or_athlete_ids_still_passes():
    old = rec()
    del old["lineup"]
    old["players"][0].pop("athlete_id")
    assert validate_record(old) == []


def test_each_contract_rule_reports_a_readable_reason():
    cases = {
        "expected 2 teams": rec(teams=[{"name": "A", "home": True}]),
        "same name": rec(teams=[{"name": "A", "home": True}, {"name": "A", "home": False}]),
        "exactly one team must be the home": rec(teams=[{"name": "A", "home": True}, {"name": "B", "home": True}]),
        "not a whole number": rec(teams=[{"name": "A", "home": True, "score": "x"}, {"name": "B", "home": False}]),
        "not ISO": rec(date="yesterday"),
        "unknown teams": rec(players=[{"team": "Z", "shots": 1, "shots_on_target": 0, "goals": 0, "assists": 0}]),
        "on target but": rec(players=[{"team": "A", "player": "X", "shots": 1, "shots_on_target": 4,
                                       "goals": 0, "assists": 0}]),
        "unknown shot outcome": rec(shots=[{"outcome": "banana"}]),
    }
    for needle, bad in cases.items():
        reasons = validate_record(bad)
        assert reasons and any(needle in r for r in reasons), (needle, reasons)


def test_negative_counts_and_impossible_minutes_fail():
    assert validate_record(rec(lineup=[{"team": "A", "minutes": 400}]))
    assert validate_record(rec(players=[{"team": "A", "shots": -1, "shots_on_target": 0, "goals": 0, "assists": 0}]))


def test_goal_with_no_listed_shot_is_tolerated():
    ok = rec(players=[{"team": "A", "player": "X", "shots": 0, "shots_on_target": 1, "goals": 1, "assists": 0}])
    assert validate_record(ok) == []


def test_scan_quarantines_bad_files_and_keeps_the_reasons(tmp_path):
    s = LocalStore(tmp_path)
    s.write_bytes("espn/matches/eng.1/1.json", json.dumps(rec()).encode())
    s.write_bytes("espn/matches/eng.1/2.json", json.dumps(rec(event_id="2", date="bad")).encode())
    res = scan(s)
    assert (res["checked"], res["valid"], res["quarantined"]) == (2, 1, 1)
    q = json.loads(s.read_bytes("espn/quarantine/eng.1/2.json"))
    assert q["source_key"] == "espn/matches/eng.1/2.json" and "ISO" in q["reasons"][0]


def test_quarantine_key_uses_league_and_file_name(tmp_path):
    s = LocalStore(tmp_path)
    assert quarantine(s, {"league": "esp.1"}, "espn/matches/esp.1/9.json", ["x"]) == "espn/quarantine/esp.1/9.json"
