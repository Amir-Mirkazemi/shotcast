"""The forecast maths, tested on simulated data where the true answer is known."""
from datetime import datetime, timedelta

import numpy as np
import pytest

from pitchline import forecast as fc
from pitchline import mlcore as ml

TEAMS = [f"Club {c}" for c in "ABCDEFGH"]


def sim_recs(alpha_true=0.4, n_matches=700, seed=7):
    """Matches where each player's shots on target are negative binomial around a fixed true rate."""
    rng = np.random.default_rng(seed)
    rates = {(t, i): rng.gamma(2.0, 0.12) for t in TEAMS for i in range(13)}
    start, recs = datetime(2025, 8, 1), []
    for m in range(n_matches):
        h, a = rng.choice(len(TEAMS), 2, replace=False)
        players, lineup = [], []
        for t in (TEAMS[h], TEAMS[a]):
            for i in range(11):
                mins = 90 if i < 9 else 30
                aid = f"{TEAMS.index(t)}{i:02d}"
                mean = rates[(t, i)] * mins / 90
                if alpha_true:
                    r = 1 / alpha_true
                    sot = int(rng.negative_binomial(r, r / (r + mean)))
                else:
                    sot = int(rng.poisson(mean))
                lineup.append({"team": t, "athlete_id": aid, "player": f"P{aid}", "minutes": mins, "starter": i < 9})
                players.append({"team": t, "athlete_id": aid, "player": f"P{aid}", "shots": sot + 1,
                                "shots_on_target": sot, "goals": 0, "assists": 0})
        recs.append({"league": "eng.1", "event_id": str(m), "season": 2025, "coverage": "ok",
                     "date": (start + timedelta(days=m // 4)).isoformat() + "T15:00Z",
                     "teams": [{"name": TEAMS[h], "home": True, "score": "1"},
                               {"name": TEAMS[a], "home": False, "score": "0"}],
                     "players": players, "lineup": lineup, "shots": []})
    return recs


@pytest.fixture(scope="module")
def nb_card():
    rows, _ = fc.replay(sim_recs(0.4))
    return rows, fc.train_and_backtest(rows)


@pytest.fixture(scope="module")
def poisson_card():
    rows, _ = fc.replay(sim_recs(0.0, seed=8))
    return fc.train_and_backtest(rows)[1]


# ------------------------------------------------------------- distributions
def test_dispersion_is_recovered_and_probabilities_are_calibrated():
    rng = np.random.default_rng(0)
    mu = rng.gamma(2, 0.15, 100_000) + 0.02
    r = 1 / 0.3
    y = rng.negative_binomial(r, r / (r + mu))
    assert ml.estimate_alpha(y, mu) == pytest.approx(0.3, abs=0.03)
    for row in ml.reliability(ml.p_at_least(mu, 0.3), (y >= 1).astype(float)):
        assert abs(row["predicted"] - row["actual"]) < 0.01
    assert ml.brier(ml.p_at_least(mu, 0.3), (y >= 1) * 1.0) <= ml.brier(ml.p_at_least(mu, 0.0), (y >= 1) * 1.0)


def test_no_extra_spread_means_plain_poisson():
    mu = np.array([0.5, 1.0, 2.0])
    assert ml.p_at_least(mu, 0.0) == pytest.approx(1 - np.exp(-mu))
    assert (ml.p_at_least(mu, 0.5) < ml.p_at_least(mu, 0.0)).all()          # spread makes zeros more likely
    assert ml.estimate_alpha(np.array([0, 1, 2, 1, 0, 3.0]), np.full(6, 1.1)) >= 0.0


# ------------------------------------------------------------- walk-forward
def test_folds_only_train_on_the_past_and_do_not_overlap():
    rows, _ = fc.replay(sim_recs(0.4))
    dates = [r["date"] for r in rows]
    folds = ml.walk_forward_folds(dates, 4, min_train=1000, min_test=100)
    assert len(folds) == 4
    d = np.array(dates)
    for f in folds:
        assert max(d[f.train]) < min(d[f.test])
        assert min(d[f.test]) == f.start and max(d[f.test]) == f.end
    for a, b in zip(folds, folds[1:], strict=False):
        assert a.end < b.start and len(b.train) > len(a.train)               # windows expand, tests never overlap


def test_too_little_history_gives_no_folds():
    assert ml.walk_forward_folds(["2025-01-01"] * 50, 4) == []


# ------------------------------------------------------------- the card
def test_card_reports_folds_dispersion_and_calibrated_probabilities(nb_card):
    _, (model, card) = nb_card
    assert card["n_folds"] == len(card["folds"]) >= 2
    assert card["train_until"] <= card["test_from"] <= card["test_to"]
    assert {"baseline", "poisson", "boosted", "league average"} <= set(card["results"])
    assert card["alpha"] > 0.1                                                # extra spread is detected
    assert card["likelihood"]["negative_binomial"] > card["likelihood"]["poisson"]
    for row in card["prob_calibration"]:
        assert abs(row["predicted"] - row["actual"]) < 0.06, card["prob_calibration"]
    assert card["brier"]["used"] <= card["brier"]["baseline"] + 0.005
    assert (model is not None) == card["beats_baseline"]


def test_poisson_data_shows_much_less_spread_than_negative_binomial_data(nb_card, poisson_card):
    assert poisson_card["alpha"] < nb_card[1][1]["alpha"] - 0.15


def test_a_useless_model_is_rejected_but_probabilities_still_work(monkeypatch):
    rows, _ = fc.replay(sim_recs(0.4))

    class Junk:
        def predict(self, X):
            return np.full(len(X), 5.0)                                        # wildly wrong everywhere

    monkeypatch.setattr(ml, "fit", lambda kind, X, y: Junk())
    model, card = fc.train_and_backtest(rows)
    assert model is None and card["beats_baseline"] is False and "baseline is used" in card["reason"]
    assert card["status"] == "baseline only" and card["alpha"] > 0              # spread comes from the baseline


def test_paired_bootstrap_separates_real_gains_from_noise():
    rng = np.random.default_rng(3)
    groups = np.repeat(np.arange(500), 20)
    base = rng.gamma(2, 1, 10_000)
    better = base - 0.3 + rng.normal(0, 0.05, 10_000)
    gain, lo, hi = ml.paired_bootstrap(base, better, groups)
    assert lo > 0 and gain == pytest.approx(0.3, abs=0.02)
    noise = base + rng.normal(0, 0.5, 10_000)
    _, lo2, hi2 = ml.paired_bootstrap(base, noise, groups)
    assert lo2 < 0 < hi2


# ------------------------------------------------------------- features, matchday, season
def test_new_features_use_only_the_past():
    rows, _ = fc.replay(sim_recs(0.4, 60))
    first = next(r for r in rows if r["athlete_id"] == "000")
    assert first["f"][fc.FEATURES.index("rest_days")] == fc.DEFAULT_REST        # nothing known yet
    assert first["f"][fc.FEATURES.index("start10")] == pytest.approx(0.5)       # shrunk to 50% with no history
    later = [r for r in rows if r["athlete_id"] == "000"]
    assert all(0 <= r["f"][fc.FEATURES.index("rest_days")] <= 14 for r in later)
    assert later[-1]["f"][fc.FEATURES.index("start10")] > 0.8                   # he starts every match


def test_matchday_rows_carry_probabilities_that_order_sensibly():
    recs = sim_recs(0.4)
    _, st = fc.replay(recs)
    fx = [{"league": "eng.1", "event_id": "9", "date": "2026-03-02T15:00Z", "home": TEAMS[0], "away": TEAMS[1]}]
    rows = fc.matchday_forecast(st, None, fx, alpha=0.4, alpha_base=0.4)
    assert rows and all(0 < r["p2"] < r["p1"] < 1 and 0 < r["b1"] < 1 for r in rows)
    hi, lo = max(rows, key=lambda r: r["pred"]), min(rows, key=lambda r: r["pred"])
    assert hi["p1"] > lo["p1"]
    assert "p1" not in fc.matchday_forecast(st, None, fx)[0]                   # no alpha, no probabilities


def test_season_range_covers_outcomes_about_as_often_as_claimed():
    """Players with known true rates: the 10th-90th percentile range must not be overconfident."""
    rng = np.random.default_rng(1)
    n_hist, left, n, a = 20, 18, 500, 0.3
    lam = rng.gamma(2, 0.15, n)
    r = 1 / a
    hist = rng.negative_binomial(r * n_hist, r * n_hist / (r * n_hist + lam * n_hist))
    mu = (hist + fc.K * hist.sum() / (n * n_hist)) / (n_hist + fc.K)
    actual = rng.negative_binomial(r * left, r * left / (r * left + lam * left))
    draw_rng = np.random.default_rng(5)
    inside = []
    for i in range(n):
        d = fc.future_draws(draw_rng, float(mu[i]), left, a, n_hist, 2000)
        inside.append(np.percentile(d, 10) <= actual[i] <= np.percentile(d, 90))
    assert 0.78 <= np.mean(inside) <= 0.93                                     # nominal 80%, discrete counts run high
    old = [np.percentile(draw_rng.poisson(mu[i] * left, 2000), 10) <= actual[i]
           <= np.percentile(draw_rng.poisson(mu[i] * left, 2000), 90) for i in range(n)]
    assert np.mean(old) < np.mean(inside) - 0.05                              # the old plain-Poisson range was overconfident


def test_scorecard_grades_probabilities():
    recs = sim_recs(0.4, 120)
    now = datetime(2026, 3, 1)
    kick = (now + timedelta(days=2)).strftime("%Y-%m-%dT15:00Z")
    rows = [{"league": "eng.1", "event_id": "9001", "kickoff": kick, "team": "A", "opponent": "B", "home": True,
             "athlete_id": f"a{i}", "player": f"P{i}", "pred": 0.5, "baseline": 0.5, "p1": 0.4, "p2": 0.1, "b1": 0.4}
            for i in range(10)]
    forecast = {"asof": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "matchday": rows}
    played = {"league": "eng.1", "event_id": "9001", "date": kick, "teams": [], "lineup": [
        {"athlete_id": f"a{i}", "minutes": 90} for i in range(10)],
        "players": [{"athlete_id": f"a{i}", "shots_on_target": 1 if i < 4 else 0} for i in range(10)]}
    sc = fc.scorecard([forecast], recs + [played])
    assert sc["prob"]["n"] == 10 and sc["prob"]["actual_rate"] == 0.4 and sc["prob"]["brier"] == 0.24
    old = {"asof": forecast["asof"], "matchday": [{k: v for k, v in r.items() if k not in ("p1", "p2", "b1")} for r in rows]}
    assert "prob" not in fc.scorecard([old], recs + [played])                  # forecasts from before probabilities


def _page(card, matchday=None):
    from pitchline import site
    forecast = {"asof": "2026-03-01T06:00:00Z", "model": card, "matchday": matchday or [], "season": {}}
    return site.forecast_page(forecast, {"n": 0})


def test_forecast_page_shows_the_walk_forward_card_and_probabilities(nb_card):
    _, (_, card) = nb_card
    row = {"league": "eng.1", "event_id": "1", "kickoff": "2026-03-02T15:00Z", "team": "A", "opponent": "B",
           "home": True, "athlete_id": "1", "player": "Test Player", "pred": 0.6, "baseline": 0.5,
           "p1": 0.41, "p2": 0.12, "b1": 0.39}
    html = _page(card, [row])
    for needle in ("Walk-forward backtest", "Test period", "Chance of 1+", "41%", "dispersion of", "Said (avg chance)", "model card"):
        assert needle in html, needle


def test_forecast_page_still_renders_a_forecast_saved_before_these_changes():
    legacy = {"rows": 9000, "features": ["a"], "status": "poisson model", "train_until": "2025-12-01",
              "train_rows": 6000, "test_rows": 2000, "test_from": "2025-12-01", "test_to": "2026-02-01",
              "results": {"baseline": {"mae": 0.3, "deviance": 0.7, "bias": 0.0},
                          "poisson": {"mae": 0.29, "deviance": 0.65, "bias": 0.0}},
              "best": "poisson", "beats_baseline": True,
              "calibration": [{"predicted": 0.2, "actual": 0.21, "n": 400}]}
    row = {"league": "eng.1", "event_id": "1", "kickoff": "2026-03-02T15:00Z", "team": "A", "opponent": "B",
           "home": True, "athlete_id": "1", "player": "Old Row", "pred": 0.6, "baseline": 0.5}
    html = _page(legacy, [row])
    assert "Old Row" in html and "before walk-forward testing was added" in html
    assert "Chance of 1+" not in html and "dispersion" not in html
    assert "baseline only" in _page({"rows": 10, "status": "baseline only", "reason": "needs more", "alpha": None})
