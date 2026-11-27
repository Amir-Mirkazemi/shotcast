import pytest

from pitchline import insights as ins


def _rec(eid, date, lg="eng.1", sots=None, lineup=True, team="A", opp="B"):
    sots = sots or {}
    players = [{"team": team, "athlete_id": a, "player": n, "shots": s + 1, "shots_on_target": s, "goals": 0,
                "assists": 0, "own_goals": 0} for (a, n), s in sots.items() if s]
    lu = [{"team": team, "athlete_id": a, "player": n, "minutes": 90} for (a, n) in sots] if lineup else []
    return {"league": lg, "event_id": eid, "date": date, "coverage": "ok", "players": players, "lineup": lu,
            "shots": [], "teams": [{"name": team, "home": True, "score": "1"}, {"name": opp, "home": False, "score": "0"}]}


def test_season_of_switches_in_july():
    assert ins.season_of("2025-09-14") == "2025-26" and ins.season_of("2026-06-30") == "2025-26"
    assert ins.season_of("2026-07-01") == "2026-27" and ins.season_of("") == "unknown"


def test_rolling_needs_a_full_window():
    assert ins.rolling([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    assert ins.rolling([1, 2], 5) == [None, None]


def test_series_counts_zero_shot_appearances_and_sorts_by_date():
    sam = ("7", "Sam")
    recs = [_rec("3", "2025-09-20", sots={sam: 3}), _rec("1", "2025-09-01", sots={sam: 0}),
            _rec("2", "2025-09-10", sots={sam: 1})]
    series, career = ins.player_history(recs)
    assert [m["sot"] for m in series["7"]] == [0, 1, 3]                  # the 0-shot match is a real appearance
    assert [m["date"] for m in series["7"]] == ["2025-09-01", "2025-09-10", "2025-09-20"]
    assert series["7"][0]["opp"] == "B"
    assert career["7"]["rows"][("2025-26", "A", "eng.1")]["apps"] == 3
    assert ins.form_chart(series["7"]) is None                           # fewer than 5 matches: no chart


def test_form_chart_has_rolling_series_and_matches_without_lineup_are_skipped():
    sam = ("7", "Sam")
    recs = [_rec(str(i), f"2025-09-{i + 1:02d}", sots={sam: i % 3}) for i in range(12)]
    recs.append(_rec("99", "2025-10-30", sots={sam: 5}, lineup=False))   # unknown lineup: zeros are unknown
    series, career = ins.player_history(recs)
    chart = ins.form_chart(series["7"])
    assert len(chart["sot"]) == 12 and chart["r5"][:4] == [None] * 4 and chart["r10"][8] is None
    assert chart["r10"][9] == pytest.approx(sum(chart["sot"][:10]) / 10, abs=1e-3)
    assert career["7"]["rows"][("2025-26", "A", "eng.1")]["sot"] == sum(i % 3 for i in range(12)) + 5


def test_career_table_and_club_stints_follow_a_move():
    sam = ("7", "Sam")
    recs = [_rec("1", "2023-09-01", sots={sam: 2}, team="Paris"), _rec("2", "2025-09-01", sots={sam: 4}, team="Madrid", lg="esp.1")]
    _, career = ins.player_history(recs)
    t = ins.career_table(career["7"])
    assert [(r["season"], r["team"]) for r in t] == [("2025-26", "Madrid"), ("2023-24", "Paris")]
    assert ins.club_stints(career["7"]) == [{"team": "Paris", "from": "2023-24", "to": "2023-24"},
                                           {"team": "Madrid", "from": "2025-26", "to": "2025-26"}]


def test_season_rows_keep_complete_rows_for_listed_players():
    recs = []
    for i in range(6):
        recs.append(_rec(str(i), f"2023-10-{i + 1:02d}", sots={("1", "Old"): 2, ("2", "New"): 0}))
        recs.append(_rec(f"n{i}", f"2025-10-{i + 1:02d}", sots={("1", "Old"): 1, ("2", "New"): 3}))
    _, career = ins.player_history(recs)
    out = ins.season_rows(career, "eng.1", linked={"1"}, per_season=1, overall=1)
    assert out["seasons"] == ["2025-26", "2023-24"] and out["teams"] == ["A"]
    ids = {r["id"] for r in out["rows"]}
    assert ids == {"1", "2"}                      # Old is top overall, New tops 2025-26
    old = [r for r in out["rows"] if r["id"] == "1"]
    assert sum(r["so"] for r in old) == 18 and {r["u"] for r in old} == {1}
    assert out["rows"][0]["so"] >= out["rows"][-1]["so"]


def test_match_view_grades_predictions_and_counts_missing_as_zero():
    rec = _rec("5", "2025-09-20", sots={("7", "Sam"): 2, ("8", "Kit"): 0})
    rec["boxscore_totals"] = {"A": {"totalShots": "9", "shotsOnTarget": "4"}, "B": {}}
    preds = {"7": {"player": "Sam", "team": "A", "pred": 1.5, "baseline": 1.0, "athlete_id": "7"},
             "9": {"player": "Zed", "team": "A", "pred": 0.5, "baseline": 0.4, "athlete_id": "9"}}
    v = ins.match_view(rec, preds)
    home = v["sides"][0]
    assert home["name"] == "A" and home["total_shots"] == "9" and home["players"][0]["pred"] == 1.5
    assert v["graded"] == {"n": 2, "pred": 2.0, "actual": 2} and v["missing"][0]["name"] == "Zed"
    assert ins.match_view({"teams": [], "league": "x", "event_id": "1"}) is None
    assert ins.match_view(rec)["graded"] is None


def test_latest_predictions_ignore_forecasts_made_after_kickoff():
    fc = [{"asof": "2025-09-01T00:00Z", "matchday": [{"league": "eng.1", "event_id": "5", "athlete_id": "7",
                                                      "kickoff": "2025-09-02T00:00Z", "pred": 1.0}]},
          {"asof": "2025-09-03T00:00Z", "matchday": [{"league": "eng.1", "event_id": "5", "athlete_id": "7",
                                                      "kickoff": "2025-09-02T00:00Z", "pred": 9.0}]}]
    assert ins.latest_predictions(fc)[("eng.1", "5")]["7"]["pred"] == 1.0
