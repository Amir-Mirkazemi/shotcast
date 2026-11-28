"""Player-level numbers built on the shot-level xG: xG, xA, finishing, percentile bars and similar players.

Everything here is computed per ESPN athlete id across all stored competitions. Per-90 rates use only matches
with a stored lineup, so a player's minutes and his shots always describe the same matches. Percentiles and
similarity use the pool of players with at least MIN_MINUTES of known minutes. There is no position data, so
the pool mixes forwards, midfielders and defenders; defenders rank low on every shooting measure.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

MIN_MINUTES = 1800            # 20 full matches
SIMILAR = 5
# label, key, format
BARS = (("Shots per 90", "shots90", "{:.2f}"), ("On target per 90", "sot90", "{:.2f}"),
        ("Goals per 90", "goals90", "{:.2f}"), ("Assists per 90", "assists90", "{:.2f}"),
        ("xG per 90", "xg90", "{:.2f}"), ("xA per 90", "xa90", "{:.2f}"))
SIM_KEYS = ("shots90", "sot90", "goals90", "assists90", "xg90", "xa90", "xg_per_shot", "header_share",
            "avg_dist")


def aggregate(scored: list[dict]) -> dict[str, dict]:
    """athlete id -> xG totals. Non-penalty xG and goals are the finishing pair (penalties are another skill).
    *_lc fields count only matches where the player has stored minutes."""
    a: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for s in scored:
        if s["aid"]:
            t = a[s["aid"]]
            t["shots"] += 1
            t["header"] += s["body"] == "header"
            t["dist_sum"] += s["dist"]
            if s["sit"] == "penalty":
                t["pens"] += 1
            else:
                t["xg"] += s["xg"]
                t["goals"] += s["goal"]
            if s["lc"]:
                t["shots_lc"] += 1
                t["xg_lc"] += 0 if s["sit"] == "penalty" else s["xg"]
        if s["assist_aid"] and s["sit"] != "penalty":
            t = a[s["assist_aid"]]
            t["xa"] += s["xg"]
            t["assisted"] += 1
            if s["assist_lc"]:
                t["xa_lc"] += s["xg"]
    return {k: dict(v) for k, v in a.items()}


def metrics(rows: list[dict], agg: dict[str, dict]) -> dict[str, dict]:
    """One dict per athlete (from combined leaderboard rows): totals and per-90 rates, None where unknown."""

    def per90(v: float, mins: float, ok: bool):
        return v * 90 / mins if ok else None

    out: dict[str, dict] = {}
    for r in rows:
        aid = r.get("athlete_id")
        if not aid:
            continue
        x, mins = agg.get(aid, {}), r["minutes"]
        ok = bool(r["lineup_matches"]) and mins >= MIN_MINUTES
        shots = x.get("shots", 0)
        out[aid] = {
            "shots90": per90(r["lc_shots"], mins, ok), "sot90": per90(r["lc_sot"], mins, ok),
            "goals90": per90(r["lc_goals"], mins, ok), "assists90": per90(r["lc_assists"], mins, ok),
            "xg90": per90(x.get("xg_lc", 0.0), mins, ok) if agg else None,
            "xa90": per90(x.get("xa_lc", 0.0), mins, ok) if agg else None,
            "xg_per_shot": (x.get("xg", 0.0) / max(shots - x.get("pens", 0), 1)) if shots and agg else None,
            "header_share": x["header"] / shots if shots else None,
            "avg_dist": x["dist_sum"] / shots if shots else None,
            "xg": x.get("xg", 0.0) if x else None, "goals_np": x.get("goals", 0.0) if x else None,
            "xa": x.get("xa", 0.0) if agg else None,
            "pens": int(x.get("pens", 0)), "located": int(shots), "ok": ok}
    return out


def percentile_bars(m: dict[str, dict]) -> dict[str, list[dict]]:
    """athlete id -> [{label, value, pct}] against the whole pool. Players outside the pool get no bars."""
    pool = {k: v for k, v in m.items() if v["ok"]}
    out: dict[str, list[dict]] = {}
    if len(pool) < 50:                         # a percentile among a handful of players means nothing
        return out
    for label, key, fmt in BARS:
        vals = np.array([v[key] for v in pool.values() if v[key] is not None])
        if len(vals) < 50:
            continue
        for aid, v in pool.items():
            if v[key] is None:
                continue
            below, equal = float((vals < v[key]).sum()), float((vals == v[key]).sum())
            out.setdefault(aid, []).append({"label": label, "value": fmt.format(v[key]),
                                            "pct": int(round(100 * (below + 0.5 * equal) / len(vals)))})
    return out


def similar_players(m: dict[str, dict], k: int = SIMILAR) -> dict[str, list[tuple[str, float]]]:
    """athlete id -> [(other id, similarity 0-100)]: cosine similarity of z-scored profiles in the pool."""
    ids = [a for a, v in m.items() if v["ok"] and all(v[key] is not None for key in SIM_KEYS)]
    if len(ids) < 50:
        return {}
    X = np.array([[m[a][key] for key in SIM_KEYS] for a in ids], float)
    sd = X.std(axis=0)
    Z = (X - X.mean(axis=0)) / np.where(sd > 0, sd, 1.0)
    norm = np.linalg.norm(Z, axis=1, keepdims=True)
    Z = Z / np.where(norm > 0, norm, 1.0)
    sim = Z @ Z.T
    np.fill_diagonal(sim, -2.0)
    out: dict[str, list[tuple[str, float]]] = {}
    for i, a in enumerate(ids):
        top = np.argpartition(-sim[i], k)[:k]
        top = top[np.argsort(-sim[i][top])]
        out[a] = [(ids[j], round(float(50 * (sim[i][j] + 1)), 0)) for j in top]       # cosine -1..1 -> 0..100
    return out


def profiles(rows: list[dict], scored: list[dict], links: dict[str, str]) -> dict[str, dict]:
    """athlete id -> everything the player page and compare page need beyond the basic totals."""
    agg = aggregate(scored)
    m = metrics(rows, agg)
    bars, sim = percentile_bars(m), similar_players(m)
    info = {r["athlete_id"]: r for r in rows if r.get("athlete_id")}
    pool = sum(1 for v in m.values() if v["ok"])
    out: dict[str, dict] = {}
    for aid, v in m.items():
        out[aid] = {
            "xg": None if v["xg"] is None else {"xg": v["xg"], "goals": v["goals_np"], "xa": v["xa"],
                                                "pens": v["pens"], "located": v["located"]},
            "bars": bars.get(aid, []),
            "similar": [{"name": info[o]["player"], "team": info[o]["team"], "sim": int(sc),
                         "url": links.get(o, "")} for o, sc in sim.get(aid, [])],
            "pool_min": MIN_MINUTES, "pool": pool, "in_pool": v["ok"]}
    return out
