import json

import pytest
import requests
from conftest import FakeResponse, FakeSession, make_events

from shotcast.config import Competition
from shotcast.ingest import IngestError, fetch_json, ingest
from shotcast.storage import LocalStore

COMP = (Competition(99, 1, "Test Cup"),)
BASE = "http://x/data"
NOSLEEP = lambda _s: None  # noqa: E731


def routes(match_list):
    return {
        "/matches/99/1.json": FakeResponse(match_list),
        "/events/1.json": FakeResponse(make_events()),
        "/events/2.json": FakeResponse(make_events()),
    }


def test_ingest_writes_raw_files(tmp_path, match_list):
    store = LocalStore(tmp_path)
    res = ingest(store, COMP, BASE, session=FakeSession(routes(match_list)), sleep=NOSLEEP)
    assert res.ok and res.matches_listed == 2 and res.events_fetched == 2
    assert store.exists("raw/matches/99/1.json") and store.exists("raw/events/2.json")


def test_second_run_skips_existing_events(tmp_path, match_list):
    store = LocalStore(tmp_path)
    ingest(store, COMP, BASE, session=FakeSession(routes(match_list)), sleep=NOSLEEP)
    sess = FakeSession(routes(match_list))
    res = ingest(store, COMP, BASE, session=sess, sleep=NOSLEEP)
    assert res.events_fetched == 0 and res.events_skipped == 2
    assert not any("/events/" in u for u in sess.calls), "no event downloads on a rerun"


def test_limit_caps_matches(tmp_path, match_list):
    store = LocalStore(tmp_path)
    res = ingest(store, COMP, BASE, session=FakeSession(routes(match_list)),
                 sleep=NOSLEEP, limit=1)
    assert res.events_fetched == 1 and res.matches_listed == 1
    saved = json.loads(store.read_bytes("raw/matches/99/1.json"))
    assert [m["match_id"] for m in saved] == [1], "sample mode keeps match list and events consistent"


def test_retries_then_succeeds():
    sess = FakeSession({"/a.json": [requests.Timeout("slow"), requests.Timeout("slow"), FakeResponse([1])]})
    sleeps = []
    assert fetch_json("http://x/a.json", sess, sleeps.append) == [1]
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] - 1  # backoff grows


def test_gives_up_after_max_attempts():
    sess = FakeSession({"/a.json": requests.Timeout("slow")})
    with pytest.raises(IngestError, match="gave up after 3 attempts"):
        fetch_json("http://x/a.json", sess, NOSLEEP, attempts=3)
    assert len(sess.calls) == 3


def test_404_is_not_retried():
    sess = FakeSession({})
    with pytest.raises(IngestError, match="404"):
        fetch_json("http://x/missing.json", sess, NOSLEEP)
    assert len(sess.calls) == 1


def test_failure_is_reported_and_no_bad_file_written(tmp_path, match_list):
    r = routes(match_list)
    r["/events/2.json"] = FakeResponse([])  # empty payload must be rejected
    store = LocalStore(tmp_path)
    res = ingest(store, COMP, BASE, session=FakeSession(r), sleep=NOSLEEP)
    assert not res.ok and any("events 2" in f for f in res.failures)
    assert store.exists("raw/events/1.json") and not store.exists("raw/events/2.json")


def test_invalid_json_body_is_retried_then_fails():
    sess = FakeSession({"/a.json": FakeResponse(bad_json=True)})
    with pytest.raises(IngestError):
        fetch_json("http://x/a.json", sess, NOSLEEP, attempts=2)
