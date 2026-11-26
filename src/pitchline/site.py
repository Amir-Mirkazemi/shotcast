"""Static website built from the stored ESPN match files (hosted on GitHub Pages).

Pages: home (search, latest results, one card per competition), a leaderboard page per competition (on target,
goals, assists, on target per 90, shots), a page per team, a page per leading player with a shot map, and a
method page with data quality and known limits. Plain HTML, one CSS file, one small JS file, no framework.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from . import cards
from .forecast import load_forecasts, scorecard
from .leaderboard import read_records, totals_from_records
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

SITE_NAME = "Shotcast"
OWNER = "Amir Mirkazemi"
OWNER_LINKS = (("LinkedIn", "https://www.linkedin.com/in/amir-mirkazemi/"),
               ("GitHub", "https://github.com/Amir-Mirkazemi"), ("amirdata.com", "https://www.amirdata.com/"))
# absolute address of the published site, needed for link previews (set by the workflow)
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
# in GitHub Actions the link follows the repository name, so renaming the repo does not break it
REPO_URL = "https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "Amir-Mirkazemi/pitchline-live")

LEAGUE_NAMES = {
    "eng.1": "Premier League", "esp.1": "La Liga", "ita.1": "Serie A", "fra.1": "Ligue 1",
    "ger.1": "Bundesliga", "usa.1": "MLS", "uefa.champions": "Champions League",
    "afc.champions": "AFC Champions League", "fifa.world": "World Cup",
}

CSS = """
:root { --bg:#f6f5ef; --panel:#fff; --panel2:#f0efe6; --line:#e2e0d3; --text:#1b201c; --muted:#657067;
  --accent:#16833a; --ink:#fff; --blue:#1479b8; --amber:#c88a00; --red:#d64545; --grey:#8a948d;
  --pitch:#e3f1d9; --pline:#fff; --glow:#dcefd0;
  --shadow:0 1px 2px rgba(20,30,20,.06), 0 6px 18px rgba(20,30,20,.05); }
:root[data-theme="dark"] { --bg:#0a0f0d; --panel:#111915; --panel2:#17211b; --line:#223028; --text:#e8f0ea;
  --muted:#8aa196; --accent:#b6f23f; --ink:#0a0f0d; --blue:#4cc9f0; --amber:#ffd166; --red:#ff6b6b;
  --grey:#7f8f86; --pitch:#143222; --pline:#2d4a38; --glow:#14301f; --shadow:none; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; background:radial-gradient(1100px 360px at 50% -90px, var(--glow), var(--bg)) no-repeat var(--bg);
  color:var(--text);
  font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
a { color:var(--accent); text-underline-offset:3px; }
.wrap { max-width:1040px; margin:0 auto; padding:0 18px; }
.top { border-bottom:1px solid var(--line); background:var(--panel); position:sticky; top:0; z-index:5; }
.top .wrap { display:flex; align-items:center; gap:22px; height:56px; }
.brand { display:flex; align-items:center; gap:9px; color:var(--text); text-decoration:none; font-weight:800;
  letter-spacing:.01em; font-size:1.1rem; white-space:nowrap; }
.brand svg { width:26px; height:26px; color:var(--accent); }
.top nav { display:flex; gap:18px; margin-left:auto; font-size:.92rem; }
.top nav a { color:var(--muted); text-decoration:none; } .top nav a:hover { color:var(--text); }
.top nav button { background:none; border:1px solid var(--line); color:var(--muted); border-radius:999px;
  padding:3px 12px; font:inherit; font-size:.82rem; cursor:pointer; } .top nav button:hover { color:var(--text); }
.hero { padding:44px 0 26px; }
.hero h1 { font-size:clamp(2rem,5vw,3.2rem); line-height:1.08; margin:0 0 12px; letter-spacing:-.02em; }
.hero h1 em { font-style:normal; color:var(--accent); }
.hero p { color:var(--muted); max-width:620px; margin:0 0 22px; font-size:1.05rem; }
.search { width:100%; max-width:480px; padding:13px 16px; font-size:1rem; color:var(--text);
  background:var(--panel); border:1px solid var(--line); border-radius:10px; outline:none; }
.search:focus { border-color:var(--accent); }
.strip { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:26px 0 8px; }
.kpi { box-shadow:var(--shadow); background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.kpi b { display:block; font-size:1.5rem; line-height:1.2; font-variant-numeric:tabular-nums;
  white-space:nowrap; }
.kpi span { color:var(--muted); font-size:.82rem; }
h2 { font-size:1.15rem; margin:34px 0 12px; letter-spacing:.01em; }
h2 small { color:var(--muted); font-weight:400; font-size:.85rem; margin-left:8px; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(235px,1fr)); gap:12px; }
.card { box-shadow:var(--shadow); display:block; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px;
  color:var(--text); text-decoration:none; transition:border-color .15s, transform .15s; }
.card:hover { border-color:var(--accent); transform:translateY(-2px); }
.card h3 { margin:0 0 2px; font-size:1.05rem; }
.card .sub { color:var(--muted); font-size:.8rem; }
.card .lead { margin-top:10px; font-size:.88rem; color:var(--muted); }
.card .lead b { color:var(--accent); font-variant-numeric:tabular-nums; }
.results { box-shadow:var(--shadow); list-style:none; margin:0; padding:0; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; }
.results li { display:grid; grid-template-columns:78px 1fr auto 1fr auto; gap:10px; padding:10px 14px; align-items:center;
  border-bottom:1px solid var(--line); font-size:.92rem; }
.results li:last-child { border-bottom:0; }
.results .d { color:var(--muted); font-size:.8rem; white-space:nowrap; } .results .h { text-align:right; }
.results .sc { font-weight:800; font-variant-numeric:tabular-nums; background:var(--panel2); padding:2px 10px;
  border-radius:6px; }
.results li.on .d { color:var(--green,#16833a); font-weight:700; } .results li.on .sc { background:var(--green,#16833a); color:#fff; }
.results .fcl { grid-column:1 / -1; color:var(--muted); font-size:.78rem; }
.results .lg { color:var(--muted); font-size:.75rem; text-align:right; min-width:92px; }
.tabs { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 12px; }
.pick { background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:999px; padding:3px 12px; cursor:pointer; font:inherit; font-size:.8rem; }
.pick[aria-pressed=true] { background:var(--accent); border-color:var(--accent); font-weight:700; }
#call-lg { font:inherit; padding:8px 12px; border:1px solid var(--line); border-radius:10px; background:var(--panel); color:var(--ink); margin:8px 0; }
h3 { margin:22px 0 8px; font-size:1.05rem; }
.tabs button { background:var(--panel); color:var(--muted); border:1px solid var(--line); border-radius:999px;
  padding:7px 14px; font:inherit; font-size:.88rem; cursor:pointer; }
.tabs button[aria-pressed=true] { background:var(--accent); color:var(--ink); border-color:var(--accent);
  font-weight:700; }
.tablewrap { box-shadow:var(--shadow); overflow-x:auto; background:var(--panel); border:1px solid var(--line); border-radius:12px; }
table { border-collapse:collapse; width:100%; }
th, td { padding:9px 12px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }
tr:last-child td { border-bottom:0; }
th { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); cursor:pointer;
  background:var(--panel2); }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
td.rk { color:var(--muted); width:42px; }
td.who a { color:var(--text); font-weight:600; text-decoration:none; } td.who a:hover { color:var(--accent); }
td.who small { display:block; color:var(--muted); font-size:.78rem; font-weight:400; }
td.hl { position:relative; font-weight:800; min-width:96px; }
td.hl i { position:absolute; left:0; top:0; bottom:0; width:var(--w); background:var(--accent); opacity:.16; }
td.hl b { position:relative; }
.crumbs { margin:22px 0 0; font-size:.85rem; color:var(--muted); } .crumbs a { color:var(--muted); }
.ph { padding:10px 0 4px; } .ph h1 { margin:6px 0 2px; font-size:clamp(1.7rem,4vw,2.4rem); letter-spacing:-.02em; }
.ph .meta { color:var(--muted); }
.badge { display:inline-block; background:var(--panel2); border:1px solid var(--line); border-radius:999px;
  padding:3px 11px; font-size:.8rem; margin:8px 6px 0 0; } .badge b { color:var(--accent); }
.mapbox { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(0,1fr); gap:18px; align-items:start; }
@media (max-width:760px) { .mapbox { grid-template-columns:1fr; } .opt { display:none; } .results li {
  grid-template-columns:74px 1fr auto 1fr; padding:9px 10px; font-size:.86rem; } .results .lg { display:none; }
  .results .d { font-size:.74rem; } .kpi b { font-size:1.25rem; }
  .top .wrap { gap:12px; } .top nav { gap:10px; font-size:.8rem; flex-wrap:wrap; justify-content:flex-end; } .top .wrap { height:auto;
  padding-top:8px; padding-bottom:8px; } }
.av { width:44px; height:44px; border-radius:50%; display:inline-grid; place-items:center; flex:none;
  background:hsl(var(--h) 52% 36%); color:#fff; font-weight:800; font-size:.95rem; letter-spacing:.02em; }
.av.lg { width:72px; height:72px; font-size:1.5rem; }
.phead { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.phead > div:nth-child(2) { flex:1; min-width:200px; }
.herostat { text-align:right; } .herostat b { display:block; font-size:clamp(2.6rem,8vw,4rem); line-height:1;
  color:var(--accent); font-variant-numeric:tabular-nums; } .herostat span { color:var(--muted); font-size:.85rem; }
.wcards { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:14px; }
.wcard { display:block; background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px;
  color:var(--text); text-decoration:none; box-shadow:var(--shadow); transition:transform .15s, border-color .15s; }
.wcard:hover { transform:translateY(-3px); border-color:var(--accent); }
.wh { display:flex; align-items:center; gap:10px; margin-bottom:10px; } .wh b { display:block; }
.wh div span { color:var(--muted); font-size:.8rem; }
.pitch.mini { padding:6px; box-shadow:none; background:var(--panel2); }
.big { display:grid; grid-template-columns:3.4rem 1fr; gap:0 8px; align-items:baseline; margin-top:10px; }
.big b { font-size:1.9rem; line-height:1.15; color:var(--accent); font-variant-numeric:tabular-nums; }
.big span { color:var(--muted); font-size:.82rem; }
.notes { display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:10px; }
.note-card { display:flex; gap:10px; align-items:flex-start; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; padding:12px 14px; color:var(--text); text-decoration:none; box-shadow:var(--shadow); }
a.note-card:hover { border-color:var(--accent); }
.note-card .nt { font-size:.92rem; line-height:1.45; }
.kind { flex:none; font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.05em; padding:3px 8px;
  border-radius:999px; background:var(--panel2); border:1px solid var(--line); color:var(--accent); margin-top:2px; }
.pitch { box-shadow:var(--shadow); background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:10px; }
.pitch svg { width:100%; height:auto; display:block; }
.pl { fill:none; stroke:var(--pline); stroke-width:3; } .pl.goal { stroke:var(--muted); }
.turf { fill:var(--pitch); stroke:none; }
.dot { stroke:var(--panel); stroke-width:2; opacity:.95; }
.s-goal { fill:var(--accent); stroke:var(--text); stroke-width:3; } .s-saved { fill:var(--blue); }
.s-woodwork { fill:var(--amber); } .s-off_target { fill:var(--red); } .s-blocked { fill:var(--grey); }
.s-own_goal { fill:var(--grey); }
.legend { display:flex; flex-wrap:wrap; gap:6px 16px; margin:10px 2px 2px; font-size:.82rem; color:var(--muted); }
.legend i { display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:6px; vertical-align:-1px; }
.bars { box-shadow:var(--shadow); background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:12px 16px; }
.bars h3 { margin:6px 0 8px; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.bar { display:grid; grid-template-columns:110px 1fr 34px; gap:10px; align-items:center; font-size:.88rem;
  margin:5px 0; }
.bar div { background:var(--panel2); border-radius:4px; height:9px; overflow:hidden; }
.bar div i { display:block; height:100%; background:var(--accent); border-radius:4px; }
.bar span:last-child { text-align:right; font-variant-numeric:tabular-nums; color:var(--muted); }
.note { color:var(--muted); font-size:.88rem; max-width:760px; }
ul.plain { max-width:760px; color:var(--muted); } ul.plain li { margin:6px 0; }
footer { color:var(--muted); font-size:.82rem; padding:34px 0 44px; margin-top:30px; border-top:1px solid var(--line); }
"""

JS = """
const fold = t => t.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
const num = v => (v === '-' ? -1 : parseFloat(v.replace(/[#,]/g, '')));
document.querySelectorAll('table.sortable th').forEach((th, col) => th.addEventListener('click', () => {
  const body = th.closest('table').tBodies[0], dir = th.dataset.dir === 'desc' ? 1 : -1;
  th.dataset.dir = dir === 1 ? 'asc' : 'desc';
  const rows = [...body.rows], key = r => r.cells[col].innerText;
  rows.sort((a, b) => th.classList.contains('n') ? dir * (num(key(a)) - num(key(b)))
    : dir * key(a).localeCompare(key(b)));
  rows.forEach(r => body.appendChild(r));
}));
document.querySelectorAll('.tabs button').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.tabs button').forEach(b => b.setAttribute('aria-pressed', b === btn));
  document.querySelectorAll('.panel').forEach(p => { p.hidden = p.id !== btn.dataset.tab; });
}));
const root = document.documentElement, tb = document.getElementById('theme');
if (tb) {
  const label = () => { tb.textContent = root.dataset.theme === 'dark' ? 'Light' : 'Dark'; };
  label();
  tb.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {}
    label();
  });
}
const box = document.getElementById('q');
if (box) {
  let data = null;
  const out = document.getElementById('results');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  box.addEventListener('input', async () => {
    const q = fold(box.value.trim());
    if (!q) { out.innerHTML = ''; return; }
    if (!data) data = await (await fetch('players.json')).json();
    const hits = data.filter(p => fold(p.player).includes(q)).slice(0, 20);
    out.innerHTML = hits.length ? '<div class="tablewrap" style="margin-top:12px"><table><thead><tr>'
      + '<th>Player</th><th>Competition</th><th class="n">Rank</th><th class="n">Shots</th>'
      + '<th class="n">On target</th><th class="n">Goals</th><th class="n">Assists</th></tr></thead><tbody>'
      + hits.map(p => '<tr><td class="who">' + (p.url ? '<a href="' + esc(p.url) + '">' + esc(p.player) + '</a>'
      : esc(p.player)) + '<small>' + esc(p.team) + '</small></td><td><a href="' + esc(p.league) + '.html">'
      + esc(p.league_name) + '</a></td><td class="n">#' + p.rank + ' of ' + p.field + '</td><td class="n">'
      + p.shots + '</td><td class="n">' + p.shots_on_target + '</td><td class="n">' + p.goals
      + '</td><td class="n">' + p.assists + '</td></tr>').join('') + '</tbody></table></div>'
      : '<p class="note">No player found.</p>';
  });
}
"""

LOGO = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 21V5h16v16" fill="none" stroke="currentColor" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="14" r="2.6" '
        'fill="currentColor"/></svg>')
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M4 21V5h16v16' "
           "fill='none' stroke='%23b6f23f' stroke-width='2.4'/%3E%3Ccircle cx='12' cy='14' r='2.8' fill='%23b6f23f'/%3E%3C/svg%3E")


def lname(slug_: str) -> str:
    return LEAGUE_NAMES.get(slug_, slug_)


def page(title: str, body: str, desc: str = "", path: str = "", image: str = "") -> str:
    d = f'<meta name="description" content="{escape(desc)}">' if desc else ""
    og = ""
    if SITE_URL:
        og = (f'<meta property="og:site_name" content="{SITE_NAME}"><meta property="og:type" content="website">'
              f'<meta property="og:title" content="{escape(title)}">'
              f'<meta property="og:description" content="{escape(desc)}">'
              f'<meta property="og:url" content="{SITE_URL}/{escape(path)}">')
        if image:
            og += (f'<meta property="og:image" content="{SITE_URL}/{escape(image)}">'
                   '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
                   '<meta name="twitter:card" content="summary_large_image">')
    links = " · ".join(f'<a href="{u}">{n}</a>' for n, u in OWNER_LINKS)
    return f"""<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<title>{escape(title)}</title>{d}{og}
<link rel="icon" href="{FAVICON}">
<link rel="stylesheet" href="style.css"></head>
<body>
<div class="top"><div class="wrap"><a class="brand" href="index.html">{LOGO}{SITE_NAME}</a>
<nav><a href="best.html">Best of</a><a href="index.html">Leagues</a><a href="live.html">Match Day</a>
<a href="forecast.html">Forecast</a><a href="call.html">Call your shot</a><a href="about.html">About</a>
<button id="theme" type="button">Dark</button></nav>
</div></div>
<div class="wrap">{body}
<footer>Built by <b>{OWNER}</b> · {links}<br>Match data from ESPN (unofficial endpoints). Not affiliated with ESPN
or any league. <a href="method.html">Method</a> · <a href="how.html">How it is built</a> ·
<a href="{REPO_URL}">Source code</a></footer></div>
<script src="app.js"></script>
</body></html>
"""


def player_url(lg: str, pid: str) -> str:
    return f"p-{lg}-{slug(pid)}.html"


def team_url(lg: str, team: str) -> str:
    return f"t-{lg}-{slug(team)}.html"


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


def name_link(lg: str, r: dict, linked: set) -> str:
    name = escape(r["player"])
    return f'<a href="{player_url(lg, r["pid"])}">{name}</a>' if r["pid"] in linked else name


def board(rows: list[dict], key: str, cols: list, linked: set, lg: str, n: int) -> str:
    if key == "sot90":
        pool = [r for r in rows if r.get(key) is not None]
    else:
        pool = list(rows)
    pool.sort(key=lambda r: (-(r.get(key) or 0), -r["shots"], r["player"]))
    pool = pool[:n]
    if not pool:
        return (f'<p class="note">Not enough minutes data yet: this board needs players with at least '
                f'{MIN_MINUTES_PER90} minutes across matches that have stored lineups.</p>')
    top_v = max((r.get(key) or 0) for r in pool) or 1
    head = "".join(f"<th class='n {c}'>{lab}</th>" for lab, _, c in cols)
    body = []
    for i, r in enumerate(pool, 1):
        name = name_link(lg, r, linked)
        cells = []
        for _, k, c in cols:
            if k == key:
                w = 100 * (r.get(k) or 0) / top_v
                cells.append(f"<td class='n hl'><i style='--w:{w:.0f}%'></i><b>{_cell(r, k)}</b></td>")
            else:
                cells.append(f"<td class='n {c}'>{_cell(r, k)}</td>")
        body.append(f"<tr><td class='rk'>{i}</td><td class='who'>{name}"
                    f"<small>{escape(r['team'])}</small></td>{''.join(cells)}</tr>")
    return (f'<div class="tablewrap"><table class="sortable"><thead><tr><th class="n">#</th><th>Player</th>'
            f'{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def result_line(m: dict, league: str = "") -> str:
    (h, hs), (a, as_) = sorted(zip(m["teams"], m["scores"], strict=True), key=lambda p: not p[0].get("home"))
    lg = f'<span class="lg">{escape(league)}</span>' if league else ""
    return (f'<li><span class="d">{escape(m["date"])}</span><span class="h">{escape(h["name"])}</span>'
            f'<span class="sc">{hs}–{as_}</span><span>{escape(a["name"])}</span>{lg}</li>')


def _span(c: dict) -> str:
    return f"{c['first']} to {c['last']}" if c.get("first") else "dates unknown"


def avatar(name: str, team: str = "", size: str = "") -> str:
    """Round initials badge in a colour taken from the team name. A neutral stand-in, not a photo."""
    parts = [w for w in re.split(r"[\s.-]+", name) if w]
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "?"
    hue = sum(ord(c) * (i + 3) for i, c in enumerate(team or name)) % 360
    return f'<span class="av {size}" style="--h:{hue}" aria-hidden="true">{escape(initials)}</span>'


KIND_LABEL = {"hattrick": "Hat-trick", "form": "In form", "heavy": "Shot-heavy", "per90": "Per 90",
              "accuracy": "Accuracy"}


def notes_section(notes: list[dict], linked: set) -> str:
    if not notes:
        return ""
    items = []
    for n in notes:
        inner = (f'<span class="kind k-{n["kind"]}">{KIND_LABEL[n["kind"]]}</span>'
                 f'<span class="nt">{escape(n["text"])}</span>')
        if n["pid"] and (n["league"], n["pid"]) in linked:
            items.append(f'<a class="note-card" href="{player_url(n["league"], n["pid"])}">{inner}</a>')
        else:
            items.append(f'<div class="note-card">{inner}</div>')
    return (f'<h2>Shot Notes<small>written automatically from the stored matches</small></h2>'
            f'<div class="notes">{"".join(items)}</div>')


def watch_section(watch: list[dict]) -> str:
    if not watch:
        return ""
    cards = []
    for w in watch:
        svg, _ = shot_map(w["shots"], mini=True)
        inner = (f'<div class="wh">{avatar(w["name"], w["team"])}<div><b>{escape(w["name"])}</b>'
                 f'<span>{escape(w["team"])}</span></div></div><div class="pitch mini">{svg}</div>'
                 f'<div class="big"><b>{w["sot"]:,}</b><span>shots on target</span>'
                 f'<b>{w["goals"]:,}</b><span>goals</span></div>')
        cards.append(f'<a class="wcard" href="{w["url"]}">{inner}</a>')
    return (f'<h2>Players to watch<small>most shots on target across all stored competitions</small></h2>'
            f'<div class="wcards">{"".join(cards)}</div>')


def index_page(model: dict, players_n: int, stamp: str, notes_html: str = "", watch_html: str = "") -> str:
    leagues = model["leagues"]
    total = sum(v["cov"]["matches"] for v in leagues.values())
    cards = []
    for lg in sorted(leagues, key=lname):
        v = leagues[lg]
        lead = v["rows"][0] if v["rows"] else None
        line = (f'<div class="lead">Most on target: <b>{lead["shots_on_target"]}</b> '
                f'{escape(lead["player"])}</div>') if lead else ""
        cards.append(f'<a class="card" href="{lg}.html"><h3>{escape(lname(lg))}</h3>'
                     f'<div class="sub">{v["cov"]["matches"]:,} matches · {escape(_span(v["cov"]))}</div>{line}</a>')
    latest = sorted(((m, lname(lg)) for lg, v in leagues.items() for m in v["recent"][:6]),
                    key=lambda t: t[0]["date"], reverse=True)[:10]
    results = "".join(result_line(m, lgname) for m, lgname in latest)
    body = f"""
<section class="hero"><h1>Who really shoots,<br>and who <em>finishes</em>?</h1>
<p>Player leaderboards and shot maps for {len(leagues)} competitions, rebuilt automatically from match data.
Search a player or pick a competition.</p>
<input id="q" class="search" type="search" placeholder="Search a player, e.g. Haaland" autocomplete="off"
aria-label="Search a player"><div id="results"></div>
<div class="strip"><div class="kpi"><b>{total:,}</b><span>matches</span></div>
<div class="kpi"><b>{players_n:,}</b><span>players</span></div>
<div class="kpi"><b>{len(leagues)}</b><span>competitions</span></div>
<div class="kpi"><b>{escape(stamp[:10])}</b><span>last build (UTC)</span></div></div></section>
{watch_html}{notes_html}
<h2>Competitions</h2><div class="cards">{"".join(cards)}</div>
<h2>Latest results<small>newest matches in the data</small></h2><ul class="results">{results}</ul>
<p class="note">Leaderboards are per competition on purpose: leagues differ in strength and in how many matches
are stored, so one combined ranking would not be a fair comparison. See <a href="method.html">method</a>.</p>"""
    return page(f"{SITE_NAME}: shots, goals and shot maps by player", body,
                "Automatically updated player leaderboards, shot maps and forecasts for the top soccer competitions.",
                "index.html", "og-home.png")


def league_page(lg: str, v: dict, linked: set, n: int) -> str:
    rows = v["rows"]
    tabs = "".join(f'<button data-tab="t-{tid}" aria-pressed="{str(i == 0).lower()}">{label}</button>'
                   for i, (tid, label, _, _) in enumerate(TABS))
    panels = "".join(
        f'<div class="panel" id="t-{tid}"{"" if i == 0 else " hidden"}>{board(rows, key, cols, linked, lg, n)}</div>'
        for i, (tid, _, key, cols) in enumerate(TABS))
    teams = "".join(f'<a class="card" href="{team_url(lg, t["name"])}"><h3>{escape(t["name"])}</h3>'
                    f'<div class="sub">{t["p"]} played · {t["w"]}W {t["d"]}D {t["l"]}L</div></a>'
                    for t in sorted(v["teams"].values(), key=lambda t: t["name"]))
    results = "".join(result_line(m) for m in v["recent"][:10])
    note = ("" if v["cov"]["with_lineup"] else
            '<p class="note">Minutes and appearances are not stored for this competition yet, '
            'so per-90 rates are unavailable.</p>')
    body = f"""
<p class="crumbs"><a href="index.html">Competitions</a> / {escape(lname(lg))}</p>
<section class="ph"><h1>{escape(lname(lg))}</h1>
<div class="meta">{v["cov"]["matches"]:,} matches stored · {escape(_span(v["cov"]))}</div></section>
<h2>Leaderboards<small>top {n} · click a column to sort</small></h2>
<div class="tabs">{tabs}</div>{panels}{note}
<h2>Latest results</h2><ul class="results">{results}</ul>
<h2>Teams<small>record in the stored matches</small></h2><div class="cards">{teams}</div>"""
    return page(f"{lname(lg)} leaderboards | {SITE_NAME}", body,
                f"Shots on target, goals and assists leaders in the {lname(lg)}.", f"{lg}.html", f"og-{lg}.png")


def team_page(lg: str, t: dict, v: dict, linked: set) -> str:
    squad = sorted((r for r in v["rows"] if r["team"] == t["name"]),
                   key=lambda r: (-r["shots_on_target"], -r["shots"], r["player"]))[:20]
    rows = "".join(
        f"<tr><td class='who'>{name_link(lg, r, linked)}</td>"
        f"<td class='n'>{r['shots']}</td><td class='n'>{r['shots_on_target']}</td>"
        f"<td class='n'>{r['goals']}</td><td class='n'>{r['assists']}</td></tr>" for r in squad)
    games = [m for m in v["recent"] if any(x["name"] == t["name"] for x in m["teams"])][:8]
    gd = t["gf"] - t["ga"]
    body = f"""
<p class="crumbs"><a href="index.html">Competitions</a> / <a href="{lg}.html">{escape(lname(lg))}</a> /
{escape(t["name"])}</p>
<section class="ph"><h1>{escape(t["name"])}</h1>
<div class="meta">{escape(lname(lg))} · {t["p"]} matches in the stored data</div>
<span class="badge"><b>{t["w"]}</b> W</span><span class="badge"><b>{t["d"]}</b> D</span>
<span class="badge"><b>{t["l"]}</b> L</span><span class="badge">Goals <b>{t["gf"]}</b>–<b>{t["ga"]}</b>
({gd:+d})</span></section>
<h2>Players<small>by shots on target, all stored matches</small></h2>
<div class="tablewrap"><table class="sortable"><thead><tr><th>Player</th><th class="n">Shots</th>
<th class="n">On target</th><th class="n">Goals</th><th class="n">Assists</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<h2>Recent results</h2><ul class="results">{"".join(result_line(m) for m in games)}</ul>"""
    return page(f"{t['name']} | {lname(lg)} | {SITE_NAME}", body)


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
        r = (18 if s["o"] == "goal" else 13) if mini else (11 if s["o"] == "goal" else 8)
        dots.append(f'<circle class="dot s-{escape(str(s["o"]))}" cx="{cx:.0f}" cy="{cy:.0f}" r="{r}">'
                    f'<title>{escape(tip)}</title></circle>')
    box = "-20 -28 720 450" if mini else "-20 -28 720 580"
    svg = (f'<svg viewBox="{box}" role="img" aria-label="Shot map">{_PITCH}{"".join(dots)}</svg>')
    return svg, drawn


def _bars(title: str, counts: dict, labels: dict) -> str:
    items = [(labels.get(k, str(k)), c) for k, c in sorted(counts.items(), key=lambda kv: -kv[1])]
    top = max((c for _, c in items), default=1) or 1
    rows = "".join(f'<div class="bar"><span>{escape(lab)}</span><div><i style="width:{100 * c / top:.0f}%"></i>'
                   f'</div><span>{c}</span></div>' for lab, c in items)
    return f"<h3>{escape(title)}</h3>{rows}"


def player_page(lg: str, r: dict, shots: list[dict], ranks: dict, card: bool = False) -> str:
    sot_rank, goal_rank, size = ranks["sot"][r["pid"]], ranks["goals"][r["pid"]], ranks["size"]
    kpis = [("Apps", _cell(r, "apps")), ("Minutes", _cell(r, "minutes")), ("Shots", r["shots"]),
            ("On target", r["shots_on_target"]), ("Goals", r["goals"]), ("Assists", r["assists"]),
            ("On target / 90", _cell(r, "sot90"))]
    strip = "".join(f'<div class="kpi"><b>{v}</b><span>{k}</span></div>' for k, v in kpis)
    svg, drawn = shot_map(shots)
    legend = "".join(f'<span><i class="s-{k}" style="background:var(--{c})"></i>{OUTCOMES[k]}</span>'
                     for k, c in (("goal", "accent"), ("saved", "blue"), ("woodwork", "amber"),
                                  ("off_target", "red"), ("blocked", "grey")))
    if shots:
        counts = lambda key: {k: sum(1 for s in shots if (s[key] or "other") == k)  # noqa: E731
                              for k in {(s[key] or "other") for s in shots}}
        side = (f'<div class="bars">{_bars("Outcome", counts("o"), OUTCOMES)}'
                f'{_bars("Body part", counts("b"), BODY)}{_bars("Situation", counts("s"), SITUATION)}</div>')
        mapbox = (f'<div class="mapbox"><div><div class="pitch">{svg}</div><div class="legend">{legend}</div></div>'
                  f'{side}</div><p class="note">{drawn} of {len(shots)} parsed shots have a usable location and '
                  f'lie in the attacking half. The outcome, body-part and situation counts come from the parsed '
                  f'commentary, so they can differ slightly from the totals above, which are ESPN\'s own player '
                  f'statistics (ESPN counts some blocked shots as on target).</p>')
    else:
        mapbox = '<p class="note">No located shots are stored for this player yet.</p>'
    body = f"""
<p class="crumbs"><a href="index.html">Competitions</a> / <a href="{lg}.html">{escape(lname(lg))}</a> /
{escape(r["player"])}</p>
<section class="ph phead">{avatar(r["player"], r["team"], "lg")}<div><h1>{escape(r["player"])}</h1>
<div class="meta"><a href="{team_url(lg, r["team"])}">{escape(r["team"])}</a> · {escape(lname(lg))}</div>
<span class="badge">Shots on target <b>#{sot_rank}</b> of {size}</span>
<span class="badge">Goals <b>#{goal_rank}</b> of {size}</span></div>
<div class="herostat"><b>{r["shots_on_target"]:,}</b><span>shots on target</span></div></section>
<div class="strip">{strip}</div>
<h2>Shot map<small>attacking goal at the top</small></h2>{mapbox}"""
    url = player_url(lg, r["pid"])
    return page(f"{r['player']} | {lname(lg)} | {SITE_NAME}", body,
                f"{r['player']}: shots, goals, assists and shot map in the {lname(lg)}.", url,
                "og-" + url.replace(".html", ".png") if card else "og-home.png")


def about_page() -> str:
    links = " · ".join(f'<a href="{u}">{n}</a>' for n, u in OWNER_LINKS)
    body = f"""
<section class="ph"><h1>About {SITE_NAME}</h1></section>
<p class="note">{SITE_NAME} is a soccer data project built end to end by <b>{OWNER}</b>, a data engineer
completing an M.S. in Data Science at UC San Diego. It tracks who shoots and who finishes across nine
competitions, shows where shots come from, forecasts shots on target, and keeps a public scorecard of
how those forecasts turn out.</p>
<h2>What is under it</h2>
<ul class="plain">
<li>A scheduled pipeline (Python, GitHub Actions) downloads match data, parses the commentary into one row per shot,
checks every match against the source's own totals, and stores one JSON file per match in Amazon S3.</li>
<li>This site is rebuilt from those files automatically, so a finished match appears without manual work.</li>
<li>Forecasts use a Poisson model trained on past matches and are judged against a simple baseline in a
time-ordered backtest; the results are on the <a href="forecast.html">forecast page</a>, including when the
model is no better than the baseline.</li>
<li>Everything is open source and tested; see <a href="how.html">how it is built</a> and
<a href="method.html">the method and data quality</a>.</li>
</ul>
<h2>Contact</h2>
<p class="note">{links}</p>"""
    return page(f"About | {SITE_NAME}", body, f"{SITE_NAME} is a soccer data project built by {OWNER}.",
                "about.html", "og-home.png")


BEST_TABS = [("sot", "Most on target", "shots_on_target"), ("goals", "Most goals", "goals"),
             ("p90", "On target per 90", "sot90")]


LIVE_JS = """
(function(){
var box=document.getElementById("live"), meta=document.getElementById("live-meta"), url=box.dataset.src;
var names=JSON.parse(box.dataset.names), fc=JSON.parse(box.dataset.fc||"{}");
function esc(t){return String(t).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function row(m){
  var live=m.state==="in", done=m.state==="post", t=new Date(m.kickoff);
  var when=live?(m.clock||m.detail):(done?"Full time":t.toLocaleTimeString([], {hour:"numeric",minute:"2-digit"}));
  var sc=(live||done)?(m.home_score+" - "+m.away_score):"vs";
  var f=fc[m.league+":"+m.id], line="";
  if(f&&f.length){line='<span class="fcl">Forecast, shots on target: '+f.map(function(p){
    return esc(p.p)+' ('+esc(p.t)+') '+p.x.toFixed(2)}).join(' · ')+'</span>'}
  return '<li class="lv'+(live?' on':'')+'"><span class="d">'+esc(when)+'</span><span class="h">'+esc(m.home)+
    '</span><b class="sc">'+esc(sc)+'</b><span class="a">'+esc(m.away)+'</span><span class="lg"></span>'+line+'</li>';
}
function draw(d){
  var by={};d.matches.forEach(function(m){(by[m.league]=by[m.league]||[]).push(m)});
  var html="";
  Object.keys(by).sort(function(a,b){
    var la=by[a].some(function(m){return m.state==="in"}), lb=by[b].some(function(m){return m.state==="in"});
    return (lb-la)||String(names[a]||a).localeCompare(names[b]||b)}).forEach(function(k){
    html+='<h2>'+esc(names[k]||k)+'</h2><ul class="results">'+by[k].map(row).join("")+'</ul>'});
  box.innerHTML=html||'<p class="note">No matches in the window around today.</p>';
  var age=Math.round((Date.now()-new Date(d.updated))/60000);
  meta.textContent="Updated "+(age<1?"just now":age+" min ago")+(d.failed.length?" · unavailable: "+d.failed.join(", "):"");
}
function load(){fetch(url+"?t="+Date.now()).then(function(r){if(!r.ok)throw 0;return r.json()}).then(draw)
  .catch(function(){meta.textContent="Live scores are not available right now."})}
load();setInterval(load,60000);
})();
"""


def matchday_data(fc: dict | None, per_match: int = 3) -> dict:
    """Top expected shooters per match, keyed 'league:event_id', for the Match Day page."""
    out: dict[str, list] = {}
    for p in (fc or {}).get("matchday", []):
        lst = out.setdefault(f'{p["league"]}:{p["event_id"]}', [])
        if len(lst) < per_match:
            lst.append({"p": p["player"], "t": p["team"], "x": p["pred"]})
    return out


def live_page(fc: dict | None = None) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "Amir-Mirkazemi/pitchline-live")
    src = f"https://raw.githubusercontent.com/{repo}/live-data/live.json"
    body = f"""
<section class="ph"><h1>Match Day</h1>
<div class="meta" id="live-meta">Loading...</div></section>
<div id="live" data-src="{src}" data-names='{escape(json.dumps(LEAGUE_NAMES), quote=True)}'
 data-fc='{escape(json.dumps(matchday_data(fc)), quote=True)}'></div>
<p class="note">Scores are near-live: a scheduled job checks ESPN about every 15 minutes on match days, so a
score can be a few minutes behind. Times are shown in your time zone. The page re-reads the file every minute.
The forecast line is the model's expected shots on target for the likeliest shooters, made before kickoff; how
those forecasts did is graded on the <a href="forecast.html">Forecast page</a>.</p>
<script>{LIVE_JS}</script>"""
    return page(f"Match Day | {SITE_NAME}", body, "Near-live scores with a shots-on-target forecast for each match.",
                "live.html", "og-home.png")


def _f(v, nd=2):
    return "-" if v is None else f"{v:.{nd}f}"


def _model_section(card: dict, sc: dict) -> str:
    if not card:
        return ""
    res = card.get("results")
    if not res:
        return (f'<p class="note"><b>Model status: baseline only.</b> {escape(card.get("reason", ""))}. Until then the '
                f'forecasts use each player\'s own recent rate times expected minutes.</p>')
    names = {"baseline": "Baseline (own recent rate x minutes)", "league average": "Everyone gets the average",
             "poisson": "Poisson regression", "boosted": "Gradient-boosted Poisson"}
    rows = "".join(f"<tr><td>{names[k]}{' <b>(used)</b>' if k == card['best'] and card['beats_baseline'] else ''}</td>"
                   f"<td class='n'>{_f(v['mae'], 3)}</td><td class='n'>{_f(v['deviance'], 3)}</td>"
                   f"<td class='n'>{_f(v['bias'], 3)}</td></tr>" for k, v in res.items())
    cal = "".join(f"<tr><td class='n'>{c['predicted']:.2f}</td><td class='n'>{c['actual']:.2f}</td>"
                  f"<td class='n'>{c['n']:,}</td></tr>" for c in card["calibration"])
    verdict = ("The model beat the baseline on matches it never saw, so it is used."
               if card["beats_baseline"] else
               "No model beat the baseline on matches it never saw, so the baseline is used. That is a real result: "
               "shots on target are noisy and a simple rule is hard to beat.")
    return f"""<h3>Backtest</h3>
<p class="note">Trained on {card['train_rows']:,} player-matches up to {escape(card['train_until'])}, then tested on
{card['test_rows']:,} later ones ({escape(card['test_from'])} to {escape(card['test_to'])}). Lower is better.
{verdict}</p>
<div class="tablewrap"><table><thead><tr><th>Method</th><th class="n">Avg miss (MAE)</th>
<th class="n">Poisson deviance</th><th class="n">Bias</th></tr></thead><tbody>{rows}</tbody></table></div>
<h3>Does it say what it means?</h3>
<p class="note">Players sorted by predicted shots on target and split into five groups. If the model is honest, the
two columns match.</p>
<div class="tablewrap"><table><thead><tr><th class="n">Predicted (avg)</th><th class="n">Actual (avg)</th>
<th class="n">Player-matches</th></tr></thead><tbody>{cal}</tbody></table></div>"""


def _score_section(sc: dict) -> str:
    if not sc.get("n"):
        return ('<p class="note">Nothing graded yet. Each forecast is saved before the matches are played. '
                'Once those matches are stored, this page compares what was predicted with what happened.</p>')
    m, b = sc["model"], sc["baseline"]
    return f"""<p class="note">{sc['n']:,} saved forecasts for {sc['matches']:,} matches that have since been played.
Predicted {sc['predicted_total']:,} shots on target in total; the players had {sc['actual_total']:,}. Players who did
not play count as 0, as they would for anyone using the forecast.</p>
<div class="tablewrap"><table><thead><tr><th>Method</th><th class="n">Avg miss (MAE)</th><th class="n">Bias</th></tr></thead>
<tbody><tr><td>Forecast</td><td class="n">{_f(m['mae'], 3)}</td><td class="n">{_f(m['bias'], 3)}</td></tr>
<tr><td>Baseline</td><td class="n">{_f(b['mae'], 3)}</td><td class="n">{_f(b['bias'], 3)}</td></tr></tbody></table></div>"""


def forecast_page(fc: dict | None, sc: dict) -> str:
    if not fc:
        body = """<section class="ph"><h1>Forecast</h1></section>
<p class="note">The first forecast has not been generated yet. It appears here after the next scheduled run.</p>"""
        return page(f"Forecast | {SITE_NAME}", body, "Shots on target forecasts.", "forecast.html", "og-home.png")
    md = fc["matchday"][:20]
    mrows = "".join(
        f"<tr><td class='rk'>{i}</td><td class='who'>{escape(p['player'])}<small>{escape(p['team'])} "
        f"{'vs' if p['home'] else '@'} {escape(p['opponent'])} · {escape(lname(p['league']))}</small></td>"
        f"<td class='n opt'>{escape(p['kickoff'][:10])}</td><td class='n opt'>{_f(p['baseline'])}</td>"
        f"<td class='n hl'><b>{_f(p['pred'])}</b></td></tr>" for i, p in enumerate(md, 1))
    mtable = (f'<div class="tablewrap"><table><thead><tr><th class="n">#</th><th>Player</th><th class="n opt">Date</th>'
              f'<th class="n opt">Baseline</th><th class="n">Expected on target</th></tr></thead><tbody>{mrows}'
              f'</tbody></table></div>' if md else
              '<p class="note">No upcoming matches in the next 7 days for the stored competitions.</p>')
    seasons = ""
    for lg, v in fc["season"].items():
        rows = "".join(f"<tr><td class='rk'>{i}</td><td class='who'>{escape(r['player'])}<small>{escape(r['team'])}"
                       f"</small></td><td class='n'>{r['so_far']}</td><td class='n opt'>{r['left']}</td>"
                       f"<td class='n hl'><b>{r['proj']:.0f}</b></td><td class='n opt'>{r['lo']}-{r['hi']}</td></tr>"
                       for i, r in enumerate(v["players"][:10], 1))
        seasons += (f"<h3>{escape(lname(lg))}</h3><div class='tablewrap'><table><thead><tr><th class='n'>#</th>"
                    f"<th>Player</th><th class='n'>So far</th><th class='n opt'>Matches left</th>"
                    f"<th class='n'>Projected</th><th class='n opt'>Likely range</th></tr></thead>"
                    f"<tbody>{rows}</tbody></table></div>")
    body = f"""
<section class="ph"><h1>Forecast</h1>
<div class="meta">Shots on target · made {escape(fc['asof'][:16].replace('T', ' '))} UTC</div></section>
<h2>Next matchday<small>expected shots on target, next 7 days</small></h2>{mtable}
<h2>Season projection<small>shots on target by the end of the season</small></h2>
<p class="note">Shots on target so far plus the expected rate times matches left. The range covers only the luck
in the remaining matches (10th to 90th percentile), not injuries, transfers or changes in form, so it is narrower
than reality. Leagues with a fixed schedule only.</p>{seasons}
<h2>Scorecard<small>forecasts graded on what happened</small></h2>{_score_section(sc)}
<h2>How the model is tested</h2>{_model_section(fc['model'], sc)}
<p class="note">Method: for each player in each match, features known before kickoff (own shots-on-target rate over
recent matches, shrunk toward the average, expected minutes, home or away, the team's shot volume and the opponent's
shots conceded) feed a Poisson model. Newer matches are always the test set. Nothing is hidden when the model
does not win.</p>"""
    return page(f"Forecast | {SITE_NAME}", body, "Shots on target forecasts with a public backtest and scorecard.",
                "forecast.html", "og-home.png")


CALL_JS = """
(function(){
var box=document.getElementById("call"), data=JSON.parse(box.dataset.seasons), names=JSON.parse(box.dataset.names);
var KEY="shotcast-picks-v1";
function load(){try{return JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){return {}}}
function save(p){try{localStorage.setItem(KEY,JSON.stringify(p))}catch(e){}}
function esc(t){return String(t).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
var picks=load(), lg=Object.keys(data)[0];
var sel=document.getElementById("call-lg");
Object.keys(data).forEach(function(k){var o=document.createElement("option");o.value=k;o.textContent=names[k]||k;sel.appendChild(o)});
sel.onchange=function(){lg=sel.value;draw()};
function draw(){
  var d=data[lg]; if(!d){box.innerHTML='<p class="note">No projections yet.</p>';return}
  var mine=picks[lg]||[];
  var rows=d.players.map(function(p,i){
    var on=mine.indexOf(p.athlete_id)>=0;
    return '<tr><td class="rk">'+(i+1)+'</td><td class="who">'+esc(p.player)+'<small>'+esc(p.team)+'</small></td>'+
      '<td class="n">'+p.so_far+'</td><td class="n hl"><b>'+Math.round(p.proj)+'</b></td>'+
      '<td class="n"><button class="pick" data-id="'+p.athlete_id+'" aria-pressed="'+on+'">'+(on?"Picked":"Pick")+'</button></td></tr>'}).join("");
  var chosen=d.players.filter(function(p){return mine.indexOf(p.athlete_id)>=0});
  var yours=chosen.length?('<h2>Your picks</h2><ul class="plain">'+chosen.map(function(p){
    return '<li><b>'+esc(p.player)+'</b> ('+esc(p.team)+'): '+p.so_far+' on target so far, the model projects '+Math.round(p.proj)+
      ' (range '+p.lo+'-'+p.hi+')</li>'}).join("")+'</ul><p class="note">The model\'s top pick is <b>'+esc(d.players[0].player)+
      '</b> ('+Math.round(d.players[0].proj)+'). Your picks stay in this browser only.</p>')
    :'<p class="note">Pick up to three players you think will finish with the most shots on target.</p>';
  box.innerHTML=yours+'<h2>'+esc(names[lg]||lg)+'<small>season '+esc(d.season)+', model projection</small></h2>'+
    '<div class="tablewrap"><table><thead><tr><th class="n">#</th><th>Player</th><th class="n">So far</th>'+
    '<th class="n">Projected</th><th class="n"></th></tr></thead><tbody>'+rows+'</tbody></table></div>';
  box.querySelectorAll(".pick").forEach(function(b){b.onclick=function(){
    var id=b.dataset.id, cur=(picks[lg]||[]).slice(), i=cur.indexOf(id);
    if(i>=0)cur.splice(i,1);else if(cur.length<3)cur.push(id);else return;
    picks[lg]=cur;save(picks);draw()}});
}
draw();
})();
"""


def call_page(fc: dict | None) -> str:
    seasons = {lg: v for lg, v in (fc or {}).get("season", {}).items() if v["players"]}
    body = f"""
<section class="ph"><h1>Call your shot</h1>
<div class="meta">Who finishes the season with the most shots on target?</div></section>
<p class="note">Choose up to three players per competition. Your picks are saved in this browser only, nothing is
sent anywhere, and you can come back to see how the season is going against the model's projection.</p>
<select id="call-lg" aria-label="Competition"></select>
<div id="call" data-seasons='{escape(json.dumps(seasons, separators=(",", ":")), quote=True)}'
 data-names='{escape(json.dumps(LEAGUE_NAMES), quote=True)}'></div>
<script>{CALL_JS}</script>"""
    return page(f"Call your shot | {SITE_NAME}", body,
                "Pick who will finish the season with the most shots on target and compare with the model.",
                "call.html", "og-home.png")


def _box(x, y, w, t1, t2):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="64" rx="10" style="fill:var(--panel);stroke:var(--line)"/>'
            f'<text x="{x + w / 2}" y="{y + 28}" text-anchor="middle" font-weight="700" style="fill:var(--text)">{t1}</text>'
            f'<text x="{x + w / 2}" y="{y + 48}" text-anchor="middle" font-size="12" style="fill:var(--muted)">{t2}</text>')


def how_page(model: dict, stamp: str) -> str:
    cov = [v["cov"] for v in model["leagues"].values()]
    total, ok = sum(c["matches"] for c in cov), sum(c["ok"] for c in cov)
    arrow = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto"><path d="M0 0L10 5L0 10z" style="fill:var(--muted)"/></marker></defs>')
    line = lambda x1, y1, x2, y2: (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" style="stroke:var(--muted)" '  # noqa: E731
                                   f'stroke-width="1.5" marker-end="url(#ah)"/>')
    svg = (f'<svg viewBox="0 0 820 250" role="img" aria-label="Pipeline: ESPN to parser to S3 to forecast and site" '
           f'style="width:100%;height:auto;font-family:inherit">{arrow}'
           + _box(10, 20, 150, "ESPN", "match endpoints") + _box(200, 20, 170, "Backfill job", "parse + reconcile")
           + _box(410, 20, 150, "Amazon S3", "one JSON per match") + _box(600, 20, 210, "Forecast job", "backtest + save forecasts")
           + _box(410, 150, 150, "Site build", "pages + preview images") + _box(600, 150, 210, "GitHub Pages", "this website")
           + _box(10, 150, 360, "Live job (every 15 min)", "scores file, read by the Live page")
           + line(160, 52, 198, 52) + line(370, 52, 408, 52) + line(560, 52, 598, 52)
           + line(485, 84, 485, 148) + line(560, 182, 598, 182) + '</svg>')
    body = f"""
<section class="ph"><h1>How it is built</h1><div class="meta">Last build {escape(stamp)} UTC</div></section>
{svg}
<h2>The pieces</h2>
<ul class="plain">
<li><b>Collect.</b> A scheduled GitHub Actions job asks ESPN for finished matches, parses the commentary into one
row per shot, and writes one small JSON file per match to Amazon S3. It resumes where it stopped and never downloads
a finished match twice. Every night it catches up on the last few days for every competition.</li>
<li><b>Check.</b> Each match is reconciled against ESPN's own totals. Right now {ok:,} of {total:,} stored matches
({100 * ok / total:.1f}%) agree exactly; the rest stay flagged and are retried, not hidden.</li>
<li><b>Forecast.</b> After the nightly run, a job rebuilds features from past matches only, trains Poisson models,
tests them on newer matches, saves the forecasts, and later grades them. See <a href="forecast.html">Forecast</a>.</li>
<li><b>Publish.</b> The site is plain HTML generated from the stored files, so there is no server or database to
run. A finished match shows up without anyone touching anything.</li>
<li><b>Live.</b> A separate job refreshes near-live scores every 15 minutes and publishes one tiny file.</li>
</ul>
<h2>Choices worth knowing</h2>
<ul class="plain">
<li>Storage holds parsed results, not raw ESPN responses, which keeps it small and cheap.</li>
<li>Players are keyed by ESPN athlete id so same-name players never merge.</li>
<li>Forecasts are saved before matches and graded after, so the scorecard cannot be rewritten with hindsight.</li>
<li>Everything runs on free tiers plus a few cents of S3 storage.</li>
</ul>
<p class="note">Code, tests and design notes: <a href="{REPO_URL}">GitHub repository</a>.
Built by {OWNER}.</p>"""
    return page(f"How it is built | {SITE_NAME}", body, "Architecture and data-quality notes for Shotcast.",
                "how.html", "og-home.png")


def best_board(rows: list[dict], key: str, linked: set, n: int = 10) -> str:
    pool = [r for r in rows if r.get(key) is not None]
    pool.sort(key=lambda r: (-r[key], -r["shots"], r["player"]))
    pool = pool[:n]
    if not pool:
        return (f'<p class="note">Not enough minutes data yet. This board needs players with at least '
                f'{MIN_MINUTES_BEST:,} minutes across matches that have stored lineups.</p>')
    top_v = pool[0][key] or 1
    out = []
    for i, r in enumerate(pool, 1):
        lg = r["best_league"]
        name = escape(r["player"])
        if (lg, r["best_pid"]) in linked:
            name = f'<a href="{player_url(lg, r["best_pid"])}">{name}</a>'
        comps = ", ".join(lname(k) for k, _ in sorted(r["leagues"].items(), key=lambda kv: -kv[1])[:3])
        val = f"{r[key]:.2f}" if isinstance(r[key], float) else f"{r[key]:,}"
        out.append(f"<tr><td class='rk'>{i}</td><td class='who'>{name}<small>{escape(r['team'])} · "
                   f"{escape(comps)}</small></td><td class='n opt'>{r['minutes']:,}</td>"
                   f"<td class='n'>{r['shots_on_target']:,}</td><td class='n'>{r['goals']:,}</td>"
                   f"<td class='n hl'><i style='--w:{100 * r[key] / top_v:.0f}%'></i><b>{val}</b></td></tr>")
    return ('<div class="tablewrap"><table><thead><tr><th class="n">#</th><th>Player</th>'
            '<th class="n opt">Min</th><th class="n">On target</th><th class="n">Goals</th>'
            f'<th class="n">{"Per 90" if key == "sot90" else "Ranked by"}</th></tr></thead>'
            f'<tbody>{"".join(out)}</tbody></table></div>')


def best_page(rows: list[dict], linked: set, span: str) -> str:
    tabs = "".join(f'<button data-tab="b-{tid}" aria-pressed="{str(i == 0).lower()}">{label}</button>'
                   for i, (tid, label, _) in enumerate(BEST_TABS))
    panels = "".join(f'<div class="panel" id="b-{tid}"{"" if i == 0 else " hidden"}>'
                     f'{best_board(rows, key, linked)}</div>' for i, (tid, _, key) in enumerate(BEST_TABS))
    body = f"""
<section class="ph"><h1>Best of the best</h1>
<div class="meta">Top 10 across every competition stored · {escape(span)}</div></section>
<div class="tabs">{tabs}</div>{panels}
<p class="note">Each player is counted once, with matches added up across all competitions (league, Champions
League, World Cup and so on) using ESPN's player id. These numbers are not adjusted for league strength, minutes
played or team style, so a striker in a weaker league is not directly comparable to one in a stronger league.
Per-90 needs at least {MIN_MINUTES_BEST:,} known minutes. Rankings cover only the matches stored on this site.</p>"""
    return page(f"Best of the best | {SITE_NAME}", body,
                "Top 10 players by shots on target, goals and shots on target per 90 across every stored competition.",
                "best.html", "og-home.png")


def method_page(model: dict, stamp: str) -> str:
    rows = "".join(
        f"<tr><td>{escape(lname(lg))}</td><td class='n'>{c['matches']:,}</td>"
        f"<td class='n'>{c['ok']:,} ({100 * c['ok'] / c['matches']:.1f}%)</td>"
        f"<td class='n'>{c['with_lineup']:,}</td><td>{escape(_span(c))}</td></tr>"
        for lg, c in sorted(((k, v['cov']) for k, v in model['leagues'].items()), key=lambda kv: lname(kv[0])))
    body = f"""
<section class="ph"><h1>Method and data quality</h1></section>
<h2>Where the numbers come from</h2>
<p class="note">Match data comes from ESPN's public match-summary endpoints, which are unofficial and can change
without notice. Shots on target, goals and assists are ESPN's own per-player match statistics, added up across the
stored matches. Players are identified by ESPN athlete id, so two players with the same name stay separate.
Per-90 rates use only matches where the player's minutes are known, and only for players with at least
{MIN_MINUTES_PER90} minutes.</p>
<h2>How it is checked</h2>
<p class="note">For every match, the shots parsed from the commentary are compared with ESPN's team totals. A match
counts as reconciled when they agree exactly. Where ESPN's team totals are empty (some older seasons), the parsed
shots are compared with the sum of ESPN's player statistics instead. Matches that do not reconcile are flagged and
retried, never hidden.</p>
<div class="tablewrap"><table><thead><tr><th>Competition</th><th class="n">Matches</th>
<th class="n">Reconciled</th><th class="n">With minutes</th><th>Stored range</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="note">Last built {escape(stamp)} UTC.</p>
<h2>Known limits</h2>
<ul class="plain">
<li>ESPN counts some blocked shots as on target. This site uses ESPN's own on-target numbers, so some players
differ from other data providers.</li>
<li>A small number of matches have incomplete commentary at the source and stay flagged.</li>
<li>Totals cover only the stored matches and date ranges above, not a full history.</li>
<li>Minutes and appearances exist only for matches stored with a lineup; players without one show "-".</li>
<li>Shot locations come in two ESPN formats (0-100 with the goal at 100, and 0-1 with the goal at 0). The shot
maps detect the format for each match. Some shots have no location and are not drawn.</li>
<li>The "Best of the best" page adds a player's matches across competitions without adjusting for league strength,
so treat it as a count, not a verdict on quality.</li>
<li>No expected-goals ranking is published. An estimate exists, but it has not been validated against independent
data, and ESPN's coordinate formats make that check necessary first.</li>
</ul>
<h2>How it is built</h2>
<p class="note">A scheduled GitHub Actions job downloads new matches, parses them, checks them and writes one JSON
file per match to Amazon S3. Another job rebuilds this site from those files and publishes it. Code, tests and
design notes are in the <a href="{REPO_URL}">GitHub repository</a>.</p>"""
    return page(f"Method and data quality | {SITE_NAME}", body)


def build(store, out_dir: str | Path, now: datetime | None = None, top_n: int = 50) -> list[Path]:
    recs = read_records(store)
    players, coverage = totals_from_records(recs)
    if not coverage:
        raise SystemExit("No matches stored yet: nothing to build.")
    model = build_model(recs, players, coverage)
    fcs = load_forecasts(store)
    latest_fc = fcs[-1] if fcs else None
    sc = scorecard(fcs, recs)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    linked_all = {(lg, pid) for lg, v in model["leagues"].items() for pid in featured_ids(v["rows"])}
    allrows = combined_rows(players)
    watch = []
    for r in sorted(allrows, key=lambda r: (-r["shots_on_target"], r["player"])):
        key = (r["best_league"], r["best_pid"])
        if key in linked_all and model["shots"].get(key):
            watch.append({"name": r["player"], "team": r["team"], "sot": r["shots_on_target"], "goals": r["goals"],
                          "url": player_url(*key), "shots": model["shots"][key]})
        if len(watch) == 6:
            break
    notes_html = notes_section(shot_notes(recs, players, lname), linked_all)
    watch_html = watch_section(watch)
    files: dict[str, str] = {"style.css": CSS, "app.js": JS, ".nojekyll": "",
                             "index.html": index_page(model, len(players), stamp, notes_html, watch_html),
                             "method.html": method_page(model, stamp), "about.html": about_page(), "live.html": live_page(latest_fc), "how.html": how_page(model, stamp),
                             "forecast.html": forecast_page(latest_fc, sc), "call.html": call_page(latest_fc)}
    search = []
    spans = [c["first"] for c in coverage.values() if c.get("first")] + [c["last"] for c in coverage.values() if c.get("last")]
    files["best.html"] = best_page(allrows, linked_all, f"{min(spans)} to {max(spans)}" if spans else "")
    for lg, v in model["leagues"].items():
        linked = featured_ids(v["rows"])
        files[f"{lg}.html"] = league_page(lg, v, linked, top_n)
        ranks = {"sot": rank_map(v["rows"], ("shots_on_target", "shots")),
                 "goals": rank_map(v["rows"], ("goals", "shots_on_target")), "size": len(v["rows"])}
        for r in v["rows"]:
            if r["pid"] in linked:
                files[player_url(lg, r["pid"])] = player_page(lg, r, model["shots"].get((lg, r["pid"]), []), ranks, card=cards.available())
            search.append({"player": r["player"], "team": r["team"], "league": lg, "league_name": lname(lg),
                           "url": player_url(lg, r["pid"]) if r["pid"] in linked else "",
                           "rank": ranks["sot"][r["pid"]], "field": len(v["rows"]), "shots": r["shots"],
                           "shots_on_target": r["shots_on_target"], "goals": r["goals"],
                           "assists": r["assists"]})
        for t in v["teams"].values():
            files[team_url(lg, t["name"])] = team_page(lg, t, v, linked)
    files["players.json"] = json.dumps(search, separators=(",", ":"), ensure_ascii=False)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in files.items():
        path = out / name
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
