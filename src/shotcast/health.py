"""Run history and dbt test summaries, written as small JSON files the app can read."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def log_run(health_dir: str | Path, step: str, payload: dict) -> dict:
    health = Path(health_dir)
    health.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step, **payload}
    with (health / "runs.jsonl").open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def summarize_dbt(run_results: str | Path, out_dir: str | Path) -> dict:
    data = json.loads(Path(run_results).read_text())
    results = data.get("results", [])
    tests = [r for r in results if r["unique_id"].startswith("test.")]
    summary = {
        "generated_at": data.get("metadata", {}).get("generated_at"),
        "elapsed_seconds": data.get("elapsed_time"),
        "models_ok": sum(
            1 for r in results if r["unique_id"].startswith("model.") and r["status"] == "success"
        ),
        "tests_total": len(tests),
        "tests_passed": sum(1 for r in tests if r["status"] == "pass"),
        "tests_failed": [r["unique_id"].split(".")[2] for r in tests if r["status"] in ("fail", "error")],
        "tests_warned": [r["unique_id"].split(".")[2] for r in tests if r["status"] == "warn"],
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dbt_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
