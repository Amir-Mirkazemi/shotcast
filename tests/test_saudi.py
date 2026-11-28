import io
import json

from shotcast import saudi
from shotcast.storage import LocalStore


class R(io.BytesIO):
    def __init__(self, body, remaining="50"):
        super().__init__(body)
        self.headers = {"x-ratelimit-requests-remaining": remaining}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def make_opener(calls):
    def opener(req, timeout=0):
        calls.append(req.full_url)
        if "/fixtures?" in req.full_url:
            fx = [{"fixture": {"id": i}, "league": {}, "teams": {}, "goals": {}} for i in (1, 2, 3)]
            return R(json.dumps({"errors": [], "response": fx}).encode())
        return R(json.dumps({"errors": [], "response": [{"team": 1}, {"team": 2}]}).encode())
    return opener


def test_budget_stop_then_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(saudi, "SEASONS", [2022])
    store, calls = LocalStore(tmp_path), []
    assert saudi.run(store, "k", make_opener(calls), budget=3, sleep=lambda s: None) is False
    assert len(store.list_keys("saudi")) == 3          # fixtures list + 2 matches
    calls.clear()
    assert saudi.run(store, "k", make_opener(calls), budget=90, sleep=lambda s: None) is True
    assert len(calls) == 1                              # resumed: only the missing match


def test_bad_response_is_skipped_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(saudi, "SEASONS", [2022])

    def opener(req, timeout=0):
        if "/fixtures?" in req.full_url:
            fx = [{"fixture": {"id": 1}, "league": {}, "teams": {}, "goals": {}}]
            return R(json.dumps({"errors": [], "response": fx}).encode())
        return R(json.dumps({"errors": {"rate": "limit"}, "response": []}).encode())

    store = LocalStore(tmp_path)
    saudi.run(store, "k", opener, sleep=lambda s: None)
    assert not store.exists("saudi/2022_1.json")
