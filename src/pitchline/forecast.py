"""Shots-on-target forecasts, a public backtest, and a scorecard that grades saved forecasts.

What it predicts: how many shots on target each player will have in a match (next matchday), and how many
they will finish the season with (season projection, deliberately simple).

How it is judged: models are trained on older matches and tested on newer ones, never the reverse, and are
compared with a plain baseline (the player's own recent rate times expected minutes). A model is only used
if it beats the baseline on the test period. Every forecast is saved before the matches, then graded on
what actually happened.

Run: python -m pitchline.forecast --root s3://bucket/pitchline
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from .espn_shots import _norm
from .leaderboard import read_records
from .live import LEAGUES, day_events
from .storage import Store, get_store

log = logging.getLogger("pitchline.forecast")
FEATURES = ["rate10", "rate_long", "shots10", "minutes5", "apps", "home", "team_for", "opp_against"]
SEASON_GAMES = {"eng.1": 38, "esp.1": 38, "ita.1": 38, "ger.1": 34, "fra.1": 34, "usa.1": 34}
K = 3.0                  # prior strength, in 90-minute games, for shrinking a player's rate toward the average
MIN_TRAIN = 3000         # fewer player-matches than this and no model is fitted: baseline only
MIN_TEST = 500
PRIOR_RATE = 0.2


class State:
    """What is known about everyone after the matches replayed so far."""

    def __init__(self):
        self.hist = defaultdict(lambda: deque(maxlen=40))      # athlete -> (minutes, sot, shots)
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

    def features(self, aid: str, team: str, opp: str, home: float) -> list[float] | None:
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
        return [rate10, rate_long, shots10, minutes5, float(min(len(h), 10)), home, t_for, o_against]


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
            f = st.features(p["athlete_id"], tk, opp_of[tk], home_of[tk])
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
            st.hist[p["athlete_id"]].append((p["minutes"], sot, sh))
            st.sot += sot
            st.min90 += p["minutes"] / 90
            st.last_team[p["athlete_id"]] = tk
            st.names[p["athlete_id"]] = p["player"]
        for tk, opp in opp_of.items():
            st.team_hist[tk].append((shots_for.get(tk, 0), shots_for.get(opp, 0)))
            st.team_apps[tk].append({p["athlete_id"] for p in lineup if _team_key(p["team"]) == tk})
    return rows, st


def _deviance(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.clip(mu, 1e-3, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = 2 * (np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / mu), 0.0) - (y - mu))
    return float(np.mean(d))


def _metrics(y: np.ndarray, mu: np.ndarray) -> dict:
    return {"mae": round(float(np.mean(np.abs(y - mu))), 4), "deviance": round(_deviance(y, mu), 4),
            "bias": round(float(np.mean(mu - y)), 4)}


def _fit(kind: str, X: np.ndarray, y: np.ndarray):
    if kind == "poisson":
        from sklearn.linear_model import PoissonRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(StandardScaler(), PoissonRegressor(alpha=1e-3, max_iter=500)).fit(X, y)
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(loss="poisson", max_iter=150, learning_rate=0.05, max_depth=3,
                                         random_state=0).fit(X, y)


def calibration(y: np.ndarray, mu: np.ndarray, bins: int = 5) -> list[dict]:
    order = np.argsort(mu)
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append({"predicted": round(float(mu[chunk].mean()), 3), "actual": round(float(y[chunk].mean()), 3),
                        "n": int(len(chunk))})
    return out


def train_and_backtest(rows: list[dict]) -> tuple[object | None, dict]:
    """Fit on the older 75% of matches, test on the newest 25%, pick the best model, refit on everything."""
    card: dict = {"rows": len(rows), "features": FEATURES, "status": "baseline only"}
    if len(rows) < MIN_TRAIN + MIN_TEST:
        card["reason"] = f"needs at least {MIN_TRAIN + MIN_TEST:,} player-matches with minutes; have {len(rows):,}"
        return None, card
    cut = rows[int(len(rows) * 0.75)]["date"]
    train = [r for r in rows if r["date"] < cut]
    test = [r for r in rows if r["date"] >= cut]
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST:
        card["reason"] = "not enough rows on one side of the time split"
        return None, card
    Xtr, ytr = np.array([r["f"] for r in train]), np.array([r["y"] for r in train], float)
    Xte, yte = np.array([r["f"] for r in test]), np.array([r["y"] for r in test], float)
    results = {"baseline": _metrics(yte, np.array([baseline(r["f"]) for r in test])),
               "league average": _metrics(yte, np.full(len(yte), ytr.mean()))}
    models = {}
    for kind in ("poisson", "boosted"):
        m = _fit(kind, Xtr, ytr)
        models[kind] = m
        results[kind] = _metrics(yte, np.clip(m.predict(Xte), 1e-3, None))
    best = min(models, key=lambda k: results[k]["deviance"])
    card.update({"train_until": cut, "train_rows": len(train), "test_rows": len(test),
                 "test_from": test[0]["date"], "test_to": test[-1]["date"], "results": results,
                 "best": best, "beats_baseline": results[best]["deviance"] < results["baseline"]["deviance"],
                 "calibration": calibration(yte, np.clip(models[best].predict(Xte), 1e-3, None))})
    if not card["beats_baseline"]:
        card["reason"] = "no model beat the baseline on the test period, so the baseline is used"
        return None, card
    final = _fit(best, np.array([r["f"] for r in rows]), np.array([r["y"] for r in rows], float))
    card["status"] = f"{best} model"
    return final, card


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


def matchday_forecast(st: State, model, fixtures: list[dict], per_team: int = 14) -> list[dict]:
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
                f = st.features(aid, tk, ok, home)
                if f[3] < 30:
                    continue
                preds.append({"league": fx["league"], "event_id": fx["event_id"], "kickoff": fx["date"],
                              "team": team, "opponent": opp, "home": bool(home), "athlete_id": aid,
                              "player": st.names[aid], "pred": round(predict(model, f), 3),
                              "baseline": round(baseline(f), 3)})
    keep = []
    by_team: dict = defaultdict(list)
    for p in preds:
        by_team[(p["event_id"], p["team"])].append(p)
    for lst in by_team.values():
        keep += sorted(lst, key=lambda p: -p["pred"])[:per_team]
    return sorted(keep, key=lambda p: (-p["pred"], p["player"]))


def season_projection(recs: list[dict], st: State, model, top: int = 25) -> dict:
    """Shots on target so far + expected rate x matches left. Simple on purpose; the interval is only the
    Poisson noise of the future matches, not the uncertainty in the rate itself."""
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
            draws = rng.poisson(mu * left, 4000) if left else np.zeros(1)
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
    return {"asof": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "model": card,
            "matchday": matchday_forecast(st, model, upcoming(fetch, now, sleep=sleep)),
            "season": season_projection(recs, st, model)}


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
    matches = set()
    for (lg, eid, aid), p in latest.items():
        if (lg, eid) not in actual:
            continue
        sot, mins = actual[(lg, eid)]
        ys.append(sot.get(aid, 0))
        mus.append(p["pred"])
        bs.append(p["baseline"])
        played += mins.get(aid, 0) > 0
        matches.add((lg, eid))
    if not ys:
        return {"n": 0}
    y, mu, b = np.array(ys, float), np.array(mus), np.array(bs)
    return {"n": len(ys), "matches": len(matches), "played": int(played), "predicted_total": round(float(mu.sum()), 1),
            "actual_total": int(y.sum()), "model": _metrics(y, mu), "baseline": _metrics(y, b)}


def load_forecasts(store: Store) -> list[dict]:
    return [json.loads(store.read_bytes(k)) for k in sorted(store.list_keys("forecasts/")) if k.endswith(".json")]


def main(argv=None) -> int:
    from .http import get_json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="storage root (default: PITCHLINE_ROOT)")
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
