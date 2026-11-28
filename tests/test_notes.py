from shotcast.notes import shot_notes


def rec(lg, eid, day, players, teams=("A", "B"), totals=None, scores=("2", "1")):
    return {"league": lg, "event_id": eid, "date": f"{day}T15:00Z", "players": players,
            "teams": [{"name": teams[0], "home": True, "score": scores[0]},
                      {"name": teams[1], "home": False, "score": scores[1]}],
            "boxscore_totals": totals or {}}


def pl(team, name, aid, sot, goals=0, shots=None):
    return {"team": team, "athlete_id": aid, "player": name, "shots": shots if shots is not None else sot,
            "shots_on_target": sot, "goals": goals}


def row(pid, name, team, shots, sot, sot90=None):
    return {"pid": pid, "player": name, "team": team, "shots": shots, "shots_on_target": sot, "sot90": sot90}


def test_notes_are_factual_windowed_and_varied():
    lname = lambda lg: {"eng.1": "Premier League"}[lg]  # noqa: E731
    recs = [rec("eng.1", "1", "2026-11-01", [pl("A", "Old Timer", "9", 9)]),                 # outside the window
            rec("eng.1", "2", "2026-11-20", [pl("A", "Sam Hat", "7", 4, goals=3), pl("B", "Bo", "8", 1)],
                totals={"A": {"totalShots": 22}, "B": {"totalShots": 9}}),
            rec("eng.1", "3", "2026-11-24", [pl("A", "Sam Hat", "7", 2)])]
    players = {("eng.1", "7"): row("7", "Sam Hat", "A", 100, 60, 0.9), ("eng.1", "8"): row("8", "Bo", "B", 70, 20, 0.2),
               ("eng.1", "9"): row("9", "Old Timer", "A", 10, 9)}
    notes = shot_notes(recs, players, lname)
    kinds = {n["kind"] for n in notes}
    assert kinds == {"hattrick", "form", "heavy", "per90", "accuracy"}
    form = next(n for n in notes if n["kind"] == "form")
    assert "Sam Hat" in form["text"] and "6 shots on target" in form["text"] and form["pid"] == "7"
    hat = next(n for n in notes if n["kind"] == "hattrick")
    assert "3 goals" in hat["text"] and "against B" in hat["text"] and "Nov 20" in hat["text"]
    heavy = next(n for n in notes if n["kind"] == "heavy")
    assert "22 shots to B's 9" in heavy["text"]
    assert "Old Timer" not in form["text"]
    acc = next(n for n in notes if n["kind"] == "accuracy")
    assert "Sam Hat" in acc["text"] and "60 of 100" in acc["text"]


def test_no_note_without_enough_evidence():
    recs = [rec("eng.1", "1", "2026-11-24", [pl("A", "Sam", "7", 1)])]
    players = {("eng.1", "7"): row("7", "Sam", "A", 5, 1)}
    assert shot_notes(recs, players, lambda lg: lg) == []
