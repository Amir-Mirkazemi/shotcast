import json
import logging
from datetime import date, datetime, timezone

from shotcast.health_check import build_report, main, problems, publish_dbt, stale
from shotcast.logs import JsonFormatter, read_runs, record_run
from shotcast.storage import LocalStore


def rec(lg, eid, day, ok=True, shots=2, located=True, lineup=True):
    return {"league": lg, "event_id": eid, "date": f"{day}T14:00Z", "coverage": "ok" if ok else "mismatch",
            "teams": [{"name": "A", "home": True, "score": "1"}, {"name": "B", "home": False, "score": "0"}],
            "players": [], "lineup": [{"team": "A", "minutes": 90}] if lineup else [],
            "shots": [{"outcome": "saved", "x": 0.1 if located else None, "y": 0.5} for _ in range(shots)]}


def test_report_has_per_league_freshness_and_coverage(tmp_path):
    s = LocalStore(tmp_path)
    recs = [rec("eng.1", "1", "2025-09-01"), rec("eng.1", "2", "2025-09-10", ok=False, lineup=False),
            rec("esp.1", "3", "2025-08-01", located=False)]
    r = build_report(s, recs, [], today=date(2025, 9, 12))
    eng = next(x for x in r["leagues"] if x["league"] == "eng.1")
    assert (eng["matches"], eng["days_since"], eng["ok_pct"], eng["lineup_pct"]) == (2, 2, 50.0, 50.0)
    assert next(x for x in r["leagues"] if x["league"] == "esp.1")["located_pct"] == 0.0
    assert (r["newest"], r["days_since_newest"], r["matches"]) == ("2025-09-10", 2, 3)


def test_staleness_uses_the_newest_match_anywhere(tmp_path):
    s = LocalStore(tmp_path)
    r = build_report(s, [rec("eng.1", "1", "2025-09-01"), rec("esp.1", "2", "2025-09-10")], [], today=date(2025, 9, 12))
    assert not stale(r, 4)                                 # eng.1 is 11 days quiet, but esp.1 is fresh
    assert stale(r, 1)
    assert problems(r, 4) == []
    assert "no new matches" in problems(r, 1)[0]


def test_problems_report_invalid_files_and_failing_dbt_tests(tmp_path):
    s = LocalStore(tmp_path)
    bad = [{"key": "espn/matches/eng.1/9.json", "reasons": ["teams: expected 2 teams, found 1"]}]
    s.write_bytes("health/dbt_espn.json", json.dumps({"tests_total": 5, "tests_passed": 4,
                                                      "tests_failed": ["unique_x"]}).encode())
    r = build_report(s, [rec("eng.1", "1", "2025-09-10")], bad, today=date(2025, 9, 11))
    out = problems(r, 4)
    assert any("fail the data contract" in p for p in out) and any("unique_x" in p for p in out)
    assert r["invalid"]["files"] == ["eng.1/9.json"] and r["invalid"]["reasons"][0][0] == "expected 2 teams, found 1"


def test_run_records_round_trip_newest_first(tmp_path):
    s = LocalStore(tmp_path)
    record_run(s, "espn_backfill", {"new": 3}, now=datetime(2025, 9, 1, 1, 0, tzinfo=timezone.utc))
    record_run(s, "espn_backfill", {"new": 9}, now=datetime(2025, 9, 2, 1, 0, tzinfo=timezone.utc))
    runs = read_runs(s)
    assert [r["new"] for r in runs] == [9, 3] and runs[0]["kind"] == "espn_backfill"


def test_json_log_line_carries_extra_fields():
    rec_ = logging.LogRecord("shotcast.espn", logging.INFO, "f.py", 1, "done %s", ("eng.1",), None)
    rec_.league, rec_.failed = "eng.1", 2
    out = json.loads(JsonFormatter().format(rec_))
    assert out["msg"] == "done eng.1" and out["league"] == "eng.1" and out["failed"] == 2 and out["level"] == "INFO"


def test_publish_dbt_stores_a_summary(tmp_path):
    s = LocalStore(tmp_path / "d")
    rr = tmp_path / "run_results.json"
    rr.write_text(json.dumps({"metadata": {"generated_at": "t"}, "elapsed_time": 1.0, "results": [
        {"unique_id": "model.p.m", "status": "success"},
        {"unique_id": "test.p.ok.abc", "status": "pass"}, {"unique_id": "test.p.bad.abc", "status": "fail"}]}))
    summary = publish_dbt(s, str(rr))
    assert summary["tests_total"] == 2 and summary["tests_failed"] == ["bad"]
    assert json.loads(s.read_bytes("health/dbt_espn.json"))["tests_passed"] == 1


def test_main_exits_nonzero_when_stale_and_zero_when_fresh(tmp_path):
    s = LocalStore(tmp_path)
    s.write_bytes("espn/matches/eng.1/1.json", json.dumps(rec("eng.1", "1", "2020-01-01")).encode())
    assert main(["--root", str(tmp_path), "--max-days", "4"]) == 1
    today = datetime.now(timezone.utc).date().isoformat()
    s.write_bytes("espn/matches/eng.1/2.json", json.dumps(rec("eng.1", "2", today)).encode())
    assert main(["--root", str(tmp_path), "--max-days", "4"]) == 0
