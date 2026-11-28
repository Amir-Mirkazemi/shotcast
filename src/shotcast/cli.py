from __future__ import annotations

import argparse
import logging
import os
import sys

from . import health
from .bronze import build_bronze
from .config import load_sources
from .ingest import ingest
from .storage import get_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shotcast")
    ap.add_argument("command", choices=["ingest", "bronze", "dbt-summary", "all"])
    ap.add_argument("--root", help="data root: a folder or s3://bucket/prefix (env SHOTCAST_ROOT)")
    ap.add_argument("--config", help="path to sources.yml")
    ap.add_argument("--limit", type=int, help="max matches per competition (for quick runs)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild bronze even if files exist")
    ap.add_argument("--health-dir", default=os.environ.get("SHOTCAST_HEALTH", "health"))
    ap.add_argument("--dbt-results", default="dbt/target/run_results.json")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    store = get_store(args.root)
    sources = load_sources(args.config) if args.config else load_sources()
    rc = 0

    if args.command in ("ingest", "all"):
        res = ingest(store, sources.competitions, sources.base_url, limit=args.limit)
        logging.info("ingest: %d matches listed, %d fetched, %d skipped, %d failed",
                     res.matches_listed, res.events_fetched, res.events_skipped, len(res.failures))
        for f in res.failures:
            logging.error(f)
        health.log_run(args.health_dir, "ingest", {
            "matches_listed": res.matches_listed, "fetched": res.events_fetched,
            "skipped": res.events_skipped, "failures": len(res.failures)})
        rc = rc or (1 if res.failures else 0)

    if args.command in ("bronze", "all") and rc == 0:
        b = build_bronze(store, sources.competitions, rebuild=args.rebuild)
        logging.info("bronze: %d matches, %d built, %d skipped, %d missing raw",
                     b.matches, b.events_built, b.events_skipped, len(b.missing_raw))
        health.log_run(args.health_dir, "bronze", {
            "matches": b.matches, "built": b.events_built, "skipped": b.events_skipped,
            "missing_raw": len(b.missing_raw)})

    if args.command == "dbt-summary":
        s = health.summarize_dbt(args.dbt_results, args.health_dir)
        logging.info("dbt: %d/%d tests passed", s["tests_passed"], s["tests_total"])
        rc = 1 if s["tests_failed"] else 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
