"""Near-live scores for every competition: one small JSON file, refreshed by a scheduled job.

Run: python -m shotcast.live --out live.json
The job publishes the file to a branch called live-data; the Live page reads it from there.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .http import get_json

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
LEAGUES = ["eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "usa.1",
           "uefa.champions", "afc.champions", "fifa.world", "uefa.euro", "ksa.1"]
log = logging.getLogger("shotcast.live")


def parse_event(league: str, e: dict) -> dict | None:
    try:
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        st = e["status"]["type"]
        return {"league": league, "id": str(e["id"]), "kickoff": e["date"], "state": st.get("state", "pre"),
                "detail": st.get("shortDetail") or st.get("description") or "",
                "clock": e["status"].get("displayClock") or "",
                "home": teams["home"]["team"]["displayName"], "away": teams["away"]["team"]["displayName"],
                "home_score": teams["home"].get("score"), "away_score": teams["away"].get("score")}
    except (KeyError, IndexError, TypeError):
        return None


def day_events(fetch, league: str, start, days: int, sleep=time.sleep) -> list[dict]:
    """Scoreboard events for `days` single days from `start`. ESPN rejects multi-day ranges for current dates,
    so each day is its own request; events are de-duplicated by id."""
    seen: dict[str, dict] = {}
    for i in range(days):
        data, _ = fetch(f"{BASE}/{league}/scoreboard?dates={start + timedelta(days=i):%Y%m%d}&limit=300")
        for e in data.get("events", []):
            seen[str(e.get("id"))] = e
        sleep(0.3)
    return list(seen.values())


def collect(fetch=get_json, now: datetime | None = None, leagues=LEAGUES, sleep=time.sleep) -> dict:
    """Yesterday, today and tomorrow (UTC) per competition. A failing competition is listed, not fatal."""
    now = now or datetime.now(timezone.utc)
    matches, failed = [], []
    for lg in leagues:
        try:
            events = day_events(fetch, lg, (now - timedelta(days=1)).date(), 3, sleep)
        except Exception as err:   # noqa: BLE001 - one broken endpoint must not blank the whole page
            log.warning("%s failed: %s", lg, err)
            failed.append(lg)
            continue
        matches += [m for e in events if (m := parse_event(lg, e))]
    matches.sort(key=lambda m: (m["state"] != "in", m["kickoff"]))
    return {"updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "failed": failed, "matches": matches}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="live.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = collect()
    if len(data["failed"]) == len(LEAGUES):
        print("every competition failed; keeping the previous file", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{len(data['matches'])} matches, {len(data['failed'])} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
