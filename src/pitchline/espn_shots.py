"""Parse ESPN match-summary commentary into one clean row per shot.

Source: https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event=ID
(unofficial endpoint). Every shot-like commentary entry carries a structured
``play`` object with type, team, participants (shooter first, assister second)
and pitch coordinates, so the parser relies on structure first and uses the
free text only for body part, situation and cross detection.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# ESPN play type text -> (outcome, on_target)
SHOT_TYPES: dict[str, tuple[str, bool]] = {
    "Shot Off Target": ("off_target", False),
    "Shot Blocked": ("blocked", False),
    "Shot On Target": ("saved", True),
    "Shot Hit Woodwork": ("woodwork", False),
    "Goal": ("goal", True),
    "Goal - Header": ("goal", True),
    "Goal - Free-kick": ("goal", True),
    "Penalty - Scored": ("goal", True),
    "Penalty - Saved": ("saved", True),
    "Penalty - Missed": ("off_target", False),
    "Own Goal": ("own_goal", False),
}

# Some shot lines carry another play type (e.g. a direct free-kick attempt is typed "Free Kick"),
# so lines are also classified by their text prefix.
_TEXT_PREFIX: list[tuple[str, tuple[str, bool]]] = [
    ("Attempt missed", ("off_target", False)),
    ("Attempt saved", ("saved", True)),
    ("Attempt blocked", ("blocked", False)),
    ("Penalty saved", ("saved", True)),
    ("Penalty missed", ("off_target", False)),
]

_BODY = [
    ("header", re.compile(r"\bheader\b", re.I)),
    ("right_foot", re.compile(r"right footed", re.I)),
    ("left_foot", re.compile(r"left footed", re.I)),
]
_ZONE = re.compile(
    r"from (?:the )?((?:very )?close range"
    r"|(?:the )?(?:centre|right|left)(?: side)? of the (?:six yard box|box)"
    r"|(?:a )?difficult angle(?: and long range| on the (?:right|left))?|outside the box|long range"
    r"|more than 35 yards|the (?:right|left) wing|very close range)",
    re.I,
)
_CROSS = re.compile(r"with a cross", re.I)
_THROUGH = re.compile(r"through ball", re.I)


_NOT_A_GOAL = ("disallow", "cancel", "chalked", "no goal")


def classify_type(play_type: str | None) -> tuple[str, bool] | None:
    """Outcome for a structured play type. Any "Goal - <how>" subtype (Volley, Header, Free-kick,
    Penalty, ...) is a goal, except disallowed/cancelled ones. "Goal Kick" is not a shot."""
    if not play_type:
        return None
    if play_type in SHOT_TYPES:
        return SHOT_TYPES[play_type]
    if play_type.startswith("Goal - ") and not any(w in play_type.lower() for w in _NOT_A_GOAL):
        return ("goal", True)
    return None


def entry_play_id(entry: dict) -> str:
    """Id under which a commentary entry is stored: the play id, or text:<sequence> for text-only shots."""
    play = entry.get("play")
    if play:
        return str(play.get("id", ""))
    return f"text:{entry.get('sequence')}"


def _situation(play_type: str, text: str) -> str:
    if play_type.startswith("Penalty") or text.startswith("Penalty") or "converts the penalty" in text:
        return "penalty"
    if play_type == "Goal - Free-kick" or "free kick" in text.lower():
        return "free_kick"
    if "following a corner" in text.lower():
        return "from_corner"
    return "open_play"


# A structured "shot" play whose text is not about a shot is an ESPN data glitch
# (seen: play typed "Shot On Target" with the text "Foul by ... (Sunderland)."). Such plays are rejected.
_SHOT_WORDS = ("attempt", "goal", "penalty", "hits the", "converts", "header", "shot")


def looks_like_shot(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in _SHOT_WORDS)


def _norm(name: str) -> str:
    """Lower-case, accent-free, letters and digits only ("Cádiz" and "Cadiz" both give "cadiz")."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower().replace("&", "and"))


def resolve_team(name: str, team_names: dict[str, str]) -> str | None:
    """Match a team name from commentary text to a boxscore team. Exact (normalised) first, then a
    unique containment match ("Bournemouth" in "AFC Bournemouth"); ambiguous matches return None."""
    key = _norm(name)
    if key in team_names:
        return team_names[key]
    if len(key) < 4:
        return None
    hits = [v for k, v in team_names.items() if key in k or k in key]
    return hits[0] if len(hits) == 1 else None


_TEXT_SHOT = re.compile(r"^(?:Attempt|Penalty) (missed|saved|blocked)\. (.+?) \((.+?)\) ")
_ASSIST = re.compile(r"Assisted by (.+?)(?: with | following |\.|$)")
_TEXT_OUTCOME = {"missed": ("off_target", False), "saved": ("saved", True), "blocked": ("blocked", False)}


def roster_teams(summary: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Which team each player plays for in this match, from ESPN's rosters: (full name, last name) -> team.
    A name that belongs to players on both teams is left out rather than guessed."""
    full: dict[str, set] = {}
    last: dict[str, set] = {}
    for r in summary.get("rosters", []):
        team = (r.get("team") or {}).get("displayName")
        for pl in r.get("roster", []):
            name = (pl.get("athlete") or {}).get("displayName") or ""
            if not (team and name):
                continue
            full.setdefault(_norm(name), set()).add(team)
            last.setdefault(_norm(name.split()[-1]), set()).add(team)
    pick = lambda d: {k: next(iter(v)) for k, v in d.items() if len(v) == 1}  # noqa: E731
    return pick(full), pick(last)


def _team_for_player(player: str, roster: tuple[dict[str, str], dict[str, str]]) -> str | None:
    """Fallback when the team named in the text differs from the boxscore name ("FC Bayern Munchen" vs
    "Bayern Munich"): use the team the roster says this player is on."""
    full, last = roster
    return full.get(_norm(player)) or last.get(_norm(player.split()[-1] if player.split() else ""))


def _text_only_shot(entry: dict, team_names: dict[str, str], event_id: str, league: str,
                    roster: tuple[dict[str, str], dict[str, str]] = ({}, {})) -> dict | None:
    """Shots that ESPN publishes as plain commentary text with no structured `play` (no coordinates)."""
    text = entry.get("text") or ""
    m = _TEXT_SHOT.match(text)
    if not m:
        return None
    kind, player, team_txt = m.groups()
    team = resolve_team(team_txt, team_names) or _team_for_player(player, roster)
    if team is None:
        return None
    outcome, on_target = _TEXT_OUTCOME[kind]
    body = next((n for n, rx in _BODY if rx.search(text)), "other")
    zone = _ZONE.search(text)
    assist = _ASSIST.search(text)
    t = entry.get("time") or {}
    return {
        "league": league, "event_id": str(event_id), "play_id": entry_play_id(entry),
        "period": None, "seconds": t.get("value"), "wallclock": None, "team": team, "player": player,
        "assist_player": assist.group(1) if assist else None, "outcome": outcome, "on_target": on_target,
        "is_goal": False, "body_part": body, "situation": _situation("", text),
        "assist_is_cross": bool(_CROSS.search(text)), "assist_is_through_ball": bool(_THROUGH.search(text)),
        "zone_text": zone.group(1).lower() if zone else None, "x": None, "y": None, "goal_y": None,
        "has_location": False, "raw_text": text,
    }


def parse_shots(summary: dict[str, Any], event_id: str, league: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    team_names = {_norm(t["team"]["displayName"]): t["team"]["displayName"]
                  for t in summary.get("boxscore", {}).get("teams", []) if t.get("team")}
    roster = roster_teams(summary)
    for entry in summary.get("commentary", []):
        play = entry.get("play")
        if not play:
            extra = _text_only_shot(entry, team_names, event_id, league, roster)
            if extra:
                rows.append(extra)
            continue
        ptype = play.get("type", {}).get("text")
        text = play.get("text") or entry.get("text") or ""
        typed = classify_type(ptype)
        if typed is not None:
            if not looks_like_shot(text):
                continue
            outcome, on_target = typed
        else:
            # the line can start with "Attempt ..." in either the play text or the commentary entry's own text
            texts = (text, play.get("text") or "", entry.get("text") or "")
            hit = next((v for t in texts for p, v in _TEXT_PREFIX if t.startswith(p)), None)
            if hit is None:
                continue
            outcome, on_target = hit
        parts = [p["athlete"]["displayName"] for p in play.get("participants", []) if p.get("athlete")]
        body = next((n for n, rx in _BODY if rx.search(text)), "other")
        zone = _ZONE.search(text)
        rows.append(
            {
                "league": league,
                "event_id": str(event_id),
                "play_id": str(play["id"]),
                "period": (play.get("period") or {}).get("number"),
                "seconds": (play.get("clock") or {}).get("value"),
                "wallclock": play.get("wallclock"),
                "team": (play.get("team") or {}).get("displayName"),
                "player": parts[0] if parts else None,
                "assist_player": parts[1] if len(parts) > 1 and outcome != "own_goal" else None,
                "outcome": outcome,
                "on_target": on_target,
                "is_goal": outcome == "goal",
                "body_part": body,
                "situation": _situation(ptype, text),
                "assist_is_cross": bool(_CROSS.search(text)),
                "assist_is_through_ball": bool(_THROUGH.search(text)),
                "zone_text": zone.group(1).lower() if zone else None,
                "x": play.get("fieldPositionX"),
                "y": play.get("fieldPositionY"),
                "goal_y": play.get("goalPositionY"),
                "has_location": play.get("fieldPositionX") is not None,
                "raw_text": text,
            }
        )
    return rows
