"""Shared polite HTTP helper: retries with exponential backoff, JSON only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class HttpStatus(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status


def get_json(url: str, headers: dict | None = None, opener=urllib.request.urlopen,
             tries: int = 4, sleep=time.sleep):
    """Return (parsed_json, response_headers). 4xx (except 429) fails fast; others retry."""
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": "shotcast/0.2", **(headers or {})})
        try:
            with opener(req, timeout=30) as r:
                return json.loads(r.read().decode()), r.headers
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise HttpStatus(e.code, url) from e
            err: Exception = e
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
        if attempt == tries - 1:
            raise err
        sleep(2 ** attempt)
