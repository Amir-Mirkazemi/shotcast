"""Shots-on-target forecasts, a public backtest, and a scorecard that grades saved forecasts.

What it predicts: how many shots on target each player will have in a match (next matchday), and how many
they will finish the season with (season projection, deliberately simple).

How it is judged: walk-forward validation. Several successive future periods are each predicted by a model
trained only on what came before, and compared with a plain baseline (the player's own recent rate times
expected minutes). A model is only used if it beats the baseline clearly (paired bootstrap over matches) and
in most periods. The spread of outcomes around each forecast is a negative binomial whose dispersion is
measured on those out-of-sample periods, which gives "chance of at least one on target". Every forecast is
saved before the matches, then graded on what actually happened.

Run: python -m shotcast.forecast --root s3://bucket/shotcast
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict, deque
from datetime import date, datetime, timezone

import numpy as np

from . import mlcore as ml
from .espn_shots import _norm
from .leaderboard import read_records
from .live import LEAGUES, day_events
from .storage import Store, get_store

log = logging.getLogger("shotcast.forecast")
FEATURES = ["rate10", "rate_long", "shots10", "minutes5", "apps", "home", "team_for", "opp_against",
            "start10", "rest_days"]
SEASON_GAMES = {"eng.1": 38, "esp.1": 38, "ita.1": 38, "ger.1": 34, "fra.1": 34, "usa.1": 34}
K = 3.0                  # prior strength, in 90-minute games, for shrinking a player's rate toward the average
MIN_TRAIN = 3000         # fewer player-matches than this and no model is fitted: baseline only
MIN_TEST = 500
K_FOLDS = 4              # walk-forward test periods
MIN_FOLD_TEST = 200
DEFAULT_REST = 7.0
PRIOR_RATE = 0.2


class State:
    """What is known about everyone after the matches replayed so far."""

    def __init__(self):
        self.hist = defaultdict(lambda: deque(maxlen=40))      # athlete -> (minutes, sot, shots, started)
        self.last_date: dict[str, str] = {}
        self.team_hist = defaultdict(lambda: deque(maxlen=10))  # team key -> (shots for, shots against)
        self.team_apps = defaultdict(lambda: deque(maxlen=5))   # team key -> sets of athlete ids who played
        self.last_team: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.sot = 0.0
        self.min90 = 0.0

    def prior(self) -> float:
        return self.sot / self.min90 if self.min90 > 300 else PRIOR_RATE

    def team_avgs(self, team: str) -> tuple[float, float]:
        h = self.team_hist.get(team)
        if not h:
            return 12.0, 12.0
        return (sum(a for a, _ in h) / len(h), sum(b for _, b in h) / len(h))

    def rest_days(self, aid: str, day: str) -> float:
        last = self.last_date.get(aid)
        if not last or not day:
            return DEFAULT_REST
        try:
            return float(min(max((date.fromisoformat(day[:10]) - date.fromisoformat(last[:10])).days, 0), 14))
        except ValueError:
            return DEFAULT_REST

    def features(self, aid: str, team: str, opp: str, home: float, day: str = "") -> list[float]:
        h = list(self.hist.get(aid, ()))
        prior = self.prior()
        last10 = h[-10:]
        m10, s10, sh10 = (sum(x[i] for x in last10) for i in range(3))
        mall, sall = sum(x[0] for x in h), sum(x[1] for x in h)
        rate10 = (s10 + K * prior) / (m10 / 90 + K)
        rate_long = (sall + K * prior) / (mall / 90 + K)
        shots10 = (sh10 + K * prior * 2.5) / (m10 / 90 + K)
        last5 = h[-5:]
        minutes5 = sum(x[0] for x in last5) / len(last5) if last5 else 45.0
        t_for = self.team_avgs(team)[0]
        o_against = self.team_avgs(opp)[1]
        start10 = (sum(x[3] for x in last10) + 1.0) / (len(last10) + 2.0)        # share of recent matches started
        return [rate10, rate_long, shots10, minutes5, float(min(len(h), 10)), home, t_for, o_against,
                start10, self.rest_days(aid, day)]


def baseline(f: list[float]) -> float:
    return max(f[0] * f[3] / 90, 1e-3)


def _team_key(name: str) -> str:
    return _norm(name or "")


def replay(recs: list[dict]) -> tuple[list[dict], State]:
    """One row per player-match (with features known BEFORE the match), oldest first, plus the final state."""
    st = State()
    rows: list[dict] = []
    for rec in sorted(recs, key=lambda r: r.get("date") or ""):
        lineup = [p for p in rec.get("lineup", []) if p.get("athlete_id") and (p.get("minutes") or 0) > 0]
        teams = rec.get("teams") or []
        if not lineup or len(teams) != 2:
            continue
        keys = {t["name"]: _team_key(t["name"]) for t in teams}
        home_of = {keys[t["name"]]: 1.0 if t.get("home") else 0.0 for t in teams}
        opp_of = {keys[teams[0]["name"]]: keys[teams[1]["name"]], keys[teams[1]["name"]]: keys[teams[0]["name"]]}
        stats = {p["athlete_id"]: p for p in rec.get("players", []) if p.get("athlete_id")}
        shots_for: dict[str, int] = defaultdict(int)
        for p in rec.get("players", []):
            shots_for[_team_key(p["team"])] += p.get("shots") or 0
        day = (rec.get("date") or "")[:10]
        for p in lineup:
            tk = _team_key(p["team"])
            if tk not in opp_of:
                continue
            f = st.features(p["athlete_id"], tk, opp_of[tk], home_of[tk], day)
            s = stats.get(p["athlete_id"], {})
            rows.append({"date": day, "league": rec["league"], "event_id": rec["event_id"],
                         "athlete_id": p["athlete_id"], "player": p["player"], "team": p["team"], "f": f,
                         "y": s.get("shots_on_target") or 0, "minutes": p["minutes"]})
        for p in lineup:                                   # update only after every row of this match is made
            tk = _team_key(p["team"])
            if tk not in opp_of:
                continue
            s = stats.get(p["athlete_id"], {})
            sot, sh = s.get("shots_on_target") or 0, s.get("shots") or 0
            started = p.get("starter")
            started = p["minutes"] >= 60 if started is None else bool(started)
            st.hist[p["athlete_id"]].append((p["minutes"], sot, sh, 1.0 if started else 0.0))
            st.last_date[p["athlete_id"]] = day
            st.sot += sot
            st.min90 += p["minutes"] / 90
            st.last_team[p["athlete_id"]] = tk
            st.names[p["athlete_id"]] = p["player"]
        for tk, opp in opp_of.items():
            st.team_hist[tk].append((shots_for.get(tk, 0), shots_for.get(opp, 0)))
            st.team_apps[tk].append({p["athlete_id"] for p in lineup if _team_key(p["team"]) == tk})
    return rows, st


_deviance, _metrics, calibration = ml.deviance, ml.metrics, ml.reliability


def train_and_backtest(rows: list[dict]) -> tuple[object | None, dict]:
    """Walk-forward backtest of every candidate against the baseline; refit the winner on all rows.

    Returns (model or None, card). None means the baseline is used. The card always carries the dispersion
    (`alpha`) measured on out-of-sample predictions of whichever predictor is used, so probabilities stay honest
    even when the baseline wins."""
    card: dict = {"rows": len(rows), "features": FEATURES, "status": "baseline only", "alpha": None,
                  "alpha_baseline": None}
    if len(rows) < MIN_TRAIN + MIN_TEST:
        card["reason"] = f"needs at least {MIN_TRAIN + MIN_TEST:,} player-matches with minutes; have {len(rows):,}"
        return None, card
    folds = ml.walk_forward_folds([r["date"] for r in rows], K_FOLDS, min_train=MIN_TRAIN, min_test=MIN_FOLD_TEST)
    if len(folds) < 2:
        card["reason"] = "not enough history for walk-forward validation (needs at least two test periods)"
        return None, card
    X, y = np.array([r["f"] for r in rows]), np.array([r["y"] for r in rows], float)
    base = np.array([baseline(r["f"]) for r in rows])
    kinds = ml.available_models()
    oof = {k: np.full(len(rows), np.nan) for k in kinds}
    league = np.full(len(rows), np.nan)
    per_fold = []
    for f in folds:
        league[f.test] = y[f.train].mean()
        dev = {"baseline": round(ml.deviance(y[f.test], base[f.test]), 4)}
        for k in kinds:
            oof[k][f.test] = ml.predict_mean(ml.fit(k, X[f.train], y[f.train]), X[f.test])
            dev[k] = round(ml.deviance(y[f.test], oof[k][f.test]), 4)
        per_fold.append({"from": f.start, "to": f.end, "train_rows": int(len(f.train)), "test_rows": int(len(f.test)),
                         "deviance": dev})
    idx = np.concatenate([f.test for f in folds])
    yt = y[idx]
    results = {"baseline": ml.metrics(yt, base[idx]), "league average": ml.metrics(yt, league[idx])}
    for k in kinds:
        results[k] = ml.metrics(yt, oof[k][idx])
    best = min(kinds, key=lambda k: results[k]["deviance"])
    wins = sum(1 for pf in per_fold if pf["deviance"][best] < pf["deviance"]["baseline"])
    groups = np.array([rows[i]["event_id"] for i in idx])
    gain, lo, hi = ml.paired_bootstrap(ml.deviance_rows(yt, base[idx]), ml.deviance_rows(yt, oof[best][idx]), groups)
    beats = bool(gain > 0 and lo > 0 and wins > len(folds) / 2)
    used = oof[best][idx] if beats else base[idx]
    alpha, alpha_b = ml.estimate_alpha(yt, used), ml.estimate_alpha(yt, base[idx])
    happened = (yt >= 1).astype(float)
    p_used, p_base = ml.p_at_least(used, alpha), ml.p_at_least(base[idx], alpha_b)
    card.update({
        "train_until": folds[0].start, "train_rows": int(len(folds[0].train)), "test_rows": int(len(idx)),
        "test_from": folds[0].start, "test_to": folds[-1].end, "folds": per_fold, "results": results, "best": best,
        "beats_baseline": beats, "wins": wins, "n_folds": len(folds),
        "gain": {"mean": round(gain, 4), "lo": round(lo, 4), "hi": round(hi, 4)},
        "calibration": ml.reliability(used, yt),
        "alpha": round(alpha, 4), "alpha_baseline": round(alpha_b, 4),
        "likelihood": {"poisson": round(float(ml.dist(used, 0.0).logpmf(yt).mean()), 4),
                       "negative_binomial": round(float(ml.dist(used, alpha).logpmf(yt).mean()), 4)},
        "prob_calibration": ml.reliability(p_used, happened),
        "brier": {"used": round(ml.brier(p_used, happened), 4), "baseline": round(ml.brier(p_base, happened), 4)},
        "base_rate": round(float(happened.mean()), 4)})
    if not beats:
        card["reason"] = ("no model beat the baseline clearly enough across the test periods, so the baseline is used")
        return None, card
    card["status"] = f"{best} model"
    return ml.fit(best, X, y), card


def predict(model, f: list[float]) -> float:
    return baseline(f) if model is None else float(max(model.predict(np.array([f]))[0], 1e-3))


def upcoming(fetch, now: datetime, leagues=LEAGUES, days: int = 7, sleep=None) -> list[dict]:
    import time
    out = []
    for lg in leagues:
        try:
            events = day_events(fetch, lg, now.date(), days, sleep or time.sleep)
        except Exception as err:   # noqa: BLE001 - a broken competition should not stop the others
            log.warning("fixtures for %s failed: %s", lg, err)
            continue
        for e in events:
            try:
                if e["status"]["type"]["state"] != "pre":
                    continue
                c = {x["homeAway"]: x["team"]["displayName"] for x in e["competitions"][0]["competitors"]}
                out.append({"league": lg, "event_id": str(e["id"]), "date": e["date"], "home": c["home"],
                            "away": c["away"]})
            except (KeyError, IndexError, TypeError):
                continue
    return sorted(out, key=lambda x: x["date"])


def matchday_forecast(st: State, model, fixtures: list[dict], per_team: int = 14,
                      alpha: float | None = None, alpha_base: float | None = None) -> list[dict]:
    preds = []
    for fx in fixtures:
        for team, opp, home in ((fx["home"], fx["away"], 1.0), (fx["away"], fx["home"], 0.0)):
            tk, ok = _team_key(team), _team_key(opp)
            apps = st.team_apps.get(tk)
            if not apps:
                continue
            for aid in set().union(*apps):
                recent = sum(aid in a for a in apps)
                if st.last_team.get(aid) != tk or recent < 2:
                    continue
                f = st.features(aid, tk, ok, home, str(fx.get("date") or "")[:10])
                if f[3] < 30:
                    continue
                row = {"league": fx["league"], "event_id": fx["event_id"], "kickoff": fx["date"],
                       "team": team, "opponent": opp, "home": bool(home), "athlete_id": aid,
                       "player": st.names[aid], "pred": round(predict(model, f), 3),
                       "baseline": round(baseline(f), 3)}
                if alpha is not None:                       # chance of 1+ and 2+ shots on target
                    row["p1"] = round(float(ml.p_at_least(row["pred"], alpha, 1)), 4)
                    row["p2"] = round(float(ml.p_at_least(row["pred"], alpha, 2)), 4)
                    row["b1"] = round(float(ml.p_at_least(row["baseline"], alpha_base or 0.0, 1)), 4)
                preds.append(row)
    keep = []
    by_team: dict = defaultdict(list)
    for p in preds:
        by_team[(p["event_id"], p["team"])].append(p)
    for lst in by_team.values():
        keep += sorted(lst, key=lambda p: -p["pred"])[:per_team]
    return sorted(keep, key=lambda p: (-p["pred"], p["player"]))


def future_draws(rng, mu: float, left: int, alpha: float | None, n_matches: int, draws: int = 4000):
    """Shots on target over the remaining matches. Two sources of doubt are combined: the player's true rate is
    only estimated (gamma, tighter the more matches and shots behind the estimate) and each match adds
    negative-binomial noise on top of the rate (a sum of `left` such matches is negative binomial too)."""
    if left <= 0:
        return np.zeros(draws)
    shape = max(1.0, mu * (n_matches + K))
    lam = rng.gamma(shape, mu / shape, draws) * left
    if alpha is None or alpha < ml.MIN_ALPHA:
        return rng.poisson(lam).astype(float)
    r = left / alpha
    return rng.negative_binomial(r, r / (r + lam)).astype(float)


def season_projection(recs: list[dict], st: State, model, top: int = 25, alpha: float | None = None) -> dict:
    """Shots on target so far + expected rate x matches left. The range (10th to 90th percentile) covers the
    luck in the remaining matches and the uncertainty in the player's rate, but not injuries or transfers."""
    out: dict = {}
    rng = np.random.default_rng(0)
    for lg, games in SEASON_GAMES.items():
        lrecs = [r for r in recs if r["league"] == lg and r.get("season")]
        if not lrecs:
            continue
        season = max(r["season"] for r in lrecs)
        cur = [r for r in lrecs if r["season"] == season]
        played: dict[str, int] = defaultdict(int)
        for r in cur:
            for t in r.get("teams") or []:
                played[_team_key(t["name"])] += 1
        so_far: dict[str, float] = defaultdict(float)
        mins: dict[str, float] = defaultdict(float)
        for r in cur:
            for p in r.get("players", []):
                if p.get("athlete_id"):
                    so_far[p["athlete_id"]] += p.get("shots_on_target") or 0
            for p in r.get("lineup", []):
                if p.get("athlete_id"):
                    mins[p["athlete_id"]] += p.get("minutes") or 0
        rows = []
        for aid, m in mins.items():
            tk = st.last_team.get(aid)
            if m < 450 or tk is None or played.get(tk, 0) < 5:
                continue
            f = st.features(aid, tk, "", 0.5)
            f[7] = 12.0
            mu = predict(model, f)
            left = max(games - played[tk], 0)
            draws = future_draws(rng, mu, left, alpha, len(st.hist.get(aid, ())))
            rows.append({"athlete_id": aid, "player": st.names[aid], "team": next(
                (t["name"] for r in cur for t in r.get("teams") or [] if _team_key(t["name"]) == tk), tk),
                "so_far": int(so_far[aid]), "per_match": round(mu, 3), "left": left,
                "proj": round(so_far[aid] + mu * left, 1),
                "lo": int(so_far[aid] + np.percentile(draws, 10)), "hi": int(so_far[aid] + np.percentile(draws, 90))})
        rows.sort(key=lambda r: -r["proj"])
        out[lg] = {"season": season, "games": games, "players": rows[:top]}
    return out


def make_forecast(recs: list[dict], fetch, now: datetime | None = None, sleep=None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows, st = replay(recs)
    model, card = train_and_backtest(rows)
    alpha = card.get("alpha")
    return {"asof": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "model": card,
            "matchday": matchday_forecast(st, model, upcoming(fetch, now, sleep=sleep), alpha=alpha,
                                          alpha_base=card.get("alpha_baseline")),
            "season": season_projection(recs, st, model, alpha=alpha)}


def scorecard(forecasts: list[dict], recs: list[dict]) -> dict:
    """Grade saved forecasts on matches played after they were made."""
    actual: dict[tuple[str, str], tuple[dict, dict]] = {}
    for r in recs:
        if r.get("lineup"):
            actual[(r["league"], r["event_id"])] = (
                {p["athlete_id"]: p.get("shots_on_target") or 0 for p in r.get("players", []) if p.get("athlete_id")},
                {p["athlete_id"]: p.get("minutes") or 0 for p in r["lineup"] if p.get("athlete_id")})
    latest: dict[tuple[str, str, str], dict] = {}
    for fc in sorted(forecasts, key=lambda f: f["asof"]):
        for p in fc.get("matchday", []):
            if p["kickoff"] > fc["asof"]:                        # made before kickoff, otherwise not a forecast
                latest[(p["league"], p["event_id"], p["athlete_id"])] = p
    ys, mus, bs, played = [], [], [], 0
    p1s, b1s, hit, did_play = [], [], [], []
    matches = set()
    for (lg, eid, aid), p in latest.items():
        if (lg, eid) not in actual:
            continue
        sot, mins = actual[(lg, eid)]
        ys.append(sot.get(aid, 0))
        mus.append(p["pred"])
        bs.append(p["baseline"])
        played += mins.get(aid, 0) > 0
        did_play.append(mins.get(aid, 0) > 0)
        matches.add((lg, eid))
        if "p1" in p and "b1" in p:                            # forecasts made after the probabilities were added
            p1s.append(p["p1"])
            b1s.append(p["b1"])
            hit.append(1.0 if sot.get(aid, 0) >= 1 else 0.0)
    if not ys:
        return {"n": 0}
    y, mu, b = np.array(ys, float), np.array(mus), np.array(bs)
    out = {"n": len(ys), "matches": len(matches), "played": int(played),
           "predicted_total": round(float(mu.sum()), 1), "actual_total": int(y.sum()),
           "model": _metrics(y, mu), "baseline": _metrics(y, b)}
    pl = np.array(did_play)
    if pl.any() and not pl.all():                      # how much of the miss is players who never got on the pitch?
        out["played_only"] = {"n": int(pl.sum()), "predicted_total": round(float(mu[pl].sum()), 1),
                              "actual_total": int(y[pl].sum()), "model": _metrics(y[pl], mu[pl]),
                              "baseline": _metrics(y[pl], b[pl])}
    if p1s:
        ph, hh = np.array(p1s), np.array(hit)
        out["prob"] = {"n": len(p1s), "brier": round(ml.brier(ph, hh), 4), "brier_baseline": round(ml.brier(np.array(b1s), hh), 4),
                       "predicted_rate": round(float(ph.mean()), 4), "actual_rate": round(float(hh.mean()), 4),
                       "reliability": ml.reliability(ph, hh)}
    return out


def load_forecasts(store: Store) -> list[dict]:
    return [json.loads(store.read_bytes(k)) for k in sorted(store.list_keys("forecasts/")) if k.endswith(".json")]


def main(argv=None) -> int:
    from .http import get_json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="storage root (default: SHOTCAST_ROOT)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store = get_store(args.root)
    fc = make_forecast(read_records(store), get_json)
    store.write_bytes(f"forecasts/{fc['asof'].replace(':', '')}.json", json.dumps(fc).encode())
    m = fc["model"]
    print(f"model: {m['status']} ({m.get('reason', 'ok')}); {len(fc['matchday'])} player forecasts; "
          f"{sum(len(v['players']) for v in fc['season'].values())} season rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
