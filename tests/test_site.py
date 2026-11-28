import json

import pytest

from shotcast import site
from shotcast.sitedata import pitch_coords, rank_map, slug
from shotcast.storage import LocalStore


def _put(store, league, eid, date, players, coverage="ok", lineup=None, shots=None, teams=None):
    rec = {"league": league, "event_id": eid, "date": date, "coverage": coverage, "players": players,
           "lineup": lineup or [], "shots": shots or [],
           "teams": teams or [{"name": "A", "home": True, "score": "2"}, {"name": "B", "home": False, "score": "1"}]}
    store.write_bytes(f"espn/matches/{league}/{eid}.json", json.dumps(rec).encode())


def _p(team, name, shots, sot, goals=0, assists=0, aid=""):
    return {"team": team, "athlete_id": aid, "player": name, "shots": shots, "shots_on_target": sot,
            "goals": goals, "assists": assists, "own_goals": 0}


def _shot(player, x, y, outcome="saved", body="right_foot", situation="open_play", sec=600):
    return {"player": player, "x": x, "y": y, "outcome": outcome, "body_part": body, "situation": situation,
            "seconds": sec, "on_target": outcome in ("saved", "goal")}


def test_pitch_coords_handles_both_espn_schemes_and_missing_locations():
    old = pitch_coords([_shot("a", 88.0, 40.0), _shot("a", 75.0, 60.0)])       # 0-100, goal at 100
    assert old[0] == (88.0, 40.0)
    new = pitch_coords([_shot("a", 0.12, 0.50), _shot("a", 0.30, 0.70), _shot("a", 0.0, 0.0)])  # 0-1, goal at 0
    assert new[0] == pytest.approx((88.0, 50.0)) and new[1] == pytest.approx((70.0, 70.0))
    assert new[2] is None                                                         # (0, 0) means "no location"
    assert pitch_coords([{"x": None, "y": None}]) == [None]


def test_slug_and_rank_map_ties():
    assert slug("Atlético Madrid") == "atletico-madrid" and slug("???") == "x"
    rows = [{"pid": "a", "k": 5}, {"pid": "b", "k": 5}, {"pid": "c", "k": 3}]
    assert rank_map(rows, ("k",)) == {"a": 1, "b": 1, "c": 3}


def test_build_writes_every_page_type_escapes_names_and_maps_shots(tmp_path):
    s = LocalStore(tmp_path / "data")
    star = "Erling <b>Haaland</b>"
    _put(s, "eng.1", "1", "2025-08-16T14:00Z",
         [_p("A", star, 4, 3, goals=1, aid="1"), _p("B", "José Ñ", 5, 1, aid="2")],
         lineup=[{"team": "A", "athlete_id": "1", "player": star, "minutes": 90}],
         shots=[_shot(star, 0.1, 0.5, outcome="goal"), _shot(star, 0.0, 0.0)])
    _put(s, "esp.1", "9", "2025-08-17T14:00Z", [_p("C", "Zed", 9, 9, aid="3")], coverage="mismatch",
         teams=[{"name": "C", "home": True, "score": "0"}, {"name": "D", "home": False, "score": "0"}])
    out = tmp_path / "site"
    site.build(s, out)
    names = {p.name for p in out.iterdir()}
    assert {"index.html", "method.html", "eng.1.html", "esp.1.html", "players.json", "style.css", "app.js",
            ".nojekyll", "p-eng.1-1.html", "t-eng.1-a.html", "t-eng.1-b.html", "health.html"} <= names
    health = (out / "health.html").read_text()
    assert "Data health" in health and "Premier League" in health and "No dbt run has been recorded" in health
    assert 'href="health.html"' in (out / "index.html").read_text()
    assert "Premier League" in (out / "index.html").read_text()
    league = (out / "eng.1.html").read_text()
    assert "<b>Haaland" not in league and "&lt;b&gt;Haaland" in league and "José Ñ" in league
    assert "2025-08-16T" not in league and "2025-08-16 to 2025-08-16" in league   # dates, not timestamps
    player = (out / "p-eng.1-1.html").read_text()
    assert "<svg" in player and "s-goal" in player and "1 of 2 parsed shots" in player
    assert "<b>Haaland" not in player
    data = json.loads((out / "players.json").read_text())
    star_row = next(p for p in data if p["player"] == star)
    assert star_row["url"] == "p-eng.1-1.html" and star_row["rank"] == 1 and star_row["field"] == 2
    assert "50.0%" in (out / "method.html").read_text() or "100.0%" in (out / "method.html").read_text()


def test_per90_uses_only_matches_with_known_minutes(tmp_path):
    from shotcast.leaderboard import read_records, totals_from_records
    from shotcast.sitedata import build_model
    s = LocalStore(tmp_path)
    for i in range(10):                      # ten matches with a lineup: 900 minutes, 10 shots on target
        _put(s, "eng.1", str(i), f"2025-09-{i + 1:02d}", [_p("A", "X", 2, 1, aid="1")],
             lineup=[{"team": "A", "athlete_id": "1", "player": "X", "minutes": 90}])
    _put(s, "eng.1", "99", "2025-10-01", [_p("A", "X", 30, 30, aid="1")])         # no lineup: must not inflate
    recs = read_records(s)
    players, cov = totals_from_records(recs)
    row = build_model(recs, players, cov)["leagues"]["eng.1"]["rows"][0]
    assert row["shots_on_target"] == 40 and row["minutes"] == 900
    assert row["sot90"] == pytest.approx(1.0)


def test_build_refuses_an_empty_store(tmp_path):
    with pytest.raises(SystemExit):
        site.build(LocalStore(tmp_path / "empty"), tmp_path / "site")


def test_combined_rows_merge_by_athlete_id_only():
    from shotcast.leaderboard import totals_from_records
    from shotcast.sitedata import combined_rows

    def rec(lg, eid, pl, lineup):
        return {"league": lg, "event_id": eid, "date": "2025-09-01", "coverage": "ok", "players": pl,
                "lineup": lineup, "shots": [], "teams": []}

    def mins(aid, name, m):
        return [{"team": "A", "athlete_id": aid, "player": name, "minutes": m}]
    recs = [rec("eng.1", "1", [_p("A", "Sam", 4, 2, aid="7")], mins("7", "Sam", 90)),
            rec("uefa.champions", "2", [_p("A", "Sam", 3, 3, aid="7")], mins("7", "Sam", 90)),
            rec("eng.1", "3", [_p("C", "Sam", 1, 1, aid="8")], mins("8", "Sam", 90)),   # a different Sam
            rec("eng.1", "4", [_p("B", "Sam", 9, 9)], []),     # old layout, name is ambiguous: stays separate
            ]
    players, _ = totals_from_records(recs)
    rows = combined_rows(players)
    sam = next(r for r in rows if r["athlete_id"] == "7")
    assert sam["shots_on_target"] == 5 and sam["minutes"] == 180 and set(sam["leagues"]) == {"eng.1", "uefa.champions"}
    assert sam["sot90"] is None                                                 # under the minutes rule
    assert len(rows) == 3                                                       # id 7, id 8, and the name-only row


def test_combined_row_uses_the_club_of_the_newest_match():
    from shotcast.leaderboard import totals_from_records
    from shotcast.sitedata import combined_rows

    def rec(lg, eid, date, team, aid="9"):
        return {"league": lg, "event_id": eid, "date": date, "coverage": "ok",
                "players": [_p(team, "Kylian", 4, 2, aid=aid)], "lineup": [], "shots": [], "teams": []}
    # five seasons in France, then a move to Spain: the current club is the Spanish one even though
    # France has more matches, and the page link points at the Spanish competition
    recs = [rec("fra.1", str(i), f"2023-0{i + 1}-01", "Paris SG") for i in range(5)]
    recs += [rec("esp.1", "9", "2025-09-01", "Real Madrid"), rec("esp.1", "10", "2025-09-08", "Real Madrid")]
    players, _ = totals_from_records(recs[::-1])             # order of the records must not matter
    row = combined_rows(players)[0]
    assert row["team"] == "Real Madrid" and row["best_league"] == "esp.1"
    assert row["shots_on_target"] == 14 and set(row["leagues"]) == {"fra.1", "esp.1"}


def test_build_includes_new_pages_and_links(tmp_path):
    import json

    from shotcast.site import build
    from shotcast.storage import LocalStore
    store = LocalStore(tmp_path / "s")
    for i in range(3):
        _put(store, "eng.1", str(i), f"2025-09-0{i + 1}", [_p("A", "Sam", 4, 2, aid="7")],
             lineup=[{"team": "A", "athlete_id": "7", "player": "Sam", "minutes": 90}])
    store.write_bytes("forecasts/x.json", json.dumps({
        "asof": "2025-09-05T00:00:00Z", "model": {"status": "baseline only", "reason": "needs more data"},
        "matchday": [], "season": {"eng.1": {"season": 2025, "games": 38, "players": [
            {"athlete_id": "7", "player": "Sam <b>", "team": "A", "so_far": 6, "per_match": .5, "left": 30,
             "proj": 21.0, "lo": 14, "hi": 28}]}}}).encode())
    out = tmp_path / "site"
    build(store, out)
    for name in ("best.html", "about.html", "live.html", "forecast.html", "call.html"):
        assert (out / name).exists(), name
    fc = (out / "forecast.html").read_text()
    assert "baseline only" in fc and "Sam &lt;b&gt;" in fc and "Sam <b>" not in fc
    call = (out / "call.html").read_text()
    assert "Sam \\u003cb\\u003e" in call and "Sam <b>" not in call            # JSON in the page is escaped
    home = (out / "index.html").read_text()
    assert "Built by" in home and "Amir Mirkazemi" in home and 'href="best.html"' in home


def test_matchday_data_groups_and_limits():
    from shotcast.site import matchday_data
    fc = {"matchday": [{"league": "eng.1", "event_id": "1", "player": f"P{i}", "team": "A", "pred": 1 - i / 10}
                       for i in range(5)]}
    out = matchday_data(fc, per_match=3)
    assert list(out) == ["eng.1:1"] and len(out["eng.1:1"]) == 3
    assert matchday_data(None) == {}


def test_build_skips_and_quarantines_records_that_break_the_data_contract(tmp_path):
    s = LocalStore(tmp_path / "data")
    _put(s, "eng.1", "1", "2025-08-16T14:00Z", [_p("A", "Good Player", 4, 2, aid="1")])
    _put(s, "eng.1", "2", "2025-08-17T14:00Z", [_p("A", "Bad Player", 1, 5, aid="2")])      # 5 on target, 1 shot
    out = tmp_path / "site"
    site.build(s, out)
    assert "Good Player" in (out / "players.json").read_text()
    assert "Bad Player" not in (out / "players.json").read_text()
    q = json.loads(s.read_bytes("espn/quarantine/eng.1/2.json"))
    assert "on target but 1 shots" in q["reasons"][0]


def test_every_tracked_competition_has_a_name_and_color():
    from shotcast.live import LEAGUES

    for lg in LEAGUES:
        assert lg in site.LEAGUE_NAMES and lg in site.LEAGUE_COLOR, lg
    assert {"ksa.1", "fifa.world", "uefa.euro"} <= set(LEAGUES)


def test_backfill_target_files_parse_and_cover_the_missing_competitions():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1] / "config"
    tour = yaml.safe_load((root / "backfill_tournaments.yml").read_text())["targets"]
    saudi = yaml.safe_load((root / "backfill_saudi.yml").read_text())["targets"]
    assert {(t["league"], str(t["from"])[:4]) for t in tour} >= {("fifa.world", "2026"), ("fifa.world", "2022"),
                                                                 ("fifa.world", "2018"), ("uefa.euro", "2024")}
    assert {t["league"] for t in saudi} == {"ksa.1"} and min(str(t["from"]) for t in saudi) <= "2023-01-01"
    assert all(str(t["from"]) < str(t["to"]) for t in tour + saudi)
