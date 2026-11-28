"""Is the data healthy? One report, used by the Data health page and by the daily alert.

    python -m pitchline.health_check --max-days 4          # prints the report, exits 1 when data is stale
    python -m pitchline.health_check --publish-dbt dbt/target/run_results.json

"Stale" means no competition has a newer match than --max-days ago. Individual competitions go quiet in
the off-season, so they are shown on the page but do not trigger the alert on their own.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone

from .leaderboard import read_valid
from .logs import configure, read_runs
from .storage import Store, get_store

DBT_KEY = "health/dbt_espn.json"
LOAD_KEY = "health/warehouse_load.json"
log = logging.getLogger("pitchline.health")


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def _read_json(store: Store, key: str) -> dict | None:
    try:
        return json.loads(store.read_bytes(key)) if store.exists(key) else None
    except (ValueError, OSError):
        return None


def build_report(store: Store, recs: list[dict], invalid: list[dict], today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    by: dict[str, list[dict]] = {}
    for r in recs:
        by.setdefault(r["league"], []).append(r)
    leagues = []
    for lg, rs in sorted(by.items()):
        dates = sorted(r["date"][:10] for r in rs)
        shots = [s for r in rs for s in r.get("shots") or []]
        located = sum(1 for s in shots if s.get("x") is not None and (s.get("x"), s.get("y")) != (0, 0))
        leagues.append({
            "league": lg, "matches": len(rs), "first": dates[0], "last": dates[-1],
            "days_since": (today - date.fromisoformat(dates[-1])).days,
            "ok_pct": _pct(sum(1 for r in rs if r.get("coverage") == "ok"), len(rs)),
            "lineup_pct": _pct(sum(1 for r in rs if r.get("lineup")), len(rs)),
            "located_pct": _pct(located, len(shots)),
        })
    newest = max((x["last"] for x in leagues), default=None)
    reasons: dict[str, int] = {}
    for bad in invalid:
        for why in bad["reasons"][:1]:
            text = why.split(": ", 1)[-1][:90]
            reasons[text] = reasons.get(text, 0) + 1
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matches": len(recs), "newest": newest,
        "days_since_newest": (today - date.fromisoformat(newest)).days if newest else None,
        "leagues": leagues,
        "invalid": {"count": len(invalid), "reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:5],
                    "files": ["/".join(b["key"].split("/")[-2:]) for b in invalid[:5]]},
        "runs": read_runs(store, 12), "dbt": _read_json(store, DBT_KEY),
        "warehouse": _read_json(store, LOAD_KEY),
    }


def stale(report: dict, max_days: int) -> bool:
    d = report["days_since_newest"]
    return d is None or d > max_days


def problems(report: dict, max_days: int) -> list[str]:
    out = []
    if stale(report, max_days):
        out.append(f"no new matches for {report['days_since_newest']} days (limit {max_days})")
    if report["invalid"]["count"]:
        out.append(f"{report['invalid']['count']} match files fail the data contract")
    dbt = report.get("dbt")
    if dbt and dbt.get("tests_failed"):
        out.append("dbt tests failing: " + ", ".join(dbt["tests_failed"][:5]))
    return out


def publish_dbt(store: Store, run_results: str) -> dict:
    import tempfile

    from .health import summarize_dbt

    with tempfile.TemporaryDirectory() as tmp:
        summary = summarize_dbt(run_results, tmp)
    store.write_bytes(DBT_KEY, json.dumps(summary).encode())
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    ap.add_argument("--max-days", type=int, default=4)
    ap.add_argument("--publish-dbt", metavar="RUN_RESULTS")
    a = ap.parse_args(argv)
    configure()
    store = get_store(a.root)
    if a.publish_dbt:
        s = publish_dbt(store, a.publish_dbt)
        log.info("dbt summary stored", extra={"tests_passed": s["tests_passed"],
                                              "tests_total": s["tests_total"]})
    recs, invalid = read_valid(store)
    report = build_report(store, recs, invalid)
    found = problems(report, a.max_days)
    for p in found:
        log.error(p)
    log.info("health", extra={"matches": report["matches"], "newest": report["newest"],
                              "days_since_newest": report["days_since_newest"], "problems": len(found)})
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
