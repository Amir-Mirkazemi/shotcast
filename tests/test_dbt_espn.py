"""Integration test: load fixture matches into DuckDB, then run the real dbt models and tests on them."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
DBT = shutil.which("dbt")
pytestmark = pytest.mark.skipif(DBT is None, reason="dbt is not installed")

from pitchline.storage import LocalStore  # noqa: E402
from pitchline.warehouse_espn import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def match(eid, date, goals_x=1, minutes=90):
    return {"league": "eng.1", "event_id": eid, "date": date, "coverage": "ok", "season": 2026, "schema": 2,
            "teams": [{"name": "Arsenal", "home": True, "score": "2"}, {"name": "Chelsea", "home": False, "score": "1"}],
            "players": [
                {"team": "Arsenal", "player": "Saka", "athlete_id": "11", "shots": 4, "shots_on_target": 2,
                 "goals": goals_x, "assists": 1, "own_goals": 0},
                {"team": "Chelsea", "player": "Palmer", "athlete_id": "22", "shots": 3, "shots_on_target": 1,
                 "goals": 1, "assists": 0, "own_goals": 0}],
            "lineup": [{"team": "Arsenal", "athlete_id": "11", "player": "Saka", "starter": True, "minutes": minutes},
                       {"team": "Chelsea", "athlete_id": "22", "player": "Palmer", "starter": True, "minutes": 90}],
            "shots": [{"play_id": "a", "team": "Arsenal", "player": "Saka", "outcome": "goal", "on_target": True,
                       "x": 0.1, "y": 0.5, "seconds": 600, "body_part": "right_foot", "situation": "open_play"}]}


def run_dbt(db, tmp_path, *args):
    env = {**os.environ, "PITCHLINE_WAREHOUSE": db, "DBT_PROFILES_DIR": str(ROOT / "dbt")}
    return subprocess.run([DBT, *args, "--project-dir", str(ROOT / "dbt"), "--target-path", str(tmp_path / "t"),
                           "--log-path", str(tmp_path / "l")], env=env, capture_output=True, text=True)


def fill(tmp_path, matches):
    s = LocalStore(tmp_path / "d")
    for m in matches:
        s.write_bytes(f"espn/matches/{m['league']}/{m['event_id']}.json", json.dumps(m).encode())
    db = str(tmp_path / "w.duckdb")
    load(s, db)
    return db


def test_models_build_and_every_test_passes(tmp_path):
    db = fill(tmp_path, [match("1", "2025-09-01T14:00Z"), match("2", "2025-09-08T14:00Z"),
                         match("3", "2026-02-01T14:00Z")])
    res = run_dbt(db, tmp_path, "build", "--select", "tag:espn")
    assert res.returncode == 0, res.stdout[-2500:]
    con = duckdb.connect(db, read_only=True)
    rows = con.execute("select season, matches, goals, minutes from main.mart_espn_player_season "
                       "where athlete_id = '11' order by season").fetchall()
    assert rows == [("2025-26", 3, 3, 270)]
    assert con.execute("select current_team from main.dim_espn_player where athlete_id = '22'").fetchone() == ("Chelsea",)
    con.close()


def test_a_data_test_fails_when_the_data_is_wrong(tmp_path):
    db = fill(tmp_path, [match("1", "2025-09-01T14:00Z", goals_x=5)])      # 5 goals, but the team scored 2
    res = run_dbt(db, tmp_path, "build", "--select", "tag:espn")
    assert res.returncode != 0
    assert "assert_espn_player_goals_do_not_exceed_team_score" in res.stdout
