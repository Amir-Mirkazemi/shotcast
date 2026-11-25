import json

from pitchline import site
from pitchline.storage import LocalStore


def _put(store, league, eid, date, players, coverage="ok", lineup=None):
    rec = {"league": league, "event_id": eid, "date": date, "coverage": coverage, "players": players,
           "lineup": lineup or []}
    store.write_bytes(f"espn/matches/{league}/{eid}.json", json.dumps(rec).encode())


def _p(team, name, shots, sot, goals=0, assists=0, aid=""):
    return {"team": team, "athlete_id": aid, "player": name, "shots": shots, "shots_on_target": sot,
            "goals": goals, "assists": assists, "own_goals": 0}


def test_build_writes_pages_escapes_names_and_lists_every_league(tmp_path):
    s = LocalStore(tmp_path / "data")
    _put(s, "eng.1", "1", "2025-08-16",
         [_p("A", "Erling <b>Haaland</b>", 4, 3, goals=1, aid="1"), _p("B", "José Ñ", 5, 1, aid="2")],
         lineup=[{"team": "A", "athlete_id": "1", "player": "Erling <b>Haaland</b>", "minutes": 90}])
    _put(s, "esp.1", "9", "2025-08-17", [_p("C", "Zed", 9, 9, aid="3")], coverage="mismatch")
    out = tmp_path / "site"
    site.build(s, out, top_n=10)
    names = {p.name for p in out.iterdir()}
    assert {"index.html", "method.html", "eng.1.html", "esp.1.html", "players.json", "style.css",
            "app.js", ".nojekyll"} <= names
    index = (out / "index.html").read_text()
    assert "Premier League" in index and "La Liga" in index
    league = (out / "eng.1.html").read_text()
    assert "<b>Haaland" not in league and "&lt;b&gt;Haaland" in league       # names are escaped
    assert "José Ñ" in league
    data = json.loads((out / "players.json").read_text())
    haaland = next(p for p in data if p["player"].startswith("Erling"))
    assert haaland["rank"] == 1 and haaland["field"] == 2 and haaland["minutes"] == 90
    jose = next(p for p in data if p["player"] == "José Ñ")
    assert jose["minutes"] == "-" and jose["rank"] == 2                        # no lineup stored: shown as "-"
    method = (out / "method.html").read_text()
    assert "50.0%" in method or "100.0%" in method                            # reconcile rate per league


def test_build_refuses_an_empty_store(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        site.build(LocalStore(tmp_path / "empty"), tmp_path / "site")
