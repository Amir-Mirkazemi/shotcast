import json
from datetime import date
from itertools import chain, repeat

from pitchline import espn_backfill as eb
from pitchline.http import HttpStatus
from pitchline.storage import LocalStore


def _shot(pid, team, ptype, text="Attempt missed. A (T) right footed shot from outside the box is too high."):
    return {"text": text, "play": {"id": pid, "type": {"text": ptype}, "text": text, "period": {"number": 1},
            "clock": {"value": 60.0}, "team": {"displayName": team},
            "participants": [{"athlete": {"displayName": "A"}}],
            "fieldPositionX": 85.0, "fieldPositionY": 50.0, "goalPositionY": 50.0}}


def _summary(n_home, n_home_on, boxscore_total=None):
    shots = [_shot(str(i), "Home", "Shot On Target" if i < n_home_on else "Shot Off Target")
             for i in range(n_home)]
    stats = [{"name": "totalShots", "displayValue": str(boxscore_total or n_home)},
             {"name": "shotsOnTarget", "displayValue": str(n_home_on)},
             {"name": "blockedShots", "displayValue": "0"}]
    return {"commentary": shots, "boxscore": {"teams": [{"team": {"displayName": "Home"}, "statistics": stats}]}}


def _event(eid="1"):
    return {"id": eid, "date": "2024-01-01T12:00Z", "status": {"type": {"completed": True}},
            "competitions": [{"competitors": [{"team": {"displayName": "Home"}, "homeAway": "home", "score": "1"}]}]}


def make_fetch(summary, log=None):
    def fetch(url):
        if log is not None:
            log.append(url)
        if "scoreboard" in url:
            return {"events": [_event("1"), {**_event("2"), "status": {"type": {"completed": False}}}]}, {}
        return summary, {}
    return fetch


def test_backfill_stores_slim_record_and_reconciles(tmp_path):
    store = LocalStore(tmp_path)
    st = eb.backfill(store, "eng.1", date(2024, 1, 1), date(2024, 1, 1), make_fetch(_summary(5, 2)),
                     sleep=lambda s: None)
    assert st["listed"] == 1 and st["new"] == 1 and st["ok"] == 1   # unfinished match ignored
    assert store.exists("espn/matches/eng.1/1.json")


def test_mismatch_is_flagged_not_hidden(tmp_path):
    store = LocalStore(tmp_path)
    st = eb.backfill(store, "eng.1", date(2024, 1, 1), date(2024, 1, 1),
                     make_fetch(_summary(5, 2, boxscore_total=9)), sleep=lambda s: None)
    assert st["mismatch"] == 1


def test_resume_skips_saved_matches(tmp_path):
    store, log = LocalStore(tmp_path), []
    args = (store, "eng.1", date(2024, 1, 1), date(2024, 1, 1))
    eb.backfill(*args, make_fetch(_summary(3, 1)), sleep=lambda s: None)
    eb.backfill(*args, make_fetch(_summary(3, 1), log), sleep=lambda s: None)
    assert not any("summary" in u for u in log)


def test_window_rejected_falls_back_to_single_days(tmp_path):
    seen = []

    def fetch(url):
        seen.append(url)
        if "-" in url.split("dates=")[1].split("&")[0]:
            raise HttpStatus(400, url)
        return {"events": []}, {}

    eb.list_events("eng.1", date(2024, 1, 1), date(2024, 1, 3), 7, fetch, lambda s: None)
    singles = [u for u in seen if "-" not in u.split("dates=")[1].split("&")[0]]
    assert len(singles) == 3


def test_time_budget_stops_cleanly(tmp_path):
    ticks = chain([0], repeat(999999))
    st = eb.backfill(LocalStore(tmp_path), "eng.1", date(2024, 1, 1), date(2024, 1, 1),
                     make_fetch(_summary(3, 1)), sleep=lambda s: None, max_minutes=1,
                     clock=lambda: next(ticks))
    assert st["stopped_early"] is True and st["new"] == 0


def test_retry_bad_refetches_only_non_ok(tmp_path):
    store = LocalStore(tmp_path)
    args = (store, "eng.1", date(2024, 1, 1), date(2024, 1, 1))
    eb.backfill(*args, make_fetch(_summary(5, 2, boxscore_total=9)), sleep=lambda s: None)  # mismatch
    st = eb.backfill(*args, make_fetch(_summary(5, 2)), sleep=lambda s: None, retry_bad=True)
    assert st["new"] == 1 and st["ok"] == 1                                               # refetched, now ok
    log = []
    eb.backfill(*args, make_fetch(_summary(5, 2), log), sleep=lambda s: None, retry_bad=True)
    assert not any("summary" in u for u in log)                                           # ok -> not refetched


def test_mismatch_detail_names_teams_and_counts():
    shots = [{"team": "Home", "outcome": "saved", "on_target": True}]
    detail = []
    tot = {"Home": {"totalShots": "4", "shotsOnTarget": "2", "blockedShots": "1"}}
    assert eb.reconcile(shots, tot, detail) == "mismatch"
    assert "parsed total=1" in detail[0] and "boxscore total=4" in detail[0]


def test_on_target_difference_alone_is_not_a_mismatch():
    # ESPN counts some blocked shots as on target; commentary says only "blocked".
    shots = [{"team": "Home", "outcome": "blocked", "on_target": False}]
    tot = {"Home": {"totalShots": "1", "shotsOnTarget": "1", "blockedShots": "1"}}
    assert eb.reconcile(shots, tot) == "ok"
    assert eb.on_target_gap(shots, tot) == 1


def test_player_stats_from_espn_are_stored(tmp_path):
    summary = _summary(2, 1)
    summary["rosters"] = [{"team": {"displayName": "Home"}, "roster": [
        {"athlete": {"displayName": "A"}, "stats": [{"name": "totalShots", "value": 2.0},
                                                    {"name": "shotsOnTarget", "value": 2.0}]},
        {"athlete": {"displayName": "Bench"}, "stats": [{"name": "totalShots", "value": 0.0}]}]}]
    store = LocalStore(tmp_path)
    eb.backfill(store, "eng.1", date(2024, 1, 1), date(2024, 1, 1), make_fetch(summary), sleep=lambda s: None)
    import json
    rec = json.loads(store.read_bytes("espn/matches/eng.1/1.json"))
    assert rec["players"] == [{"team": "Home", "player": "A", "athlete_id": "", "shots": 2, "shots_on_target": 2,
                               "goals": 0, "assists": 0, "own_goals": 0}]
    assert rec["on_target_gap"] == 0


def test_suspicious_lines_flags_unparsed_and_duplicate_shots():
    from pitchline.espn_shots import parse_shots
    odd = {"text": "Z (Home) hits the bar with a volley.", "play": {"id": "77", "type": {"text": "Volley"}}}
    s = {"commentary": [_shot("1", "Home", "Shot Off Target"), _shot("2", "Home", "Shot Off Target"), odd]}
    out = eb.suspicious_lines(s, parse_shots(s, "9", "eng.1"))
    assert any("UNPARSED" in line and "hits the bar" in line for line in out)
    assert any("DUPLICATE?" in line for line in out)


def test_inconsistent_shot_typed_play_is_reported():
    from pitchline.espn_shots import parse_shots
    bad = _shot("5", "Home", "Shot On Target", text="Foul by X (Home).")
    s = {"commentary": [bad]}
    out = eb.suspicious_lines(s, parse_shots(s, "9", "eng.1"))
    assert any("INCONSISTENT" in line for line in out)


def test_foul_and_var_penalty_lines_are_not_flagged_as_unparsed():
    foul = {"text": "Penalty conceded by X (Home) after a foul in the penalty area.",
            "play": {"id": "8", "type": {"text": "Foul"}}}
    assert eb.suspicious_lines({"commentary": [foul]}, []) == []


def test_text_only_parsed_shot_is_not_flagged_as_unparsed():
    from pitchline.espn_shots import parse_shots
    line = {"sequence": 7, "text": "Attempt missed. J K (Bournemouth) left footed shot is wide.", "time": {"value": 9.0}}
    s = {"boxscore": {"teams": [{"team": {"displayName": "AFC Bournemouth"}}]}, "commentary": [line]}
    shots = parse_shots(s, "9", "eng.1")
    assert len(shots) == 1 and eb.suspicious_lines(s, shots) == []


def _roster(team, shots):
    return {"team": team, "player": "A", "shots": shots, "shots_on_target": 0, "goals": 0, "assists": 0,
            "own_goals": 0}


def test_empty_boxscore_falls_back_to_player_stats_and_adds_penalties():
    # Seen on older Premier League matches: every boxscore statistic reads 0, but player stats exist.
    shots = [{"team": "Home", "outcome": "saved", "on_target": True, "situation": "open_play"},
             {"team": "Home", "outcome": "saved", "on_target": True, "situation": "penalty"}]
    tot = {"Home": {"totalShots": "0", "shotsOnTarget": "0", "blockedShots": "0"},
           "Away": {"totalShots": "0", "shotsOnTarget": "0", "blockedShots": "0"}}
    assert not eb.boxscore_usable(tot)
    assert eb.reconcile(shots, tot, players=[_roster("Home", 1)]) == "ok"   # 1 player shot + 1 penalty
    assert eb.reconcile(shots, tot, players=[_roster("Home", 3)]) == "mismatch"
    assert eb.reconcile(shots, tot, players=[]) == "no_boxscore"


def test_stoppage_time_repeats_are_not_flagged_as_duplicates():
    from pitchline.espn_shots import parse_shots
    s = {"commentary": [_shot("1", "Home", "Shot Off Target"), _shot("2", "Home", "Shot Off Target")]}
    shots = parse_shots(s, "9", "eng.1")
    for sh in shots:
        sh["seconds"] = 5400.0
    assert not any("DUPLICATE?" in line for line in eb.suspicious_lines(s, shots))


def test_player_stats_fallback_counts_saved_penalties_but_not_scored_ones():
    tot = {"Home": {"totalShots": "0", "shotsOnTarget": "0", "blockedShots": "0"},
           "Away": {"totalShots": "0", "shotsOnTarget": "0", "blockedShots": "0"}}
    scored = [{"team": "Home", "outcome": "goal", "on_target": True, "situation": "penalty"},
              {"team": "Home", "outcome": "saved", "on_target": True, "situation": "open_play"}]
    assert eb.reconcile(scored, tot, players=[_roster("Home", 2)]) == "ok"       # scored penalty is in player stats
    saved = [{"team": "Home", "outcome": "saved", "on_target": True, "situation": "penalty"},
             {"team": "Home", "outcome": "saved", "on_target": True, "situation": "open_play"}]
    assert eb.reconcile(saved, tot, players=[_roster("Home", 1)]) == "ok"        # saved penalty is not


def _roster_entry(aid, name, starter=False, subbed_in=False, subbed_out=False, plays=()):
    return {"athlete": {"id": aid, "displayName": name}, "starter": starter, "subbedIn": subbed_in,
            "subbedOut": subbed_out,
            "plays": [{"clock": {"displayValue": c}, "substitution": sub, "redCard": red} for c, sub, red in plays]}


def test_minutes_from_roster_plays_include_stoppage_and_red_cards():
    summary = {"commentary": [{"play": {"clock": {"displayValue": "90'+7'"}}}],
               "rosters": [{"team": {"displayName": "Home"}, "roster": [
                   _roster_entry("1", "Full", starter=True),
                   _roster_entry("2", "Off", starter=True, subbed_out=True, plays=[("75'", True, False)]),
                   _roster_entry("3", "On", subbed_in=True, plays=[("75'", True, False)]),
                   _roster_entry("4", "Sent", starter=True, plays=[("58'", False, True)]),
                   _roster_entry("5", "Bench")]}]}
    assert eb.match_length(summary) == 97
    got = {r["player"]: r["minutes"] for r in eb._lineup(summary)}
    assert got == {"Full": 97, "Off": 75, "On": 22, "Sent": 58}          # the unused sub is not in the lineup
    assert eb.parse_minute("45'+5'") == 50 and eb.parse_minute(None) is None


def test_upgrade_redownloads_only_old_layout_files(tmp_path):
    store, log = LocalStore(tmp_path), []
    args = (store, "eng.1", date(2024, 1, 1), date(2024, 1, 1))
    eb.backfill(*args, make_fetch(_summary(3, 1)), sleep=lambda s: None)
    rec = json.loads(store.read_bytes("espn/matches/eng.1/1.json"))
    assert rec["schema"] == eb.SCHEMA and "lineup" in rec
    eb.backfill(*args, make_fetch(_summary(3, 1), log), sleep=lambda s: None, upgrade=True)
    assert not any("summary" in u for u in log)                           # already current: skipped
    rec.pop("schema")
    store.write_bytes("espn/matches/eng.1/1.json", json.dumps(rec).encode())
    eb.backfill(*args, make_fetch(_summary(3, 1), log), sleep=lambda s: None, upgrade=True)
    assert any("summary" in u for u in log)                               # old layout: refetched


def test_recent_targets_cover_every_tracked_competition():
    from datetime import date

    from pitchline.espn_backfill import recent_targets
    from pitchline.live import LEAGUES
    t = recent_targets(date(2026, 11, 24), 5)
    assert [x["league"] for x in t] == LEAGUES
    assert t[0] == {"league": "eng.1", "from": "2026-11-19", "to": "2026-11-24"}


def test_league_all_expands_to_every_tracked_competition(monkeypatch, tmp_path):
    import pitchline.espn_backfill as eb
    from pitchline.live import LEAGUES
    seen = []
    monkeypatch.setattr(eb, "backfill", lambda store, league, *a, **k: seen.append(league) or {
        "listed": 0, "new": 0, "skipped": 0, "failed": 0, "stopped_early": False, "mismatch": 0, "ok": 0})
    eb.main(["--root", str(tmp_path), "--league", "all", "--from", "2026-01-01", "--to", "2026-01-02"])
    assert seen == LEAGUES


def _row(team, player, pid, secs, text="Attempt saved. X (T) right footed shot is saved."):
    return {"team": team, "player": player, "play_id": pid, "seconds": secs, "outcome": "saved", "raw_text": text}


def _tot(a, b):
    return {"Home": {"totalShots": a}, "Away": {"totalShots": b}}


def test_duplicate_summary_line_is_dropped_only_when_the_total_then_matches_exactly():
    shots = [_row("Home", "A", "1", 100), _row("Home", "B", "2", 200),
             _row("Home", "A", "3", 101, "A (Home) Shot Saved at 2'"), _row("Away", "C", "4", 300)]
    kept, n = eb.drop_duplicate_shots(shots, _tot(2, 1))
    assert n == 1 and [s["play_id"] for s in kept] == ["1", "2", "4"]
    same, none = eb.drop_duplicate_shots(shots, _tot(1, 1))     # dropping one would still leave 2 vs 1: no change
    assert none == 0 and same == shots


def test_text_only_twin_of_a_structured_shot_is_dropped():
    shots = [_row("Home", "A", "11", 100), _row("Home", "A", "text:5", 103), _row("Home", "B", "12", 900)]
    kept, n = eb.drop_duplicate_shots(shots, {"Home": {"totalShots": 2}, "Away": {"totalShots": 0}})
    assert n == 1 and [s["play_id"] for s in kept] == ["11", "12"]


def test_nothing_is_dropped_when_nothing_looks_duplicated_or_boxscore_is_empty():
    shots = [_row("Home", "A", "1", 100), _row("Home", "B", "2", 200)]
    assert eb.drop_duplicate_shots(shots, _tot(1, 0)) == (shots, 0)
    assert eb.drop_duplicate_shots(shots, _tot(0, 0)) == (shots, 0)
