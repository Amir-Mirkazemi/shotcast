from datetime import datetime, timedelta, timezone

import numpy as np

from pitchline.forecast import (
    State,
    make_forecast,
    matchday_forecast,
    replay,
    scorecard,
    season_projection,
    train_and_backtest,
)

TEAMS = [f"Club {c}" for c in "ABCDEFGH"]


def make_recs(n_matches=420, seed=1, league="eng.1"):
    rng = np.random.default_rng(seed)
    rates = {(t, i): rng.gamma(2.0, 0.12) for t in TEAMS for i in range(13)}   # true shots on target per 90
    start = datetime(2025, 8, 1)
    recs = []
    for m in range(n_matches):
        h, a = rng.choice(len(TEAMS), 2, replace=False)
        teams = [{"name": TEAMS[h], "home": True, "score": "1"}, {"name": TEAMS[a], "home": False, "score": "0"}]
        players, lineup = [], []
        for t in (TEAMS[h], TEAMS[a]):
            for i in range(13):
                mins = 90 if i < 9 else (30 if i < 11 else 0)
                if not mins:
                    continue
                aid = f"{TEAMS.index(t)}{i:02d}"
                sot = int(rng.poisson(rates[(t, i)] * mins / 90))
                lineup.append({"team": t, "athlete_id": aid, "player": f"P{aid}", "minutes": mins})
                players.append({"team": t, "athlete_id": aid, "player": f"P{aid}", "shots": sot + 1,
                                "shots_on_target": sot, "goals": 0, "assists": 0})
        recs.append({"league": league, "event_id": str(m), "date": (start + timedelta(days=m // 4)).isoformat() + "T15:00Z",
                     "season": 2025, "teams": teams, "players": players, "lineup": lineup, "shots": [],
                     "coverage": "ok"})
    return recs


def test_features_use_only_the_past():
    recs = make_recs(40)
    rows, _ = replay(recs)
    first = [r for r in rows if r["athlete_id"] == "000"][0]
    assert first["f"][4] == 0.0                      # no appearances known before the first match
    later = [r for r in rows if r["athlete_id"] == "000"][5]
    assert later["f"][4] == 5.0                      # exactly the five earlier appearances
    # changing a match's own result must not change that match's own features
    recs2 = make_recs(40)
    recs2[10]["players"][0]["shots_on_target"] += 7
    rows2, _ = replay(recs2)
    ev = recs[10]["event_id"]
    a = next(r for r in rows if r["event_id"] == ev and r["athlete_id"] == recs[10]["players"][0]["athlete_id"])
    b = next(r for r in rows2 if r["event_id"] == ev and r["athlete_id"] == a["athlete_id"])
    assert a["f"] == b["f"] and a["y"] != b["y"]


def test_backtest_card_is_honest_and_time_ordered():
    rows, _ = replay(make_recs())
    model, card = train_and_backtest(rows)
    assert card["train_until"] <= card["test_from"]
    assert {"baseline", "poisson", "boosted", "league average"} <= set(card["results"])
    assert (model is not None) == card["beats_baseline"]
    assert card["calibration"]
    _, small = train_and_backtest(rows[:100])
    assert small["status"] == "baseline only" and "needs at least" in small["reason"]


def test_matchday_forecast_and_scorecard_round_trip():
    recs = make_recs()
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    kick = (now + timedelta(days=2)).strftime("%Y-%m-%dT15:00Z")

    def fetch(url):
        ev = {"id": 9001, "date": kick, "status": {"type": {"state": "pre"}},
              "competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": TEAMS[0]}},
                                                {"homeAway": "away", "team": {"displayName": TEAMS[1]}}]}]}
        return ({"events": [ev]} if "eng.1" in url else {"events": []}), {}
    fc = make_forecast(recs, fetch, now, sleep=lambda s: None)
    assert fc["matchday"] and all(p["event_id"] == "9001" for p in fc["matchday"])
    assert {p["team"] for p in fc["matchday"]} == {TEAMS[0], TEAMS[1]}
    assert fc["season"]["eng.1"]["players"]
    # the match is played; grade the forecast
    played = {"league": "eng.1", "event_id": "9001", "date": kick, "season": 2025,
              "teams": [{"name": TEAMS[0], "home": True, "score": "1"}, {"name": TEAMS[1], "home": False, "score": "1"}],
              "lineup": [{"team": p["team"], "athlete_id": p["athlete_id"], "player": p["player"], "minutes": 90}
                         for p in fc["matchday"]],
              "players": [{"team": p["team"], "athlete_id": p["athlete_id"], "player": p["player"],
                           "shots": 1, "shots_on_target": 1} for p in fc["matchday"]]}
    sc = scorecard([fc], recs + [played])
    assert sc["n"] == len(fc["matchday"]) and sc["actual_total"] == sc["n"] and sc["model"]["mae"] >= 0
    late = dict(fc, asof=(now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"))     # made after kickoff
    assert scorecard([late], recs + [played])["n"] == 0


def test_season_projection_counts_remaining_matches():
    recs = make_recs()
    _, st = replay(recs)
    proj = season_projection(recs, st, None)["eng.1"]
    top = proj["players"][0]
    assert top["proj"] >= top["so_far"] and top["lo"] <= top["proj"] <= top["hi"] + 1
    assert proj["players"] == sorted(proj["players"], key=lambda r: -r["proj"])


def test_unknown_team_and_empty_state_do_not_crash():
    assert matchday_forecast(State(), None, [{"league": "x", "event_id": "1", "date": "z", "home": "Q", "away": "R"}]) == []
