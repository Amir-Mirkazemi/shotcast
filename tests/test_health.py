import json

from shotcast.health import log_run, summarize_dbt


def test_log_run_appends_lines(tmp_path):
    log_run(tmp_path, "ingest", {"fetched": 3})
    log_run(tmp_path, "bronze", {"built": 3})
    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert [json.loads(x)["step"] for x in lines] == ["ingest", "bronze"]


def test_summarize_dbt(tmp_path):
    rr = {"metadata": {"generated_at": "t"}, "elapsed_time": 2.5, "results": [
        {"unique_id": "model.p.a", "status": "success"},
        {"unique_id": "test.p.not_null_a", "status": "pass"},
        {"unique_id": "test.p.goals_reconcile", "status": "fail"},
        {"unique_id": "test.p.minutes_close", "status": "warn"},
    ]}
    f = tmp_path / "rr.json"
    f.write_text(json.dumps(rr))
    s = summarize_dbt(f, tmp_path)
    assert s["tests_total"] == 3 and s["tests_passed"] == 1
    assert s["tests_failed"] == ["goals_reconcile"] and s["tests_warned"] == ["minutes_close"]
    assert (tmp_path / "dbt_summary.json").exists()
