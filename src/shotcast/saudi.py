"""Saudi Pro League backfill from API-Football (free plan: seasons 2022-2024).

Resumable and quota-aware: stops before the daily limit, never re-fetches a saved match.
Run:  python -m shotcast.saudi         (env APIFOOTBALL_KEY, SHOTCAST_ROOT)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

from .http import get_json
from .storage import Store, get_store

BASE = "https://v3.football.api-sports.io"
LEAGUE = 307
SEASONS = [2022, 2023, 2024]
DAILY_BUDGET = 90
log = logging.getLogger("shotcast.saudi")


def run(store: Store, key: str, opener=None, budget: int = DAILY_BUDGET, sleep=time.sleep) -> bool:
    kw = {"opener": opener} if opener else {}
    hdr = {"x-apisports-key": key}
    used = 0
    for season in SEASONS:
        fx_key = f"saudi/fixtures_{season}.json"
        if not store.exists(fx_key):
            data, _ = get_json(f"{BASE}/fixtures?league={LEAGUE}&season={season}&status=FT", hdr, **kw)
            used += 1
            if data.get("errors") or not data.get("response"):
                log.error("season %s: cannot read fixtures: %s", season, data.get("errors"))
                continue
            store.write_bytes(fx_key, json.dumps(data["response"]).encode())
        fixtures = json.loads(store.read_bytes(fx_key))
        todo = [f for f in fixtures if not store.exists(f"saudi/{season}_{f['fixture']['id']}.json")]
        log.info("season %s: %d/%d matches saved", season, len(fixtures) - len(todo), len(fixtures))
        for f in todo:
            if used >= budget:
                log.info("daily budget reached (%d requests); resume next run", used)
                return False
            fid = f["fixture"]["id"]
            data, headers = get_json(f"{BASE}/fixtures/players?fixture={fid}", hdr, **kw)
            used += 1
            resp = data.get("response")
            if data.get("errors") or not isinstance(resp, list) or len(resp) != 2:
                log.warning("fixture %s: bad response %s; skipped for now", fid, data.get("errors"))
            else:
                rec = {"fixture": f["fixture"], "league": f["league"], "teams": f["teams"],
                       "goals": f["goals"], "players": resp}
                store.write_bytes(f"saudi/{season}_{fid}.json", json.dumps(rec).encode())
            left = headers.get("x-ratelimit-requests-remaining") if headers else None
            if left is not None and int(left) <= 3:
                log.info("API quota nearly used; resume next run")
                return False
            sleep(7)
    log.info("all Saudi seasons complete")
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        sys.exit("APIFOOTBALL_KEY is not set")
    run(get_store(None), key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
