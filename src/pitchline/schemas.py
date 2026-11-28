"""Data contracts for the stored ESPN match files, checked with Pydantic.

A match file that breaks the contract is not deleted and not silently used: it is copied to
``espn/quarantine/{league}/{event}.json`` together with the list of reasons, and the build skips it.
The checks are the ones that catch real feed problems: a missing team, a score that is not a number,
a player with more shots on target than shots, minutes above a full match.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .storage import Store

QUARANTINE = "espn/quarantine/"
MAX_MINUTES = 150            # 90 + extra time + generous stoppage
OUTCOMES = {"goal", "saved", "woodwork", "off_target", "blocked", "own_goal"}


class _Row(BaseModel):
    model_config = ConfigDict(extra="allow")        # new fields from the feed must not break old checks


class Team(_Row):
    name: str = Field(min_length=1)
    home: bool
    score: str | int | None = None

    @field_validator("score")
    @classmethod
    def _score_is_a_number(cls, v):
        if v is None or str(v).strip() == "":
            return v
        if not str(v).strip().isdigit():
            raise ValueError(f"score {v!r} is not a whole number")
        return v


class PlayerRow(_Row):
    team: str
    player: str | None = None
    athlete_id: str = ""
    shots: int = Field(ge=0)
    shots_on_target: int = Field(ge=0)
    goals: int = Field(ge=0)
    assists: int = Field(ge=0)
    own_goals: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _on_target_not_above_shots(self):
        # ESPN counts a goal as on target; a goal with 0 shots listed is a feed gap, not an error
        if self.shots_on_target > max(self.shots, self.goals):
            raise ValueError(f"{self.player}: {self.shots_on_target} on target but {self.shots} shots")
        return self


class LineupRow(_Row):
    team: str
    athlete_id: str = ""
    player: str | None = None
    minutes: int | None = Field(default=None, ge=0, le=MAX_MINUTES)


class ShotRow(_Row):
    team: str | None = None
    outcome: str
    x: float | None = None
    y: float | None = None

    @field_validator("outcome")
    @classmethod
    def _known_outcome(cls, v):
        if v not in OUTCOMES:
            raise ValueError(f"unknown shot outcome {v!r}")
        return v


class MatchRecord(_Row):
    league: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    date: str
    teams: list[Team]
    players: list[PlayerRow] = []
    lineup: list[LineupRow] = []
    shots: list[ShotRow] = []
    coverage: str = "ok"
    schema_version: int | None = Field(default=None, alias="schema")

    @field_validator("date")
    @classmethod
    def _iso_date(cls, v):
        try:
            date.fromisoformat(v[:10])
        except ValueError:
            raise ValueError(f"date {v!r} is not ISO formatted") from None
        return v

    @model_validator(mode="after")
    def _teams_and_players_agree(self):
        if len(self.teams) != 2:
            raise ValueError(f"expected 2 teams, found {len(self.teams)}")
        names = {t.name for t in self.teams}
        if len(names) != 2:
            raise ValueError("both teams have the same name")
        if sum(t.home for t in self.teams) != 1:
            raise ValueError("exactly one team must be the home team")
        stray = {r.team for r in (*self.players, *self.lineup) if r.team not in names} - {None}
        if stray:
            raise ValueError(f"player rows for unknown teams: {sorted(stray)}")
        return self


def validate_record(rec: dict[str, Any]) -> list[str]:
    """Empty list when the record meets the contract, otherwise one readable line per problem."""
    try:
        MatchRecord.model_validate(rec)
    except ValidationError as err:
        out = []
        for e in err.errors():
            where = ".".join(str(p) for p in e["loc"]) or "record"
            out.append(f"{where}: {e['msg'].removeprefix('Value error, ')}")
        return out[:20]
    return []


def quarantine(store: Store, rec: dict, key: str, reasons: list[str]) -> str:
    """Keep a copy of a failing record, and why, away from the data the site reads."""
    league = rec.get("league") or key.split("/")[-2]
    qkey = f"{QUARANTINE}{league}/{key.rsplit('/', 1)[-1]}"
    store.write_bytes(qkey, json.dumps({"reasons": reasons, "source_key": key, "record": rec}).encode())
    return qkey


def split_valid(store: Store, keyed: list[tuple[str, dict]]) -> tuple[list[dict], list[dict]]:
    """(good records, [{key, reasons}]) and quarantine every bad one."""
    good, bad = [], []
    for key, rec in keyed:
        reasons = validate_record(rec)
        if reasons:
            quarantine(store, rec, key, reasons)
            bad.append({"key": key, "reasons": reasons})
        else:
            good.append(rec)
    return good, bad


def scan(store: Store, prefix: str = "espn/matches/") -> dict:
    """Validate every stored match file (the `validate` command)."""
    keys = sorted(k for k in store.list_keys(prefix) if k.endswith(".json"))
    keyed = [(k, json.loads(store.read_bytes(k))) for k in keys]
    good, bad = split_valid(store, keyed)
    return {"checked": len(keyed), "valid": len(good), "quarantined": len(bad), "problems": bad}
