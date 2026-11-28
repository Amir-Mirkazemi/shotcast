"""Static website built from the stored ESPN match files (hosted on GitHub Pages).

Pages: home, a leaderboard page per competition, a page per team, a page per leading player with a shot map and
charts, news, best-of, match day, forecast, call-your-shot, and method pages. This module prepares the data for
each page; the HTML lives in Jinja2 templates (templates/), the styling in static/style.css and the behaviour in
static/*.js. The output is plain static files, so GitHub Pages hosts it with no server.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import zlib
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import cards
from .forecast import load_forecasts, scorecard
from .health_check import build_report
from .insights import (
    career_table,
    club_stints,
    form_chart,
    latest_predictions,
    match_view,
    player_history,
    season_rows,
)
from .leaderboard import read_valid, totals_from_records
from .notes import shot_notes
from .sitedata import (
    MIN_MINUTES_BEST,
    MIN_MINUTES_PER90,
    build_model,
    combined_rows,
    featured_ids,
    rank_map,
    slug,
)
from .storage import get_store

HERE = Path(__file__).parent
TEMPLATES, STATIC = HERE / "templates", HERE / "static"

SITE_NAME = "Shotcast"
OWNER = "Amir Mirkazemi"
OWNER_LINKS = (("LinkedIn", "https://www.linkedin.com/in/amir-mirkazemi/"),
               ("GitHub", "https://github.com/Amir-Mirkazemi"), ("amirdata.com", "https://www.amirdata.com/"))
# absolute address of the published site, needed for link previews (set by the workflow)
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
# in GitHub Actions the link follows the repository name, so renaming the repo does not break it
REPO_URL = "https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "Amir-Mirkazemi/shotcast")

LEAGUE_COLOR = {"eng.1": "#6a2c91", "esp.1": "#d6402b", "ita.1": "#1e6fd9", "ger.1": "#d9232e", "fra.1": "#2748c9",
                "usa.1": "#1b4fa8", "uefa.champions": "#1a4fbf", "afc.champions": "#0f8a5f", "fifa.world": "#9b1c3b"}
PAGE_COLOR = {"news": "#7c5cff", "live": "#10b981", "forecast": "#38bdf8", "call": "#ff7a45", "best": "#fbbf24",
              "about": "#8fa3c7", "how": "#8fa3c7", "method": "#8fa3c7"}

LEAGUE_NAMES = {
    "eng.1": "Premier League", "esp.1": "La Liga", "ita.1": "Serie A", "fra.1": "Ligue 1",
    "ger.1": "Bundesliga", "usa.1": "MLS", "uefa.champions": "Champions League",
    "afc.champions": "AFC Champions League", "fifa.world": "World Cup",
}
KIND_LABEL = {"hattrick": "Hat-trick", "form": "In form", "heavy": "Shot-heavy", "per90": "Per 90",
              "accuracy": "Accuracy"}
NAV = (("best", "Best", "trophy", "best.html"), ("leagues", "Leagues", "users", "index.html"),
       ("news", "News", "newspaper", "news.html"), ("live", "Match Day", "radio", "live.html"),
       ("forecast", "Forecast", "trending-up", "forecast.html"), ("compare", "Compare", "git-compare", "compare.html"),
       ("call", "Picks", "target", "call.html"))
MATCH_DAYS = 21          # matches this recent get their own page (older ones only if a forecast exists for them)
_MATCH_URLS: dict[tuple[str, str], str] = {}      # (league, event id) -> page, filled by build()


def match_url(lg: str, eid: str) -> str:
    return f"m-{lg}-{slug(str(eid))}.html"


def lname(slug_: str) -> str:
    return LEAGUE_NAMES.get(slug_, slug_)


def player_url(lg: str, pid: str) -> str:
    return f"p-{lg}-{slug(pid)}.html"


def team_url(lg: str, team: str) -> str:
    return f"t-{lg}-{slug(team)}.html"



CLUB_KITS = {
    "real madrid": ("#f4f4f2", "#e8b923", "solid"), "barcelona": ("#a50044", "#004d98", "vstripes"),
    "atl\u00e9tico": ("#cb3524", "#f4f4f2", "vstripes"), "atletico": ("#cb3524", "#f4f4f2", "vstripes"),
    "athletic": ("#d71920", "#f4f4f2", "vstripes"), "real sociedad": ("#0a5cb8", "#f4f4f2", "vstripes"),
    "betis": ("#0b9444", "#f4f4f2", "vstripes"), "sevilla": ("#f4f4f2", "#d71920", "solid"),
    "valencia": ("#f4f4f2", "#111111", "solid"), "villarreal": ("#f6d400", "#0a3a8f", "solid"),
    "girona": ("#d71920", "#f4f4f2", "solid"), "levante": ("#a50034", "#0a3a8f", "vstripes"),
    "paris saint": ("#0a2a5e", "#d71920", "band"), "marseille": ("#f4f4f2", "#2eb4e6", "solid"),
    "lyon": ("#f4f4f2", "#1a3d8f", "band"), "monaco": ("#d71920", "#f4f4f2", "sash"), "lille": ("#d71920", "#0a2a5e", "solid"),
    "manchester city": ("#6cabdd", "#f4f4f2", "solid"), "man city": ("#6cabdd", "#f4f4f2", "solid"),
    "manchester united": ("#da291c", "#111111", "solid"), "man united": ("#da291c", "#111111", "solid"),
    "liverpool": ("#c8102e", "#f4f4f2", "solid"), "arsenal": ("#ef0107", "#f4f4f2", "sleeves"),
    "chelsea": ("#034694", "#f4f4f2", "solid"), "tottenham": ("#f4f4f2", "#132257", "solid"),
    "newcastle": ("#111111", "#f4f4f2", "vstripes"), "aston villa": ("#670e36", "#95bfe5", "sleeves"),
    "brentford": ("#e30613", "#f4f4f2", "vstripes"), "brighton": ("#0057b8", "#f4f4f2", "vstripes"),
    "west ham": ("#7a263a", "#1bb1e7", "sleeves"), "everton": ("#003399", "#f4f4f2", "solid"),
    "fulham": ("#f4f4f2", "#111111", "solid"), "wolves": ("#fdb913", "#111111", "solid"),
    "crystal palace": ("#1b458f", "#c4122e", "vstripes"), "nottingham": ("#dd0000", "#f4f4f2", "solid"),
    "bournemouth": ("#da291c", "#111111", "vstripes"), "leeds": ("#f4f4f2", "#1d428a", "solid"),
    "sunderland": ("#eb172b", "#f4f4f2", "vstripes"), "burnley": ("#6c1d45", "#99d6ea", "sleeves"),
    "bayern": ("#dc052d", "#f4f4f2", "solid"), "dortmund": ("#fde100", "#111111", "solid"),
    "leverkusen": ("#e32221", "#111111", "solid"), "leipzig": ("#f4f4f2", "#dd0741", "solid"),
    "frankfurt": ("#111111", "#e1000f", "solid"), "stuttgart": ("#f4f4f2", "#e32219", "band"),
    "juventus": ("#f4f4f2", "#111111", "vstripes"), "inter miami": ("#f7b5cd", "#111111", "solid"),
    "internazionale": ("#0a4a9f", "#111111", "vstripes"), "inter": ("#0a4a9f", "#111111", "vstripes"),
    "milan": ("#d71920", "#111111", "vstripes"), "napoli": ("#12a0d7", "#f4f4f2", "solid"),
    "roma": ("#8e1f2f", "#f0bc42", "solid"), "lazio": ("#87d8f7", "#f4f4f2", "solid"),
    "atalanta": ("#1e71b8", "#111111", "vstripes"), "fiorentina": ("#5b2a86", "#f4f4f2", "solid"),
    "bologna": ("#a50034", "#0a3a8f", "vstripes"), "como": ("#1e5eb8", "#f4f4f2", "solid"),
    "lafc": ("#111111", "#c39e6d", "solid"), "los angeles football": ("#111111", "#c39e6d", "solid"),
    "galaxy": ("#f4f4f2", "#00245d", "solid"), "seattle": ("#5d9741", "#005595", "solid"),
    "benfica": ("#e30613", "#f4f4f2", "solid"), "porto": ("#0a3a8f", "#f4f4f2", "vstripes"),
    "sporting": ("#008057", "#f4f4f2", "hoops"), "ajax": ("#f4f4f2", "#d2122e", "band"),
    "celtic": ("#16973d", "#f4f4f2", "hoops"), "galatasaray": ("#f6a800", "#a90432", "sash"),
    "fenerbah": ("#ffed00", "#0a1f5e", "solid"), "club brugge": ("#1d6ac2", "#111111", "solid"),
    "psv": ("#e30613", "#f4f4f2", "solid"), "feyenoord": ("#e30613", "#f4f4f2", "sash"),
    "columbus": ("#fedd00", "#111111", "solid"), "cincinnati": ("#f05323", "#1d3c8c", "solid"),
}


def kit(team: str, name: str = "") -> tuple[str, str, str]:
    """(primary, secondary, pattern) for a club: its real main colours if known, else a generated kit."""
    t = (team or "").lower()
    for frag in sorted(CLUB_KITS, key=len, reverse=True):
        if frag in t:
            return CLUB_KITS[frag]
    h = sum(ord(c) * (i + 3) for i, c in enumerate(team or name)) % 360
    pat = ("hoops", "vstripes", "sash", "band")[zlib.crc32((team or name).encode()) % 4]
    return f"hsl({h} 58% 38%)", f"hsl({h} 60% 58%)", pat


def card_color(team: str, name: str = "") -> str:
    """Club colour for backgrounds: the secondary colour when the main one is white or very light."""
    base, alt, _ = kit(team, name)
    return alt if _is_light(base) and not _is_light(alt) else base


def team_hue(name: str, team: str = "") -> int:
    return sum(ord(c) * (i + 3) for i, c in enumerate(team or name)) % 360


def _is_light(c: str) -> bool:
    if c.startswith("#") and len(c) == 7:
        r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        return (0.299 * r + 0.587 * g + 0.114 * b) > 150
    return False


_SHIRT = "M21 8 L28 5 Q32 11 36 5 L43 8 L58 19 L51 30 L45 26 V58 H19 V26 L13 30 L6 19 Z"


def avatar(name: str, team: str = "", size: str = "") -> str:
    """A generic football shirt in the club's main colours with the player's initials. An illustration:
    no crest, sponsor or likeness of anyone."""
    parts = [w for w in re.split(r"[\s.-]+", name) if w]
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "?"
    base, alt, pat = kit(team, name)
    cid = f"k{zlib.crc32((team or name).encode()):x}"
    if pat == "hoops":
        deco = "".join(f'<rect x="0" y="{y}" width="64" height="6" fill="{alt}"/>' for y in range(14, 60, 12))
    elif pat == "vstripes":
        deco = "".join(f'<rect x="{x}" y="0" width="7" height="64" fill="{alt}"/>' for x in range(13, 52, 14))
    elif pat == "sash":
        deco = f'<polygon points="6,8 20,8 64,52 64,64 50,64" fill="{alt}"/>'
    elif pat == "band":
        deco = f'<rect x="0" y="27" width="64" height="9" fill="{alt}"/>'
    elif pat == "sleeves":
        deco = (f'<rect x="0" y="0" width="19" height="64" fill="{alt}"/><rect x="45" y="0" width="19" height="64" fill="{alt}"/>')
    else:
        deco = ""
    on_light = _is_light(base) if pat in ("solid", "sleeves") else not _is_light(base) and False
    txt, edge = ("#111827", "#ffffff") if on_light else ("#ffffff", "#0b1020")
    svg = (f'<svg viewBox="0 0 64 64" role="img" aria-hidden="true"><defs><clipPath id="{cid}">'
           f'<path d="{_SHIRT}"/></clipPath></defs><path d="{_SHIRT}" fill="{base}"/>'
           f'<g clip-path="url(#{cid})">{deco}</g>'
           f'<path d="{_SHIRT}" fill="none" stroke="rgba(0,0,0,.35)" stroke-width="1"/>'
           f'<path d="M26 6 Q32 15 38 6" fill="none" stroke="{alt}" stroke-width="2.6" stroke-linecap="round"/>'
           f'<text x="32" y="44" text-anchor="middle" font-size="15" font-weight="800" fill="{txt}" '
           f'stroke="{edge}" stroke-width="2.2" paint-order="stroke" stroke-linejoin="round" '
           f'font-family="Inter,Arial,sans-serif">{escape(initials)}</text></svg>')
    return f'<span class="av {size}" style="--kc:{base}">{svg}</span>'




OUTCOMES = {"goal": "Goal", "saved": "Saved", "woodwork": "Hit woodwork", "off_target": "Off target",
            "blocked": "Blocked", "own_goal": "Own goal"}
BODY = {"right_foot": "Right foot", "left_foot": "Left foot", "header": "Header", "other": "Other / unknown"}
SITUATION = {"open_play": "Open play", "from_corner": "After a corner", "free_kick": "Free kick",
             "penalty": "Penalty"}

_PITCH = (
    '<rect class="turf" x="-20" y="-28" width="720" height="580" rx="10"/>'
    '<rect class="pl" x="0" y="0" width="680" height="525"/>'
    '<rect class="pl" x="138.5" y="0" width="403" height="165"/>'
    '<rect class="pl" x="248.4" y="0" width="183.2" height="55"/>'
    '<rect class="pl goal" x="303.4" y="-18" width="73.2" height="18"/>'
    '<path class="pl" d="M266.9 165 A91.5 91.5 0 0 0 413.1 165"/>'
    '<path class="pl" d="M248.5 525 A91.5 91.5 0 0 1 431.5 525"/>'
    '<circle cx="340" cy="110" r="3" fill="var(--pline)"/>')


def shot_map(shots: list[dict], mini: bool = False) -> tuple[str, int]:
    """Half-pitch SVG, attacking goal at the top. Returns (svg, number of shots drawn).
    mini: a tighter crop of the attacking third with larger dots, for cards."""
    dots, drawn = [], 0
    for s in sorted(shots, key=lambda s: s["o"] == "goal"):            # goals last, so they sit on top
        if s["x"] is None:
            continue
        cx, cy = s["y"] / 100 * 680, (100 - s["x"]) / 100 * 1050
        if cy > (400 if mini else 540):                              # beyond the halfway line (or the crop)
            continue
        drawn += 1
        tip = " · ".join(t for t in (f"{s['m']}'" if s["m"] is not None else "", s["d"],
                                     BODY.get(s["b"], ""), SITUATION.get(s["s"], ""),
                                     OUTCOMES.get(s["o"], "")) if t)
        r = (12 if s["o"] == "goal" else 6.5) if mini else (11 if s["o"] == "goal" else 8)
        dots.append(f'<circle class="dot s-{escape(str(s["o"]))}" cx="{cx:.0f}" cy="{cy:.0f}" r="{r}">'
                    f'<title>{escape(tip)}</title></circle>')
    box = "-20 -28 720 450" if mini else "-20 -28 720 580"
    svg = (f'<svg viewBox="{box}" role="img" aria-label="Shot map">{_PITCH}{"".join(dots)}</svg>')
    return svg, drawn




# ---------------------------------------------------------------- templates
def _fmt(v, nd=2):
    return "-" if v is None else f"{v:,.{nd}f}"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(SITE_NAME=SITE_NAME, OWNER=OWNER, OWNER_LINKS=OWNER_LINKS, REPO_URL=REPO_URL, SITE_URL=SITE_URL,
                       NAV=NAV, lname=lname, KIND_LABEL=KIND_LABEL,
                       avatar=lambda *a, **k: Markup(avatar(*a, **k)), fmt=_fmt)
    env.filters["thousands"] = lambda v: f"{v:,}"
    return env


_ENV = _env()


def render(template: str, **ctx) -> str:
    return _ENV.get_template(template).render(**ctx)


# ---------------------------------------------------------------- data preparation
def _cell(row: dict, key: str):
    """Apps and minutes show '-' until a lineup has been stored for the player."""
    if key in ("apps", "minutes"):
        return f"{row[key]:,}" if row["lineup_matches"] else "-"
    v = row.get(key)
    if v is None:
        return "-"
    return f"{v:.2f}" if isinstance(v, float) else f"{v:,}"


COLS_STD = [("Apps", "apps", "opt"), ("Min", "minutes", "opt"), ("Shots", "shots", ""),
            ("On target", "shots_on_target", ""), ("Goals", "goals", ""), ("Assists", "assists", "")]
COLS_90 = [("Min", "minutes", "opt"), ("On target", "shots_on_target", ""), ("Per 90", "sot90", "")]
TABS = [("sot", "On target", "shots_on_target", COLS_STD), ("goals", "Goals", "goals", COLS_STD),
        ("assists", "Assists", "assists", COLS_STD), ("p90", "On target per 90", "sot90", COLS_90),
        ("shots", "Shots", "shots", COLS_STD)]
BEST_TABS = [("sot", "Most on target", "shots_on_target"), ("goals", "Most goals", "goals"),
             ("p90", "On target per 90", "sot90")]


def board_data(rows: list[dict], key: str, cols: list, linked: set, lg: str, n: int) -> dict:
    """One leaderboard as plain data for the template: header cells and rows of formatted cells."""
    pool = [r for r in rows if r.get(key) is not None] if key == "sot90" else list(rows)
    pool.sort(key=lambda r: (-(r.get(key) or 0), -r["shots"], r["player"]))
    pool = pool[:n]
    top_v = max((r.get(key) or 0) for r in pool) or 1 if pool else 1
    out = []
    for i, r in enumerate(pool, 1):
        out.append({"rank": i, "name": r["player"], "team": r["team"],
                    "url": player_url(lg, r["pid"]) if r["pid"] in linked else "",
                    "cells": [{"text": _cell(r, k), "cls": c, "hl": k == key,
                               "w": round(100 * (r.get(k) or 0) / top_v)} for _, k, c in cols]})
    return {"head": [(lab, c) for lab, _, c in cols], "rows": out, "min_minutes": MIN_MINUTES_PER90}


def result_item(m: dict, league: str = "") -> dict:
    (h, hs), (a, as_) = sorted(zip(m["teams"], m["scores"], strict=True), key=lambda p: not p[0].get("home"))
    return {"date": m["date"], "home": h["name"], "away": a["name"], "hs": hs, "as_": as_, "league": league,
            "url": _MATCH_URLS.get((m.get("league"), m.get("event_id")), "")}


def _span(c: dict) -> str:
    return f"{c['first']} to {c['last']}" if c.get("first") else "dates unknown"


def notes_view(notes: list[dict], linked: set) -> list[dict]:
    return [{**n, "label": KIND_LABEL[n["kind"]], "league_name": lname(n["league"]),
             "url": player_url(n["league"], n["pid"]) if n["pid"] and (n["league"], n["pid"]) in linked else ""}
            for n in notes]


def index_page(model: dict, players_n: int, stamp: str, notes: list[dict], watch: list[dict]) -> str:
    leagues = model["leagues"]
    total = sum(v["cov"]["matches"] for v in leagues.values())
    cards_ = [{"slug": lg, "name": lname(lg), "matches": leagues[lg]["cov"]["matches"],
               "span": _span(leagues[lg]["cov"]), "lead": leagues[lg]["rows"][0] if leagues[lg]["rows"] else None}
              for lg in sorted(leagues, key=lname)]
    latest = sorted(((m, lname(lg)) for lg, v in leagues.items() for m in v["recent"][:6]),
                    key=lambda t: t[0]["date"], reverse=True)[:10]
    hero = None
    if watch:
        w = watch[0]
        hero = {**w, "svg": Markup(shot_map(w["shots"], mini=True)[0])}
    cards_w = [{**w, "svg": Markup(shot_map(w["shots"], mini=True)[0])} for w in watch]
    return render("index.html", title=f"{SITE_NAME}: shots, goals and shot maps by player",
                  desc="Automatically updated player leaderboards, shot maps and forecasts for the top soccer competitions.",
                  path="index.html", image="og-home.png", page_id="leagues", total=total, players_n=players_n,
                  competitions=cards_, results=[result_item(m, n) for m, n in latest], hero=hero, watch=cards_w,
                  notes=notes, n_comp=len(leagues))


def league_page(lg: str, v: dict, linked: set, n: int) -> str:
    boards = [{"id": tid, "label": label, **board_data(v["rows"], key, cols, linked, lg, n)}
              for tid, label, key, cols in TABS]
    teams = [{"name": t["name"], "url": team_url(lg, t["name"]), "p": t["p"], "w": t["w"], "d": t["d"], "l": t["l"]}
             for t in sorted(v["teams"].values(), key=lambda t: t["name"])]
    return render("league.html", title=f"{lname(lg)} leaderboards | {SITE_NAME}",
                  desc=f"Shots on target, goals and assists leaders in the {lname(lg)}.", path=f"{lg}.html",
                  image=f"og-{lg}.png", page_id="leagues", lg=lg, name=lname(lg), color=LEAGUE_COLOR.get(lg, "#1d2f55"),
                  cov=v["cov"], span=_span(v["cov"]), boards=boards, n=n, teams=teams, filter_data=f"data/{lg}.json",
                  results=[result_item(m) for m in v["recent"][:10]])


def team_page(lg: str, t: dict, v: dict, linked: set) -> str:
    squad = sorted((r for r in v["rows"] if r["team"] == t["name"]),
                   key=lambda r: (-r["shots_on_target"], -r["shots"], r["player"]))[:20]
    players = [{"name": r["player"], "url": player_url(lg, r["pid"]) if r["pid"] in linked else "", "shots": r["shots"],
                "sot": r["shots_on_target"], "goals": r["goals"], "assists": r["assists"]} for r in squad]
    games = [m for m in v["recent"] if any(x["name"] == t["name"] for x in m["teams"])][:8]
    return render("team.html", title=f"{t['name']} | {lname(lg)} | {SITE_NAME}", desc="", path=team_url(lg, t["name"]),
                  image="og-home.png", page_id="leagues", lg=lg, league_name=lname(lg), t=t, gd=t["gf"] - t["ga"],
                  color=card_color(t["name"]), players=players, results=[result_item(m) for m in games])


def player_page(lg: str, r: dict, shots: list[dict], ranks: dict, card: bool = False,
                form: dict | None = None, career: list[dict] | None = None, stints: list[dict] | None = None) -> str:
    kpis = [("Apps", _cell(r, "apps")), ("Minutes", _cell(r, "minutes")), ("Shots", r["shots"]),
            ("On target", r["shots_on_target"]), ("Goals", r["goals"]), ("Assists", r["assists"]),
            ("On target / 90", _cell(r, "sot90"))]
    svg, drawn = shot_map(shots)

    def counts(key: str, labels: dict) -> list:
        tally: dict[str, int] = {}
        for s in shots:
            k = s[key] or "other"
            tally[k] = tally.get(k, 0) + 1
        return [[labels.get(k, str(k)), c] for k, c in sorted(tally.items(), key=lambda kv: -kv[1])]

    url = player_url(lg, r["pid"])
    return render("player.html", title=f"{r['player']} | {lname(lg)} | {SITE_NAME}",
                  desc=f"{r['player']}: shots, goals, assists and shot map in the {lname(lg)}.", path=url,
                  image="og-" + url.replace(".html", ".png") if card else "og-home.png", page_id="leagues",
                  lg=lg, league_name=lname(lg), r=r, team_link=team_url(lg, r["team"]),
                  color=card_color(r["team"], r["player"]), sot_rank=ranks["sot"][r["pid"]],
                  goal_rank=ranks["goals"][r["pid"]], size=ranks["size"], kpis=kpis, svg=Markup(svg), drawn=drawn,
                  n_shots=len(shots),
                  charts=[("Outcome", counts("o", OUTCOMES)), ("Body part", counts("b", BODY)),
                          ("Situation", counts("s", SITUATION))] if shots else [],
                  legend=[("goal", "accent-hi"), ("saved", "blue"), ("woodwork", "amber"), ("off_target", "red"),
                          ("blocked", "grey")], OUTCOMES=OUTCOMES, form=form,
                  career=[{**c, "league_name": lname(c["league"])} for c in career or []], stints=stints or [],
                  aid=r["athlete_id"])


def compare_page() -> str:
    return render("compare.html", title=f"Compare players | {SITE_NAME}",
                  desc="Put two players side by side: totals, shooting accuracy and shot maps.", path="compare.html",
                  image="og-home.png", page_id="compare")


def match_page(view: dict) -> str:
    sides = [{**s, "svg": Markup(shot_map(s["shots"])[0]), "drawn": shot_map(s["shots"])[1]} for s in view["sides"]]
    a, b = sides
    return render("match.html", title=f"{a['name']} v {b['name']} | {lname(view['league'])} | {SITE_NAME}",
                  desc=f"{a['name']} {a['score']}-{b['score']} {b['name']}: shots, shot maps and forecast versus actual.",
                  path=match_url(view["league"], view["event_id"]), image="og-home.png", page_id="leagues",
                  v=view, sides=sides, league_name=lname(view["league"]), lg=view["league"],
                  has_pred=any(p["pred"] is not None for s in sides for p in s["players"]))


def news_page(notes: list[dict], linked: set) -> str:
    view = notes_view(notes, linked)
    days: dict[str, list[dict]] = {}
    for n in sorted(view, key=lambda n: n["date"], reverse=True):
        days.setdefault(n["date"], []).append(n)
    kinds = [k for k in KIND_LABEL if any(n["kind"] == k for n in view)]
    lgs = sorted({n["league"] for n in view}, key=lname)
    return render("news.html", title=f"News | {SITE_NAME}",
                  desc="Automatically written shot and goal stories from the latest matches.", path="news.html",
                  image="og-home.png", page_id="news", days=days, kinds=kinds, lgs=lgs)


def best_rows(rows: list[dict], key: str, linked: set, n: int = 10) -> dict:
    pool = [r for r in rows if r.get(key) is not None]
    pool.sort(key=lambda r: (-r[key], -r["shots"], r["player"]))
    pool = pool[:n]
    top_v = pool[0][key] or 1 if pool else 1
    out = []
    for i, r in enumerate(pool, 1):
        lg = r["best_league"]
        comps = ", ".join(lname(k) for k, _ in sorted(r["leagues"].items(), key=lambda kv: -kv[1])[:3])
        out.append({"rank": i, "name": r["player"], "team": r["team"], "comps": comps, "minutes": r["minutes"],
                    "sot": r["shots_on_target"], "goals": r["goals"], "w": round(100 * r[key] / top_v),
                    "value": f"{r[key]:.2f}" if isinstance(r[key], float) else f"{r[key]:,}",
                    "url": player_url(lg, r["best_pid"]) if (lg, r["best_pid"]) in linked else ""})
    return {"rows": out, "per90": key == "sot90"}


def best_page(rows: list[dict], linked: set, span: str) -> str:
    boards = [{"id": tid, "label": label, **best_rows(rows, key, linked)} for tid, label, key in BEST_TABS]
    return render("best.html", title=f"Best of the best | {SITE_NAME}",
                  desc="Top 10 players by shots on target, goals and shots on target per 90 across every stored competition.",
                  path="best.html", image="og-home.png", page_id="best", boards=boards, span=span,
                  min_minutes=MIN_MINUTES_BEST)


def matchday_data(fc: dict | None, per_match: int = 3) -> dict:
    """Top expected shooters per match, keyed 'league:event_id', for the Match Day page."""
    out: dict[str, list] = {}
    for p in (fc or {}).get("matchday", []):
        lst = out.setdefault(f'{p["league"]}:{p["event_id"]}', [])
        if len(lst) < per_match:
            lst.append({"p": p["player"], "t": p["team"], "x": p["pred"]})
    return out


def live_page(fc: dict | None = None) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "Amir-Mirkazemi/shotcast")
    return render("live.html", title=f"Match Day | {SITE_NAME}",
                  desc="Near-live scores with a shots-on-target forecast for each match.", path="live.html",
                  image="og-home.png", page_id="live", src=f"https://raw.githubusercontent.com/{repo}/live-data/live.json",
                  names=LEAGUE_NAMES, fc=matchday_data(fc))


def forecast_page(fc: dict | None, sc: dict) -> str:
    ctx: dict = {"fc": fc, "sc": sc}
    if fc:
        ctx["md"] = [{**p, "date": p["kickoff"][:10], "league_name": lname(p["league"])} for p in fc["matchday"][:20]]
        ctx["seasons"] = [{"name": lname(lg), "rows": v["players"][:10]} for lg, v in fc["season"].items()]
        ctx["asof"] = fc["asof"][:16].replace("T", " ")
        card = fc.get("model") or {}
        ctx["card"] = card
        if card.get("results"):
            names = {"baseline": "Baseline (own recent rate x minutes)", "league average": "Everyone gets the average",
                     "poisson": "Poisson regression", "boosted": "Gradient-boosted Poisson (scikit-learn)",
                     "lightgbm": "LightGBM (Poisson)"}
            ctx["methods"] = [{"name": names.get(k, k), "used": k == card["best"] and card["beats_baseline"], **v}
                              for k, v in card["results"].items()]
            ctx["best_name"] = names.get(card["best"], card["best"])
        ctx["has_prob"] = any("p1" in p for p in ctx["md"])
    return render("forecast.html", title=f"Forecast | {SITE_NAME}",
                  desc="Shots on target forecasts with a public backtest and scorecard.", path="forecast.html",
                  image="og-home.png", page_id="forecast", **ctx)


def call_page(fc: dict | None) -> str:
    seasons = {lg: v for lg, v in (fc or {}).get("season", {}).items() if v["players"]}
    return render("call.html", title=f"Call your shot | {SITE_NAME}",
                  desc="Pick who will finish the season with the most shots on target and compare with the model.",
                  path="call.html", image="og-home.png", page_id="call", seasons=seasons, names=LEAGUE_NAMES)


def about_page() -> str:
    return render("about.html", title=f"About | {SITE_NAME}", desc=f"{SITE_NAME} is a soccer data project built by {OWNER}.",
                  path="about.html", image="og-home.png", page_id="about")


def how_page(model: dict, stamp: str) -> str:
    cov = [v["cov"] for v in model["leagues"].values()]
    total, ok = sum(c["matches"] for c in cov), sum(c["ok"] for c in cov)
    return render("how.html", title=f"How it is built | {SITE_NAME}",
                  desc="Architecture and data-quality notes for Shotcast.", path="how.html", image="og-home.png",
                  page_id="how", stamp=stamp, total=total, ok=ok, pct=100 * ok / total if total else 0)


def health_page(report: dict, stamp: str) -> str:
    report = {**report, "leagues": [{**c, "name": lname(c["league"])} for c in report["leagues"]]}
    return render("health.html", title=f"Data health | {SITE_NAME}", desc="Freshness, validation and test results for the data behind the site.",
                  path="health.html", image="og-home.png", page_id="health", r=report, stamp=stamp)


def method_page(model: dict, stamp: str) -> str:
    rows = [{"name": lname(lg), **c, "pct": 100 * c["ok"] / c["matches"] if c["matches"] else 0, "span": _span(c)}
            for lg, c in sorted(((k, v["cov"]) for k, v in model["leagues"].items()), key=lambda kv: lname(kv[0]))]
    return render("method.html", title=f"Method and data quality | {SITE_NAME}", desc="", path="method.html",
                  image="og-home.png", page_id="method", stamp=stamp, rows=rows, min_per90=MIN_MINUTES_PER90)


# ---------------------------------------------------------------- build
def _copy_static(out: Path) -> list[Path]:
    made = []
    for src in sorted(STATIC.rglob("*")):
        if src.is_file():
            dst = out / src.relative_to(STATIC)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            made.append(dst)
    return made


def _match_pages(recs: list[dict], preds: dict) -> dict[str, str]:
    """Pages for recent matches, and for any older match that has a saved forecast to compare with."""
    _MATCH_URLS.clear()
    newest = max(((r.get("date") or "")[:10] for r in recs), default="")
    cutoff = ""
    if newest:
        y, m, d = (int(x) for x in newest.split("-"))
        cutoff = (datetime(y, m, d) - timedelta(days=MATCH_DAYS)).strftime("%Y-%m-%d")
    chosen = [r for r in recs if (r.get("date") or "")[:10] >= cutoff or (r["league"], str(r["event_id"])) in preds]
    views = {}
    for r in chosen:
        v = match_view(r, preds.get((r["league"], str(r["event_id"]))))
        if v:
            views[(r["league"], str(r["event_id"]))] = v
            _MATCH_URLS[(r["league"], str(r["event_id"]))] = match_url(r["league"], r["event_id"])
    return {match_url(lg, eid): match_page(v) for (lg, eid), v in views.items()}


def _compare_files(allrows: list[dict], linked: set, model: dict, career: dict) -> dict[str, str]:
    """compare.json (one entry per player with a page) and one small shots file per player, loaded on demand."""
    index, files = [], {}
    for r in allrows:
        key = (r["best_league"], r["best_pid"])
        aid = r["athlete_id"]
        if key not in linked or not aid:
            continue
        index.append({"id": aid, "name": r["player"], "team": r["team"], "color": card_color(r["team"], r["player"]),
                      "url": player_url(*key), "comp": lname(key[0]), "apps": r["apps"], "min": r["minutes"],
                      "shots": r["shots"], "sot": r["shots_on_target"], "goals": r["goals"], "assists": r["assists"],
                      "sot90": None if r["sot90"] is None else round(r["sot90"], 2),
                      "kit": list(kit(r["team"], r["player"]))})
        files[f"shots/{aid}.json"] = json.dumps(model["shots"].get(key, []), separators=(",", ":"))
    files["compare.json"] = json.dumps(index, separators=(",", ":"), ensure_ascii=False)
    return files


def build(store, out_dir: str | Path, now: datetime | None = None, top_n: int = 50) -> list[Path]:
    recs, invalid = read_valid(store)
    players, coverage = totals_from_records(recs)
    if not coverage:
        raise SystemExit("No matches stored yet: nothing to build.")
    model = build_model(recs, players, coverage)
    fcs = load_forecasts(store)
    latest_fc = fcs[-1] if fcs else None
    sc = scorecard(fcs, recs)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    report = build_report(store, recs, invalid, today=(now.date() if now else None))
    linked_all = {(lg, pid) for lg, v in model["leagues"].items() for pid in featured_ids(v["rows"])}
    allrows = combined_rows(players)
    watch = []
    for r in sorted(allrows, key=lambda r: (-r["shots_on_target"], r["player"])):
        key = (r["best_league"], r["best_pid"])
        if key in linked_all and model["shots"].get(key):
            watch.append({"name": r["player"], "team": r["team"], "sot": r["shots_on_target"], "goals": r["goals"],
                          "url": player_url(*key), "shots": model["shots"][key], "color": card_color(r["team"], r["player"])})
        if len(watch) == 6:
            break
    home_notes = notes_view(shot_notes(recs, players, lname), linked_all)
    series, career = player_history(recs)
    preds = latest_predictions(fcs)
    match_files = _match_pages(recs, preds)
    files: dict[str, str] = {
        ".nojekyll": "",
        "index.html": index_page(model, len(players), stamp, home_notes, watch),
        "method.html": method_page(model, stamp), "about.html": about_page(), "live.html": live_page(latest_fc),
        "how.html": how_page(model, stamp), "news.html": news_page(shot_notes(recs, players, lname, limit=60), linked_all),
        "health.html": health_page(report, stamp), "forecast.html": forecast_page(latest_fc, sc), "call.html": call_page(latest_fc), "compare.html": compare_page()}
    files.update(match_files)
    files.update(_compare_files(allrows, linked_all, model, career))
    search = []
    spans = ([c["first"] for c in coverage.values() if c.get("first")]
             + [c["last"] for c in coverage.values() if c.get("last")])
    files["best.html"] = best_page(allrows, linked_all, f"{min(spans)} to {max(spans)}" if spans else "")
    for lg, v in model["leagues"].items():
        linked = featured_ids(v["rows"])
        files[f"{lg}.html"] = league_page(lg, v, linked, top_n)
        files[f"data/{lg}.json"] = json.dumps(season_rows(career, lg, linked), separators=(",", ":"), ensure_ascii=False)
        ranks = {"sot": rank_map(v["rows"], ("shots_on_target", "shots")),
                 "goals": rank_map(v["rows"], ("goals", "shots_on_target")), "size": len(v["rows"])}
        for r in v["rows"]:
            if r["pid"] in linked:
                aid = r["athlete_id"]
                files[player_url(lg, r["pid"])] = player_page(
                    lg, r, model["shots"].get((lg, r["pid"]), []), ranks, card=cards.available(),
                    form=form_chart(series.get(aid, [])) if aid else None,
                    career=career_table(career[aid]) if aid in career else None,
                    stints=club_stints(career[aid]) if aid in career else None)
            search.append({"player": r["player"], "team": r["team"], "league": lg, "league_name": lname(lg),
                           "url": player_url(lg, r["pid"]) if r["pid"] in linked else "",
                           "rank": ranks["sot"][r["pid"]], "field": len(v["rows"]), "shots": r["shots"],
                           "shots_on_target": r["shots_on_target"], "goals": r["goals"], "assists": r["assists"]})
        for t in v["teams"].values():
            files[team_url(lg, t["name"])] = team_page(lg, t, v, linked)
    files["players.json"] = json.dumps(search, separators=(",", ":"), ensure_ascii=False)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = _copy_static(out)
    for name, text in files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    if cards.available():
        paths += preview_images(out, model, players, coverage, allrows)
    return paths


def preview_images(out: Path, model: dict, players: dict, coverage: dict, allrows: list[dict]) -> list[Path]:
    """The 1200x630 pictures that LinkedIn and other sites show when a link is shared."""
    total = sum(c["matches"] for c in coverage.values())
    made = [cards.render_card(out / "og-home.png", "Soccer shots, shown and forecast",
                              f"{len(coverage)} competitions · {len(players):,} players",
                              [(f"{total:,}", "matches"), (f"{len(coverage)}", "competitions"),
                               (f"{len(players):,}", "players")])]
    for lg, v in model["leagues"].items():
        top = sorted(v["rows"], key=lambda r: (-r["shots_on_target"], r["player"]))[:1]
        sub = f"Most on target: {top[0]['player']} ({top[0]['shots_on_target']})" if top else ""
        made.append(cards.render_card(out / f"og-{lg}.png", lname(lg), sub,
                                      [(f"{v['cov']['matches']:,}", "matches"),
                                       (f"{len(v['rows']):,}", "players")]))
    for lg, v in model["leagues"].items():
        for r in v["rows"]:
            url = player_url(lg, r["pid"])
            if (out / url).exists():
                shots = [{"x": s["x"], "y": s["y"], "o": s["o"]} for s in model["shots"].get((lg, r["pid"]), [])]
                made.append(cards.render_card(out / ("og-" + url.replace(".html", ".png")), r["player"],
                                              f"{r['team']} · {lname(lg)}",
                                              [(f"{r['shots_on_target']}", "on target"), (f"{r['goals']}", "goals"),
                                               (f"{r['shots']}", "shots")], shots))
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="storage root (default: PITCHLINE_ROOT)")
    ap.add_argument("--out", default="site")
    ap.add_argument("--top", type=int, default=50)
    a = ap.parse_args(argv)
    paths = build(get_store(a.root), a.out, top_n=a.top)
    print(f"Wrote {len(paths)} files to {a.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
