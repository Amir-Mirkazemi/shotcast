from datetime import datetime, timezone

from pitchline.live import collect, parse_event


def ev(i, state, home="A", away="B", hs="1", as_="0"):
    return {"id": i, "date": "2026-11-24T20:00Z",
            "status": {"displayClock": "67'", "type": {"state": state, "shortDetail": "67'"}},
            "competitions": [{"competitors": [{"homeAway": "home", "score": hs, "team": {"displayName": home}},
                                              {"homeAway": "away", "score": as_, "team": {"displayName": away}}]}]}


def test_parse_event_and_bad_event():
    m = parse_event("eng.1", ev(5, "in"))
    assert m["home"] == "A" and m["home_score"] == "1" and m["state"] == "in" and m["league"] == "eng.1"
    assert parse_event("eng.1", {"id": 1}) is None


def test_collect_orders_live_first_and_survives_failures():
    def fetch(url):
        if "esp.1" in url:
            raise RuntimeError("boom")
        return {"events": [ev(1, "pre"), ev(2, "in")]}, {}
    out = collect(fetch, datetime(2026, 11, 24, tzinfo=timezone.utc), ["eng.1", "esp.1"], sleep=lambda s: None)
    assert out["failed"] == ["esp.1"]
    assert [m["id"] for m in out["matches"]] == ["2", "1"]
