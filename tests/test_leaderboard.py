import json

from pitchline import leaderboard as lb
from pitchline.storage import LocalStore


def _put(store, league, eid, date, players, coverage="ok"):
    rec = {"league": league, "event_id": eid, "date": date, "coverage": coverage, "players": players}
    store.write_bytes(f"espn/matches/{league}/{eid}.json", json.dumps(rec).encode())


def _p(team, name, shots, sot, goals=0, assists=0):
    return {"team": team, "player": name, "shots": shots, "shots_on_target": sot,
            "goals": goals, "assists": assists, "own_goals": 0}


def test_totals_accumulate_across_matches_and_rank_by_shots_on_target(tmp_path):
    s = LocalStore(tmp_path)
    _put(s, "eng.1", "1", "2025-08-16", [_p("A", "X", 4, 3, goals=1), _p("B", "Y", 5, 1)])
    _put(s, "eng.1", "2", "2025-08-23", [_p("A", "X", 2, 2, assists=1)], coverage="mismatch")
    players, cov = lb.load_totals(s)
    x = players[("eng.1", "X")]
    assert (x["matches"], x["shots"], x["shots_on_target"], x["goals"], x["assists"]) == (2, 6, 5, 1, 1)
    assert [r["player"] for r in lb.top(players, "eng.1")] == ["X", "Y"]
    assert cov["eng.1"] == {"matches": 2, "ok": 1, "with_lineup": 0, "first": "2025-08-16", "last": "2025-08-23"}


def test_ties_break_on_shots_then_name_and_leagues_stay_separate(tmp_path):
    s = LocalStore(tmp_path)
    _put(s, "eng.1", "1", "2025-08-16", [_p("A", "Zed", 3, 2), _p("A", "Amy", 3, 2), _p("A", "Bob", 4, 2)])
    _put(s, "esp.1", "9", "2025-08-16", [_p("C", "Zed", 9, 9)])
    players, _ = lb.load_totals(s)
    assert [r["player"] for r in lb.top(players, "eng.1")] == ["Bob", "Amy", "Zed"]
    assert [r["player"] for r in lb.top(players, "esp.1")] == ["Zed"]


def test_team_is_the_latest_one_and_markdown_renders(tmp_path):
    s = LocalStore(tmp_path)
    _put(s, "eng.1", "2", "2025-09-01", [_p("New FC", "X", 1, 1)])
    _put(s, "eng.1", "1", "2025-08-01", [_p("Old FC", "X", 1, 0)])
    players, cov = lb.load_totals(s, "eng.1")
    assert players[("eng.1", "X")]["team"] == "New FC"
    md = lb.markdown(players, cov, 5)
    assert "| 1 | X | New FC | - | - | 2 | 1 | 0 | 0 |" in md and "2 matches stored" in md


def test_player_search_ignores_case_and_accents_and_spans_leagues(tmp_path):
    s = LocalStore(tmp_path)
    _put(s, "eng.1", "1", "2025-08-16", [_p("City", "Erling Haaland", 5, 3), _p("A", "Other", 1, 1)])
    _put(s, "uefa.champions", "2", "2025-09-16", [_p("City", "Erling Haaland", 3, 3)])
    _put(s, "esp.1", "3", "2025-09-20", [_p("Real", "Kylian Mbappé", 4, 2)])
    players, _ = lb.load_totals(s)
    rows = lb.find_players(players, "haaland")
    assert [(r["league"], r["shots_on_target"]) for r in rows] == [("uefa.champions", 3), ("eng.1", 3)] or \
           [(r["league"], r["shots_on_target"]) for r in rows] == [("eng.1", 3), ("uefa.champions", 3)]
    assert len(rows) == 2
    assert [r["player"] for r in lb.find_players(players, "MBAPPE")] == ["Kylian Mbappé"]
    assert "No player matching 'zzz'" in lb.player_markdown(lb.find_players(players, "zzz"), "zzz")


def test_player_lookup_shows_rank_within_each_league_with_shared_ties(tmp_path):
    s = LocalStore(tmp_path)
    _put(s, "eng.1", "1", "2025-08-16", [_p("A", "Star", 9, 6, goals=4), _p("A", "Twin", 5, 3, goals=1),
                                          _p("B", "Twin2", 5, 3, goals=1), _p("B", "Last", 1, 0)])
    _put(s, "esp.1", "2", "2025-08-16", [_p("C", "Star", 2, 1)])
    players, _ = lb.load_totals(s)
    star = {r["league"]: r for r in lb.find_players(players, "star")}
    assert (star["eng.1"]["sot_rank"], star["eng.1"]["field"], star["eng.1"]["goal_rank"]) == (1, 4, 1)
    assert (star["esp.1"]["sot_rank"], star["esp.1"]["field"]) == (1, 1)
    last = lb.find_players(players, "last")[0]
    assert last["sot_rank"] == 4                                    # two tied at #2, so the next is #4
    assert "#1 of 4" in lb.player_markdown(lb.find_players(players, "star", 5), "star")


def _lineup_row(team, name, minutes, aid="", starter=True):
    return {"team": team, "athlete_id": aid, "player": name, "starter": starter, "minutes": minutes}


def test_athlete_id_separates_same_name_players_and_links_old_files(tmp_path):
    s = LocalStore(tmp_path)
    rec1 = {"league": "eng.1", "event_id": "1", "date": "2025-08-01", "coverage": "ok", "schema": 2,
            "players": [{**_p("A", "Sam Smith", 2, 1), "athlete_id": "11"},
                        {**_p("B", "Sam Smith", 4, 2), "athlete_id": "22"}],
            "lineup": [_lineup_row("A", "Sam Smith", 90, "11"), _lineup_row("B", "Sam Smith", 60, "22")]}
    s.write_bytes("espn/matches/eng.1/1.json", json.dumps(rec1).encode())
    _put(s, "eng.1", "0", "2025-07-01", [_p("A", "Zed Old", 1, 1)])           # old file layout, no id
    rec2 = {"league": "eng.1", "event_id": "2", "date": "2025-08-08", "coverage": "ok", "schema": 2,
            "players": [{**_p("A", "Zed Old", 3, 2), "athlete_id": "33"}], "lineup": []}
    s.write_bytes("espn/matches/eng.1/2.json", json.dumps(rec2).encode())
    players, cov = lb.load_totals(s)
    assert players[("eng.1", "11")]["shots"] == 2 and players[("eng.1", "22")]["shots"] == 4   # not merged
    assert players[("eng.1", "11")]["minutes"] == 90 and players[("eng.1", "22")]["apps"] == 1
    assert players[("eng.1", "33")]["shots"] == 4                                              # old file linked by name
    assert cov["eng.1"]["with_lineup"] == 1
