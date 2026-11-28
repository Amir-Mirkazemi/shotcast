import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from shotcast import playerstats as ps
from shotcast import site
from shotcast.storage import LocalStore


def shot(aid, xg, goal=False, sit="open_play", body="right_foot", assist=None, lc=True, dist=12.0):
    return {"aid": aid, "assist_aid": assist, "xg": xg, "goal": goal, "sit": sit, "body": body, "lc": lc,
            "assist_lc": True, "dist": dist}


def test_aggregate_excludes_penalties_from_the_finishing_pair_and_credits_assists():
    scored = [shot("1", 0.2, True, assist="2"), shot("1", 0.1), shot("1", 0.76, True, sit="penalty"),
              shot("1", 0.3, body="header", lc=False)]
    a = ps.aggregate(scored)
    assert a["1"]["shots"] == 4 and a["1"]["pens"] == 1
    assert a["1"]["xg"] == pytest.approx(0.6) and a["1"]["goals"] == 1            # the penalty is in neither
    assert a["1"]["xg_lc"] == pytest.approx(0.3) and a["1"]["header"] == 1        # the unplayed-minutes shot not in *_lc
    assert a["2"]["xa"] == pytest.approx(0.2) and a["2"]["assisted"] == 1


def pool(n=80, seed=0):
    rng = np.random.default_rng(seed)
    rows, m = [], {}
    for i in range(n):
        v = {k: float(rng.gamma(2, 0.3)) for k in ps.SIM_KEYS}
        v.update({"xg": 3.0, "goals_np": 2.0, "xa": 1.0, "pens": 0, "located": 40, "ok": True})
        m[str(i)] = v
        rows.append({"athlete_id": str(i), "player": f"P{i}", "team": "T"})
    return rows, m


def test_percentiles_rank_the_pool_and_need_a_real_pool():
    rows, m = pool()
    m["0"]["shots90"], m["1"]["shots90"] = 99.0, 0.0
    bars = ps.percentile_bars(m)
    top = next(b for b in bars["0"] if b["label"] == "Shots per 90")
    low = next(b for b in bars["1"] if b["label"] == "Shots per 90")
    assert top["pct"] >= 98 and low["pct"] <= 2 and top["value"] == "99.00"
    assert ps.percentile_bars({k: v for k, v in list(m.items())[:20]}) == {}        # 20 players is not a pool
    m["5"]["ok"] = False
    assert "5" not in ps.percentile_bars(m)                                          # too few minutes: no bars


def test_similar_players_finds_the_near_twin_and_never_lists_himself():
    rows, m = pool()
    m["7"] = dict(m["3"])
    for k in ps.SIM_KEYS:
        m["7"][k] = m["3"][k] * 1.01
    sim = ps.similar_players(m)
    assert sim["3"][0][0] == "7" and sim["3"][0][1] >= 99
    assert all(o != a for a, lst in sim.items() for o, _ in lst) and all(len(lst) == ps.SIMILAR for lst in sim.values())
    assert all(a <= b for lst in sim.values() for (_, a), (_, b) in zip(lst[1:], lst, strict=False))   # best first
    m["9"]["xg90"] = None
    assert "9" not in ps.similar_players(m)                                          # incomplete profile: not compared


def test_profiles_without_xg_still_give_bars_but_no_xg_or_similar_players():
    rows = [{"athlete_id": str(i), "player": f"P{i}", "team": "T", "minutes": 2000, "lineup_matches": 25,
             "lc_shots": 20 + i, "lc_sot": 8 + i % 7, "lc_goals": i % 5, "lc_assists": i % 3} for i in range(80)]
    out = ps.profiles(rows, [], {})
    p = out["10"]
    assert p["xg"] is None and p["similar"] == [] and p["in_pool"]
    assert {b["label"] for b in p["bars"]} == {"Shots per 90", "On target per 90", "Goals per 90", "Assists per 90"}


# ------------------------------------------------------------- end to end through the site build
def league_store(tmp_path, n_matches=340, seed=5):
    """Enough matches, shots and minutes for xG, percentiles and similar players to switch on."""
    rng = np.random.default_rng(seed)
    teams = [f"Club {c}" for c in "ABCDEFGH"]
    skill = {(t, i): (rng.gamma(2, .5), rng.uniform(.04, .16)) for t in teams for i in range(11)}
    s, start = LocalStore(tmp_path / "data"), datetime(2025, 8, 1)
    for m in range(n_matches):
        h, a = rng.choice(len(teams), 2, replace=False)
        players, lineup, shots = [], [], []
        for t in (teams[h], teams[a]):
            for i in range(11):
                aid, name = f"{teams.index(t)}{i:02d}", f"{t[-1]}{i} Player"
                volume, quality = skill[(t, i)]
                n = int(rng.poisson(volume))
                goals = sot = 0
                for _ in range(n):
                    x, y = float(rng.uniform(75, 99)), float(rng.uniform(25, 75))
                    out = "goal" if rng.random() < quality else rng.choice(["saved", "off_target", "blocked"])
                    goals += out == "goal"
                    sot += out in ("goal", "saved")
                    shots.append({"team": t, "player": name, "x": x, "y": y, "outcome": str(out),
                                  "body_part": "header" if rng.random() < .15 else "right_foot",
                                  "situation": "open_play", "seconds": 600, "assist_player": None,
                                  "on_target": out in ("goal", "saved")})
                players.append({"team": t, "athlete_id": aid, "player": name, "shots": n, "shots_on_target": sot,
                                "goals": int(goals), "assists": 0, "own_goals": 0})
                lineup.append({"team": t, "athlete_id": aid, "player": name, "minutes": 90, "starter": True})
        rec = {"league": "eng.1", "event_id": str(m), "coverage": "ok",
               "date": (start + timedelta(days=m // 3)).isoformat() + "T15:00Z",
               "teams": [{"name": teams[h], "home": True, "score": "1"}, {"name": teams[a], "home": False, "score": "0"}],
               "players": players, "lineup": lineup, "shots": shots}
        s.write_bytes(f"espn/matches/eng.1/{m}.json", json.dumps(rec).encode())
    return s


def test_site_build_publishes_xg_percentiles_and_similar_players(tmp_path):
    s = league_store(tmp_path)
    out = tmp_path / "site"
    site.build(s, out)
    method = (out / "method.html").read_text()
    assert "Expected goals (xG)" in method and "(used)" in method and "Said (avg chance)" in method
    page = next((out).glob("p-eng.1-*.html")).read_text()
    for needle in ("xG, no penalties", "Finishing (goals - xG)", "How he compares", "Similar players", "xG per 90"):
        assert needle in page, needle
    idx = json.loads((out / "compare.json").read_text())
    assert idx and all("xg" in p and "xa" in p for p in idx)


def test_site_build_without_enough_shots_hides_xg_and_says_why(tmp_path):
    s = league_store(tmp_path, n_matches=40)
    out = tmp_path / "site"
    site.build(s, out)
    assert "Not published yet: needs at least 5,000 shots" in (out / "method.html").read_text()
    for page in out.glob("p-eng.1-*.html"):
        assert "xG, no penalties" not in page.read_text()
