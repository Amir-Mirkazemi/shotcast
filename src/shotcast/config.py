from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.yml"


@dataclass(frozen=True)
class Competition:
    competition_id: int
    season_id: int
    name: str


@dataclass(frozen=True)
class Sources:
    base_url: str
    competitions: tuple[Competition, ...]


def load_sources(path: str | Path = DEFAULT_CONFIG) -> Sources:
    raw = yaml.safe_load(Path(path).read_text())
    comps = tuple(Competition(**c) for c in raw["competitions"])
    if not comps:
        raise ValueError("config lists no competitions")
    return Sources(base_url=raw["base_url"].rstrip("/"), competitions=comps)
