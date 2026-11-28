"""xG tested on simulated shots whose true goal probability is known."""
from datetime import datetime, timedelta

import numpy as np
import pytest

from shotcast import xg


def truth(dist, ang, header, pen):
    z = 1.6 - 0.16 * dist + 0.9 * ang - 1.1 * header
    p = 1 / (1 + np.exp(-z))
    return np.where(pen, 0.76, p)


def sim_recs(n_matches=500, per_match=30, seed=3):
    """Matches full of shots; goals drawn from the true probability. Coordinates use the 0-100 grid, goal at 100."""
    rng = np.random.default_rng(seed)
    start, recs = datetime(2025, 8, 1), []
    for m in range(n_matches):
        shots = []
        for _ in range(per_match):
            x, y = rng.uniform(72, 99.5), rng.uniform(15, 85)
            header, pen = rng.random() < 0.15, rng.random() < 0.03
            if pen:
                x, y = 88.5, 50.0
            d, a = xg.geometry([x], [y])
            p = float(truth(d, a, header, pen)[0])
            shots.append({"team": "A", "player": "Ann", "assist_player": "Bea" if rng.random() < 0.3 else None,
                          "x": x, "y": y, "outcome": "goal" if rng.random() < p else "saved",
                          "body_part": "header" if header else "right_foot",
                          "situation": "penalty" if pen else "open_play", "_p": p})
        recs.append({"league": "eng.1", "event_id": str(m), "date": (start + timedelta(days=m // 3)).isoformat() + "T15:00Z",
                     "players": [{"team": "A", "player": "Ann", "athlete_id": "1"}],
                     "lineup": [{"team": "A", "player": "Ann", "athlete_id": "1", "minutes": 90},
                                {"team": "A", "player": "Bea", "athlete_id": "2", "minutes": 70}],
                     "shots": shots})
    return recs


@pytest.fixture(scope="module")
def scored():
    recs = sim_recs()
    card, shots = xg.score_shots(recs)
    return recs, card, shots


def test_geometry_is_sensible():
    d, a = xg.geometry([100, 90, 100], [50, 50, 20])
    assert d[0] == pytest.approx(0, abs=1e-6) or d[0] < 0.01
    assert d[1] == pytest.approx(10.5) and d[2] == pytest.approx(0.3 * 68)      # y offset 30 of 100 = 20.4 m
    _, central = xg.geometry([94], [50])
    _, wide = xg.geometry([94], [10])
    assert central[0] > wide[0]                                                  # the goal looks bigger head-on


def test_collect_resolves_players_inside_the_match_and_skips_what_does_not_count():
    rec = {"league": "x", "event_id": "1", "date": "2025-09-01T10:00Z",
           "players": [{"team": "A", "player": "Ann", "athlete_id": "1"}, {"team": "A", "player": "Twin", "athlete_id": "5"},
                       {"team": "A", "player": "Twin", "athlete_id": "6"}],
           "lineup": [{"team": "A", "player": "Ann", "athlete_id": "1", "minutes": 90},
                      {"team": "A", "player": "Bea", "athlete_id": "2", "minutes": 0}],
           "shots": [{"team": "A", "player": "Ann", "assist_player": "Bea", "x": 90, "y": 50, "outcome": "goal"},
                     {"team": "A", "player": "Ann", "x": 0, "y": 0, "outcome": "saved"},                  # no location
                     {"team": "A", "player": "Ann", "x": 91, "y": 50, "outcome": "own_goal"},
                     {"team": "A", "player": "Twin", "x": 91, "y": 50, "outcome": "saved"},
                     {"team": "A", "player": "Nobody", "x": 91, "y": 50, "outcome": "saved"}]}
    out = xg.collect([rec])
    assert [s["outcome"] if "outcome" in s else s["goal"] for s in out] == [True, False, False, False]    # own goal gone
    first, nolocation, twin, nobody = out
    assert first["aid"] == "1" and first["assist_aid"] == "2" and first["lc"] and not first["assist_lc"]
    assert nolocation["x"] is None
    assert twin["aid"] is None and nobody["aid"] is None                                                # never guessed


def test_the_validation_card_is_walk_forward_and_calibrated(scored):
    _, card, _ = scored
    assert card["n_folds"] >= 2 and card["test_from"] <= card["test_to"]
    r = card["results"]
    assert r["logistic"]["logloss"] < r["distance"]["logloss"] < r["constant"]["logloss"]   # body part and angle matter here
    assert r["boosted"]["auc"] > 0.7
    assert card["used"] in ("logistic", "boosted") and card["beats_distance"] and card["gain"]["lo"] > 0
    for row in card["calibration"]:
        assert abs(row["predicted"] - row["actual"]) < 0.03, card["calibration"]
    assert abs(r[card["used"]]["predicted"] - r[card["used"]]["actual"]) < 0.04 * r[card["used"]]["actual"] + 5


def test_cross_fitted_xg_is_close_to_the_known_truth(scored):
    recs, _, shots = scored
    tru = np.array([s["_p"] for rec in recs for s in rec["shots"]])              # same order as collect()
    est = np.array([s["xg"] for s in shots])
    assert len(est) == len(tru)
    assert np.mean(np.abs(est - tru)) < 0.03                                     # shot by shot, out of sample
    assert abs(est.sum() - tru.sum()) / tru.sum() < 0.03                         # and in total
    assert np.corrcoef(est, tru)[0, 1] > 0.95
    pens = [s["xg"] for s in shots if s["sit"] == "penalty"]
    assert pens and np.mean(pens) == pytest.approx(0.76, abs=0.08)               # the model learns penalties
    assert all("dist" in s for s in shots)


def test_a_fuller_model_that_does_not_help_is_not_used(monkeypatch):
    recs = sim_recs(300)

    class Same:                                                                  # as good as nothing: constant
        def predict_proba(self, X):
            return np.column_stack([np.full(len(X), 0.9), np.full(len(X), 0.1)])

    real_fit = xg.fit
    monkeypatch.setattr(xg, "fit", lambda kind, X, y: real_fit("distance", X, y) if kind == "distance" else Same())
    kind, card = xg.validate(xg.collect(recs))
    assert kind == "distance" and card["beats_distance"] is False and card["used"] == "distance"


def test_too_few_shots_means_no_xg_and_a_reason():
    card, shots = xg.score_shots(sim_recs(20))
    assert shots == [] and card["status"] == "not enough shots" and "needs at least" in card["reason"]
