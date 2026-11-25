from pitchline.espn_shots import parse_shots


def _play(ptype, text, parts, x=90.0, y=50.0):
    return {
        "text": text,
        "play": {
            "id": "1", "type": {"text": ptype}, "text": text, "period": {"number": 1},
            "clock": {"value": 210.0}, "team": {"displayName": "Home"},
            "participants": [{"athlete": {"displayName": p}} for p in parts],
            "fieldPositionX": x, "fieldPositionY": y, "goalPositionY": 50.0,
        },
    }


def test_assisted_header_off_target():
    s = {"commentary": [_play("Shot Off Target",
        "Attempt missed. A B (Home) header from the left side of the six yard box is too high. Assisted by C D.",
        ["A B", "C D"])]}
    r = parse_shots(s, "9", "eng.1")[0]
    assert (r["player"], r["assist_player"], r["body_part"], r["outcome"]) == ("A B", "C D", "header", "off_target")
    assert r["zone_text"] == "left side of the six yard box"
    assert r["on_target"] is False


def test_penalty_goal_has_no_assister():
    s = {"commentary": [_play("Penalty - Scored",
        "Goal! Home 2, Away 0. P G (Home) converts the penalty with a right footed shot to the top right corner.", ["P G"])]}
    r = parse_shots(s, "9", "eng.1")[0]
    assert r["situation"] == "penalty" and r["is_goal"] and r["on_target"] and r["assist_player"] is None


def test_cross_after_corner():
    s = {"commentary": [_play("Goal - Header",
        "Goal! A 0, B 2. M B (B) header from the centre of the box to the bottom left corner. Assisted by D C with a cross following a corner.",
        ["M B", "D C"])]}
    r = parse_shots(s, "9", "esp.1")[0]
    assert r["assist_is_cross"] and r["situation"] == "from_corner" and r["body_part"] == "header"


def test_own_goal_never_credits_assist_and_non_shots_ignored():
    s = {"commentary": [
        _play("Own Goal", "Own Goal by X Y, Lazio. Napoli 1, Lazio 1.", ["X Y"]),
        _play("Foul", "Foul by someone.", ["S"]),
        {"text": "no play object"},
    ]}
    rows = parse_shots(s, "9", "ita.1")
    assert len(rows) == 1 and rows[0]["outcome"] == "own_goal" and rows[0]["on_target"] is False


def test_direct_free_kick_attempt_typed_free_kick_is_still_a_shot():
    line = ("Attempt missed. M D (Brentford) right footed shot from outside the box "
            "is too high from a direct free kick.")
    s = {"commentary": [_play("Free Kick", line, ["M D"])]}
    r = parse_shots(s, "9", "eng.1")[0]
    assert r["outcome"] == "off_target" and r["player"] == "M D" and r["situation"] == "free_kick"


def test_non_shot_free_kick_lines_are_ignored():
    s = {"commentary": [_play("Free Kick", "Free kick awarded to Home.", ["X"])]}
    assert parse_shots(s, "9", "eng.1") == []


def _box(*teams):
    return {"boxscore": {"teams": [{"team": {"displayName": t}} for t in teams]}}


def test_text_only_shot_without_play_object_is_parsed_without_location():
    line = ("Attempt blocked. A D (Manchester United) left footed shot from the centre of the box "
            "is blocked. Assisted by B C with a cross.")
    s = {**_box("Manchester United", "Nottingham Forest"),
         "commentary": [{"sequence": 41, "text": line, "time": {"value": 600.0}}]}
    r = parse_shots(s, "9", "eng.1")[0]
    assert (r["team"], r["player"], r["outcome"], r["assist_player"]) == ("Manchester United", "A D", "blocked", "B C")
    assert r["has_location"] is False and r["x"] is None and r["assist_is_cross"]


def test_text_only_team_name_matches_despite_ampersand_and_unknown_team_is_skipped():
    ok = {"sequence": 1, "text": "Attempt saved. P G (Brighton and Hove Albion) right footed shot is saved.",
          "time": {"value": 60.0}}
    bad = {"sequence": 2, "text": "Attempt saved. Q (Mystery FC) right footed shot is saved.", "time": {"value": 90.0}}
    s = {**_box("Brighton & Hove Albion"), "commentary": [ok, bad]}
    rows = parse_shots(s, "9", "eng.1")
    assert len(rows) == 1 and rows[0]["team"] == "Brighton & Hove Albion"


def test_shot_typed_play_whose_text_is_a_foul_is_rejected():
    glitch = _play("Shot On Target", "Foul by Reinildo Mandava (Sunderland).", ["Brian Brobbey"])
    real = _play("Shot On Target", "Attempt saved. B B (Sunderland) right footed shot is saved.", ["B B"])
    rows = parse_shots({"commentary": [glitch, real]}, "9", "eng.1")
    assert len(rows) == 1 and rows[0]["player"] == "B B"


def test_saved_penalty_is_a_shot_on_target_with_penalty_situation():
    line = "Penalty saved. A S (Bournemouth) right footed shot saved in the bottom left corner by E M (Aston Villa)."
    r = parse_shots({"commentary": [_play("Penalty - Saved", line, ["A S", "E M"])]}, "9", "eng.1")[0]
    assert (r["outcome"], r["on_target"], r["situation"], r["is_goal"]) == ("saved", True, "penalty", False)


def test_missed_penalty_and_text_only_penalty_line():
    miss = "Penalty missed! X (Home) right footed shot misses to the left."
    r = parse_shots({"commentary": [_play("Penalty - Missed", miss, ["X"])]}, "9", "eng.1")[0]
    assert r["outcome"] == "off_target" and r["situation"] == "penalty"
    text_only = {"sequence": 5, "text": "Penalty saved. Z (Home) left footed shot saved in the centre by K (Away).",
                 "time": {"value": 3000.0}}
    rows = parse_shots({**_box("Home", "Away"), "commentary": [text_only]}, "9", "eng.1")
    assert rows[0]["outcome"] == "saved" and rows[0]["has_location"] is False


def test_penalty_foul_and_var_lines_are_not_shots():
    foul = _play("Foul", "Penalty conceded by X (Home) after a foul in the penalty area.", ["X"])
    var = _play("VAR - Referee decision cancelled", "VAR Decision: Penalty Home.", ["Y"])
    assert parse_shots({"commentary": [foul, var]}, "9", "eng.1") == []


def test_any_goal_subtype_counts_as_a_goal_but_goal_kick_and_disallowed_do_not():
    volley = _play("Goal - Volley", "Goal! A 1, B 0. X (Home) right footed shot from the box. Assisted by Y.", ["X", "Y"])
    kick = _play("Goal Kick", "Goal kick awarded to Home.", ["Z"])
    off = _play("Goal - Disallowed", "Goal! disallowed. W (Home) shot.", ["W"])
    rows = parse_shots({"commentary": [volley, kick, off]}, "9", "eng.1")
    assert [(r["player"], r["outcome"], r["is_goal"], r["on_target"]) for r in rows] == [("X", "goal", True, True)]


def test_text_only_team_name_matches_by_unique_containment():
    line = {"sequence": 3, "text": "Attempt missed. J K (Bournemouth) left footed shot is high and wide.",
            "time": {"value": 120.0}}
    s = {**_box("AFC Bournemouth", "Wolverhampton Wanderers"), "commentary": [line]}
    assert parse_shots(s, "9", "eng.1")[0]["team"] == "AFC Bournemouth"


def test_ambiguous_containment_does_not_guess_a_team():
    line = {"sequence": 4, "text": "Attempt missed. J K (Real) shot is wide.", "time": {"value": 10.0}}
    s = {**_box("Real Madrid", "Real Sociedad"), "commentary": [line]}
    assert parse_shots(s, "9", "esp.1") == []


def test_free_kick_typed_shot_is_parsed_when_only_the_entry_text_has_the_prefix():
    entry = _play("Free Kick", "L D (Brighton) right footed shot from more than 35 yards.", ["L D"])
    entry["text"] = "Attempt missed. L D (Brighton) right footed shot from more than 35 yards misses to the left."
    rows = parse_shots({"commentary": [entry]}, "9", "eng.1")
    assert len(rows) == 1 and rows[0]["outcome"] == "off_target"


def test_text_only_shot_matches_team_despite_accents():
    summary = {"boxscore": {"teams": [{"team": {"displayName": "Cádiz"}}, {"team": {"displayName": "Alavés"}}]},
               "commentary": [
                   {"sequence": 1, "time": {"value": 600.0},
                    "text": "Attempt saved. Roger Martí (Cadiz) right footed shot from the centre of the box "
                            "is saved in the top centre of the goal. Assisted by Chris Ramos."},
                   {"sequence": 2, "time": {"value": 900.0},
                    "text": "Attempt blocked. Jon Guridi (Alaves) left footed shot from outside the box is blocked."}]}
    rows = parse_shots(summary, "1", "esp.1")
    assert [(r["team"], r["player"], r["outcome"]) for r in rows] == [
        ("Cádiz", "Roger Martí", "saved"), ("Alavés", "Jon Guridi", "blocked")]


def test_text_only_shot_uses_roster_when_team_name_in_text_differs_from_boxscore():
    summary = {**_box("Bayern Munich", "Hertha Berlin"),
               "rosters": [{"team": {"displayName": "Bayern Munich"}, "roster": [
                               {"athlete": {"displayName": "Thomas Müller"}}]},
                           {"team": {"displayName": "Hertha Berlin"}, "roster": [
                               {"athlete": {"displayName": "Jerry St. Juste"}}]}],
               "commentary": [
                   {"sequence": 1, "time": {"value": 60.0},
                    "text": "Attempt saved. Thomas Müller (FC Bayern München) right footed shot from the centre of the box is saved."},
                   {"sequence": 2, "time": {"value": 70.0},
                    "text": "Attempt missed. Jeremiah St. Juste (Mystery Name) header from the centre of the box is close."},
                   {"sequence": 3, "time": {"value": 80.0},
                    "text": "Attempt saved. Nobody Known (Mystery Name) right footed shot is saved."}]}
    rows = parse_shots(summary, "9", "ger.1")
    assert [(r["player"], r["team"]) for r in rows] == [("Thomas Müller", "Bayern Munich"),
                                                        ("Jeremiah St. Juste", "Hertha Berlin")]
