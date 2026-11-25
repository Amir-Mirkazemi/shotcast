"""Static website built from the stored ESPN match files (hosted on GitHub Pages).

Pages: home (search, latest results, one card per competition), a leaderboard page per competition (on target,
goals, assists, on target per 90, shots), a page per team, a page per leading player with a shot map, and a
method page with data quality and known limits. Plain HTML, one CSS file, one small JS file, no framework.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .leaderboard import read_records, totals_from_records
from .sitedata import MIN_MINUTES_PER90, build_model, featured_ids, rank_map, slug
from .storage import get_store

SITE_NAME = "Shotcast"
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
.results .lg { color:var(--muted); font-size:.75rem; text-align:right; min-width:92px; }
.tabs { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 12px; }
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
  .top .wrap { gap:12px; } .top nav { gap:12px; font-size:.85rem; } }
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


def page(title: str, body: str, desc: str = "") -> str:
    d = f'<meta name="description" content="{escape(desc)}">' if desc else ""
    return f"""<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<title>{escape(title)}</title>{d}
<link rel="icon" href="{FAVICON}">
<link rel="stylesheet" href="style.css"></head>
<body>
<div class="top"><div class="wrap"><a class="brand" href="index.html">{LOGO}{SITE_NAME}</a>
<nav><a href="index.html">Competitions</a><a href="method.html">Method</a><a href="{REPO_URL}">GitHub</a><button id="theme" type="button">Dark</button></nav>
</div></div>
<div class="wrap">{body}
<footer>Match data from ESPN (unofficial endpoints). Not affiliated with ESPN or any league.
Open-source pipeline on <a href="{REPO_URL}">GitHub</a>.</footer></div>
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


def index_page(model: dict, players_n: int, stamp: str) -> str:
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
<h2>Competitions</h2><div class="cards">{"".join(cards)}</div>
<h2>Latest results<small>newest matches in the data</small></h2><ul class="results">{results}</ul>
<p class="note">Leaderboards are per competition on purpose: leagues differ in strength and in how many matches
are stored, so one combined ranking would not be a fair comparison. See <a href="method.html">method</a>.</p>"""
    return page(f"{SITE_NAME}: shots, goals and shot maps by player", body,
                "Automatically updated player leaderboards and shot maps for the top soccer competitions.")


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
                f"Shots on target, goals and assists leaders in the {lname(lg)}.")


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


def shot_map(shots: list[dict]) -> tuple[str, int]:
    """Half-pitch SVG, attacking goal at the top. Returns (svg, number of shots drawn)."""
    dots, drawn = [], 0
    for s in sorted(shots, key=lambda s: s["o"] == "goal"):            # goals last, so they sit on top
        if s["x"] is None:
            continue
        cx, cy = s["y"] / 100 * 680, (100 - s["x"]) / 100 * 1050
        if cy > 540:                                                 # beyond the halfway line
            continue
        drawn += 1
        tip = " · ".join(t for t in (f"{s['m']}'" if s["m"] is not None else "", s["d"],
                                     BODY.get(s["b"], ""), SITUATION.get(s["s"], ""),
                                     OUTCOMES.get(s["o"], "")) if t)
        r = 11 if s["o"] == "goal" else 8
        dots.append(f'<circle class="dot s-{escape(str(s["o"]))}" cx="{cx:.0f}" cy="{cy:.0f}" r="{r}">'
                    f'<title>{escape(tip)}</title></circle>')
    svg = (f'<svg viewBox="-20 -28 720 580" role="img" aria-label="Shot map">{_PITCH}{"".join(dots)}</svg>')
    return svg, drawn


def _bars(title: str, counts: dict, labels: dict) -> str:
    items = [(labels.get(k, str(k)), c) for k, c in sorted(counts.items(), key=lambda kv: -kv[1])]
    top = max((c for _, c in items), default=1) or 1
    rows = "".join(f'<div class="bar"><span>{escape(lab)}</span><div><i style="width:{100 * c / top:.0f}%"></i>'
                   f'</div><span>{c}</span></div>' for lab, c in items)
    return f"<h3>{escape(title)}</h3>{rows}"


def player_page(lg: str, r: dict, shots: list[dict], ranks: dict) -> str:
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
<section class="ph"><h1>{escape(r["player"])}</h1>
<div class="meta"><a href="{team_url(lg, r["team"])}">{escape(r["team"])}</a> · {escape(lname(lg))}</div>
<span class="badge">Shots on target <b>#{sot_rank}</b> of {size}</span>
<span class="badge">Goals <b>#{goal_rank}</b> of {size}</span></section>
<div class="strip">{strip}</div>
<h2>Shot map<small>attacking goal at the top</small></h2>{mapbox}"""
    return page(f"{r['player']} | {lname(lg)} | {SITE_NAME}", body,
                f"{r['player']}: shots, goals, assists and shot map in the {lname(lg)}.")


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
<li>There is no combined ranking across competitions: leagues differ in strength and schedule, so it would not be
a fair comparison.</li>
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
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    files: dict[str, str] = {"style.css": CSS, "app.js": JS, ".nojekyll": "",
                             "index.html": index_page(model, len(players), stamp),
                             "method.html": method_page(model, stamp)}
    search = []
    for lg, v in model["leagues"].items():
        linked = featured_ids(v["rows"])
        files[f"{lg}.html"] = league_page(lg, v, linked, top_n)
        ranks = {"sot": rank_map(v["rows"], ("shots_on_target", "shots")),
                 "goals": rank_map(v["rows"], ("goals", "shots_on_target")), "size": len(v["rows"])}
        for r in v["rows"]:
            if r["pid"] in linked:
                files[player_url(lg, r["pid"])] = player_page(lg, r, model["shots"].get((lg, r["pid"]), []), ranks)
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
    return paths


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
