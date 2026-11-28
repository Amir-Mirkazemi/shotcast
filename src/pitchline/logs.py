"""Structured logging and per-run records.

JSON log lines (one object per line) in CI or when PITCHLINE_LOG_FORMAT=json, so a log search can
filter on fields such as ``league`` or ``failed``. Plain text otherwise. Every pipeline run also writes a
small summary file to ``runs/`` in storage, which the Data health page reads.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .storage import Store

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
RUNS = "runs/"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="seconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        out.update({k: v for k, v in record.__dict__.items() if k not in _STD})     # fields passed via extra=
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: int = logging.INFO, json_logs: bool | None = None) -> None:
    if json_logs is None:
        json_logs = os.environ.get("PITCHLINE_LOG_FORMAT") == "json" or bool(os.environ.get("CI"))
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_logs
                         else logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def record_run(store: Store, kind: str, payload: dict, now: datetime | None = None) -> str:
    """Write runs/{timestamp}-{kind}.json. Returns the key."""
    now = now or datetime.now(timezone.utc)
    key = f"{RUNS}{now:%Y%m%dT%H%M%S}-{kind}.json"
    body = {"ts": now.isoformat(timespec="seconds"), "kind": kind, **payload}
    store.write_bytes(key, json.dumps(body).encode())
    return key


def read_runs(store: Store, limit: int = 12) -> list[dict]:
    """The newest run records, newest first."""
    keys = sorted(k for k in store.list_keys(RUNS) if k.endswith(".json"))[-limit:]
    return [json.loads(store.read_bytes(k)) for k in reversed(keys)]
