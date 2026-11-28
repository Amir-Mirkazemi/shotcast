"""Expected goals (xG): the chance that a shot becomes a goal, from where and how it was taken.

Every shot with a usable location gets a probability from a model that sees only what is known at the moment of
the shot: distance and angle to goal, header or foot, and the situation (open play, penalty, free kick, corner).
The outcome is never a feature. Validation is the same walk-forward scheme as the forecast: successive future
periods predicted by a model trained on earlier matches only, compared with a constant rate and with a
distance-only model. A fuller model is used only if it clearly beats the distance-only one; otherwise the
distance-only model is used and the page says so.

The xG shown for each shot comes from cross-fitting: a model that never saw that match scored it.
"""
from __future__ import annotations

import logging

import numpy as np

from . import mlcore as ml
from .sitedata import pitch_coords

log = logging.getLogger("shotcast.xg")
SHOT_OUTCOMES = {"goal", "saved", "off_target", "blocked", "woodwork"}      # own goals are not the shooter's
MIN_SHOTS = 5000
FOLDS = 4
CROSS_FIT_FOLDS = 5
LENGTH, WIDTH, GOAL_HALF = 105.0, 68.0, 3.66          # metres
EDGES = (0.0, 0.03, 0.06, 0.10, 0.20, 0.40, 1.0)       # calibration groups for goal probabilities
CANDIDATES = ("distance", "logistic", "boosted")
NAMES = {"constant": "Same chance for every shot", "distance": "Distance to goal only",
         "logistic": "Logistic regression (distance, angle, body part, situation)",
         "boosted": "Gradient-boosted trees (same inputs)"}


def geometry(x_pct, y_pct):
    """Distance (m) and the angle the goal mouth spans (radians) from points on the 0-100 grid, goal at x=100."""
    dx = LENGTH - np.asarray(x_pct, float) * LENGTH / 100
    dy = WIDTH / 2 - np.asarray(y_pct, float) * WIDTH / 100
    dist = np.hypot(dx, dy)
    ang = np.arctan2(2 * GOAL_HALF * dx, dx * dx + dy * dy - GOAL_HALF ** 2)
    return dist, np.where(ang < 0, ang + np.pi, ang)


def features(shots: list[dict]) -> np.ndarray:
    dist, ang = geometry([s["x"] for s in shots], [s["y"] for s in shots])
    col = lambda f: np.array([f(s) for s in shots], float)  # noqa: E731
    return np.column_stack([dist, ang, np.log(dist + 1), col(lambda s: s["body"] == "header"),
                            col(lambda s: s["sit"] == "penalty"), col(lambda s: s["sit"] == "free_kick"),
                            col(lambda s: s["sit"] == "from_corner")])


def collect(recs: list[dict]) -> list[dict]:
    """Every shot that counts for the shooter, with the shooter and assister resolved to ESPN athlete ids.

    Names are resolved inside the match and team; a name that two players share there is not guessed."""
    out: list[dict] = []
    for rec in recs:
        shots = rec.get("shots") or []
        if not shots:
            continue
        ids: dict[tuple, str | None] = {}
        for p in [*rec.get("players", []), *rec.get("lineup", [])]:
            if p.get("athlete_id") and p.get("player"):
                key = (p.get("team"), p["player"])
                ids[key] = None if ids.get(key, p["athlete_id"]) != p["athlete_id"] else p["athlete_id"]
        mins = {p["athlete_id"]: p.get("minutes") or 0 for p in rec.get("lineup", []) if p.get("athlete_id")}
        for s, c in zip(shots, pitch_coords(shots), strict=True):
            if s.get("outcome") not in SHOT_OUTCOMES:
                continue
            aid = ids.get((s.get("team"), s.get("player")))
            asst = ids.get((s.get("team"), s.get("assist_player"))) if s.get("assist_player") else None
            out.append({"league": rec["league"], "event_id": str(rec["event_id"]), "date": (rec.get("date") or "")[:10],
                        "aid": aid, "assist_aid": asst, "x": None if c is None else c[0],
                        "y": None if c is None else c[1], "goal": s["outcome"] == "goal",
                        "body": s.get("body_part"), "sit": s.get("situation"),
                        "lc": bool(aid and mins.get(aid, 0) > 0), "assist_lc": bool(asst and mins.get(asst, 0) > 0)})
    return out


class _DistanceOnly:
    """Logistic regression on log distance alone (the plain, hard-to-beat baseline)."""

    def __init__(self):
        from sklearn.linear_model import LogisticRegression
        self.m = LogisticRegression(C=10.0, max_iter=500)

    def fit(self, X, y):
        self.m.fit(X[:, [2]], y)
        return self

    def predict_proba(self, X):
        return self.m.predict_proba(X[:, [2]])


def fit(kind: str, X: np.ndarray, y: np.ndarray):
    if kind == "distance":
        return _DistanceOnly().fit(X, y)
    if kind == "logistic":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000)).fit(X, y)
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=150, learning_rate=0.06, max_depth=4, l2_regularization=1.0,
                                          random_state=0).fit(X, y)


def prob(model, X: np.ndarray) -> np.ndarray:
    return np.clip(model.predict_proba(X)[:, 1], 1e-4, 1 - 1e-4)


def logloss_rows(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import roc_auc_score
    return {"logloss": round(float(logloss_rows(y, p).mean()), 4), "brier": round(ml.brier(p, y), 4),
            "auc": round(float(roc_auc_score(y, p)), 4), "predicted": round(float(p.sum()), 1),
            "actual": int(y.sum())}


def calibration(y: np.ndarray, p: np.ndarray) -> list[dict]:
    out = []
    for lo, hi in zip(EDGES, EDGES[1:], strict=False):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum():
            out.append({"predicted": round(float(p[m].mean()), 4), "actual": round(float(y[m].mean()), 4),
                        "n": int(m.sum())})
    return out


def validate(shots: list[dict]) -> tuple[str, dict]:
    """Walk-forward comparison on located shots. Returns (kind to use, card)."""
    rows = sorted((s for s in shots if s["x"] is not None), key=lambda s: s["date"])
    card: dict = {"shots": len(rows), "status": "not enough shots"}
    if len(rows) < MIN_SHOTS:
        card["reason"] = f"needs at least {MIN_SHOTS:,} shots with a location; have {len(rows):,}"
        return "", card
    X, y = features(rows), np.array([s["goal"] for s in rows], float)
    folds = ml.walk_forward_folds([s["date"] for s in rows], FOLDS, min_train=2000, min_test=500)
    if len(folds) < 2:
        card["reason"] = "not enough history for walk-forward validation"
        return "", card
    oof = {k: np.full(len(rows), np.nan) for k in ("constant", *CANDIDATES)}
    per_fold = []
    for f in folds:
        oof["constant"][f.test] = y[f.train].mean()
        ll = {"constant": round(float(logloss_rows(y[f.test], oof["constant"][f.test]).mean()), 4)}
        for k in CANDIDATES:
            oof[k][f.test] = prob(fit(k, X[f.train], y[f.train]), X[f.test])
            ll[k] = round(float(logloss_rows(y[f.test], oof[k][f.test]).mean()), 4)
        per_fold.append({"from": f.start, "to": f.end, "train_rows": int(len(f.train)),
                         "test_rows": int(len(f.test)), "logloss": ll})
    idx = np.concatenate([f.test for f in folds])
    yt = y[idx]
    results = {k: _metrics(yt, oof[k][idx]) for k in ("constant", *CANDIDATES)}
    best = min(("logistic", "boosted"), key=lambda k: results[k]["logloss"])
    wins = sum(1 for pf in per_fold if pf["logloss"][best] < pf["logloss"]["distance"])
    groups = np.array([rows[i]["event_id"] for i in idx])
    gain, lo, hi = ml.paired_bootstrap(logloss_rows(yt, oof["distance"][idx]), logloss_rows(yt, oof[best][idx]), groups)
    beats = bool(gain > 0 and lo > 0 and wins > len(folds) / 2)
    used = best if beats else "distance"
    card.update({"status": f"{used} model", "used": used, "best": best, "beats_distance": beats, "folds": per_fold,
                 "n_folds": len(folds), "wins": wins, "results": results, "test_from": folds[0].start,
                 "test_to": folds[-1].end, "test_shots": int(len(idx)), "goal_rate": round(float(yt.mean()), 4),
                 "gain": {"mean": round(gain, 5), "lo": round(lo, 5), "hi": round(hi, 5)},
                 "calibration": calibration(yt, oof[used][idx])})
    return used, card


def cross_fit(shots: list[dict], kind: str, k: int = CROSS_FIT_FOLDS) -> None:
    """Give every located shot an `xg` from a model that never saw its match (folds are whole matches)."""
    loc = [s for s in shots if s["x"] is not None]
    X, y = features(loc), np.array([s["goal"] for s in loc], float)
    events = sorted({s["event_id"] + s["league"] for s in loc})
    fold_of = {e: i % k for i, e in enumerate(events)}
    part = np.array([fold_of[s["event_id"] + s["league"]] for s in loc])
    for i in range(k):
        test = part == i
        model = fit(kind, X[~test], y[~test])
        for s, p in zip((s for s, t in zip(loc, test, strict=True) if t), prob(model, X[test]), strict=True):
            s["xg"] = float(p)
    dist, _ = geometry([s["x"] for s in loc], [s["y"] for s in loc])
    for s, d in zip(loc, dist, strict=True):
        s["dist"] = float(d)


def score_shots(recs: list[dict]) -> tuple[dict, list[dict]]:
    """(card, shots that have an xG). Empty list and an explanatory card when there is too little data."""
    shots = collect(recs)
    kind, card = validate(shots)
    if not kind:
        return card, []
    cross_fit(shots, kind)
    return card, [s for s in shots if "xg" in s]
