"""Download raw StatsBomb open-data files into the store.

Design goals: safe to rerun (existing event files are skipped), polite to the source
(bounded workers, retries with backoff), and never write a bad file (validated before saving).
"""

from __future__ import annotations

import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

from .config import Competition
from .storage import Store

log = logging.getLogger("shotcast.ingest")

TIMEOUT = (5, 30)  # connect, read seconds
MAX_ATTEMPTS = 4


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    matches_listed: int = 0
    events_fetched: int = 0
    events_skipped: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def fetch_json(url: str, session=None, sleep=time.sleep, attempts: int = MAX_ATTEMPTS):
    """GET a URL and parse JSON, retrying transient failures with exponential backoff."""
    session = session or requests.Session()
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code == 404:
                raise IngestError(f"404 not found: {url}")  # not transient, do not retry
            resp.raise_for_status()
            return resp.json()
        except IngestError:
            raise
        except (requests.RequestException, ValueError) as err:
            last = err
            if attempt < attempts:
                delay = min(2**attempt + random.random(), 30)
                log.warning("attempt %d/%d failed for %s: %s; retrying in %.1fs",
                            attempt, attempts, url, err, delay)
                sleep(delay)
    raise IngestError(f"gave up after {attempts} attempts: {url}: {last}")


def _validate_events(data) -> None:
    if not isinstance(data, list) or not data:
        raise IngestError("events payload is empty or not a list")
    if not all(isinstance(e, dict) and "type" in e for e in data[:50]):
        raise IngestError("events payload does not look like StatsBomb events")


def ingest(
    store: Store,
    competitions: tuple[Competition, ...],
    base_url: str,
    *,
    session=None,
    sleep=time.sleep,
    workers: int = 4,
    limit: int | None = None,
) -> IngestResult:
    result = IngestResult()
    match_ids: list[int] = []

    for comp in competitions:
        url = f"{base_url}/matches/{comp.competition_id}/{comp.season_id}.json"
        try:
            matches = fetch_json(url, session, sleep)
        except IngestError as err:
            result.failures.append(f"matches {comp.name}: {err}")
            continue
        # The match list is small and can be corrected upstream, so always refresh it.
        # In sample mode (--limit) keep only the sampled matches, so the match list and the
        # event files always agree.
        if limit:
            matches = matches[:limit]
        key = f"raw/matches/{comp.competition_id}/{comp.season_id}.json"
        store.write_bytes(key, json.dumps(matches).encode())
        match_ids.extend(m["match_id"] for m in matches)
        result.matches_listed += len(matches)

    def one(match_id: int) -> str:
        key = f"raw/events/{match_id}.json"
        if store.exists(key):
            return "skipped"
        data = fetch_json(f"{base_url}/events/{match_id}.json", session, sleep)
        _validate_events(data)
        store.write_bytes(key, json.dumps(data).encode())
        return "fetched"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, mid): mid for mid in dict.fromkeys(match_ids)}
        for fut, mid in futures.items():
            try:
                outcome = fut.result()
            except Exception as err:  # keep going; report every failure at the end
                result.failures.append(f"events {mid}: {err}")
                continue
            if outcome == "fetched":
                result.events_fetched += 1
            else:
                result.events_skipped += 1
    return result
