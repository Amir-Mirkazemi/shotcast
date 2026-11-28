"""Estimate xG for ESPN shots, calibrated to StatsBomb's xG.

Uses only pre-shot information that ESPN also provides: location, body part and
situation. Outcome (saved/blocked/goal) is deliberately NOT a feature.
"""
from __future__ import annotations

import math

import numpy as np

GOAL_X, GOAL_Y = 120.0, 40.0          # StatsBomb pitch units (120 x 80)
POST = 3.66                            # half goal width in yards (StatsBomb units)
PENALTY_XG = 0.78


def to_statsbomb(x_pct: float, y_pct: float) -> tuple[float, float]:
    """ESPN reports 0-100 on both axes; rescale to the 120x80 grid."""
    return x_pct * 1.2, y_pct * 0.8


def geometry(x: float, y: float) -> tuple[float, float]:
    dx, dy = GOAL_X - x, GOAL_Y - y
    dist = math.hypot(dx, dy)
    a = math.atan2(POST * 2 * dx, dx * dx + dy * dy - POST * POST)
    return dist, a if a >= 0 else a + math.pi


def features(x: float, y: float, body: str, situation: str) -> list[float]:
    dist, ang = geometry(x, y)
    return [dist, ang, math.log(dist + 1), float(body == "header"),
            float(situation == "free_kick"), float(situation == "from_corner")]


def feature_matrix(rows: list[tuple]) -> np.ndarray:
    return np.array([features(*r) for r in rows])
