"""Static website built from the stored ESPN match files (hosted on GitHub Pages).

Pages: index (search + one card per competition), one leaderboard per competition, and a method page that
lists data quality per competition and the known limits. No framework and no server: plain HTML, one CSS
file, one JSON file that the search box reads.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .leaderboard import load_totals, top
from .storage import get_store

REPO_URL = "https://github.com/Amir-Mirkazemi/pitchline-live"

LEAGUE_NAMES = {
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "ger.1": "Bundesliga",
    "usa.1": "MLS",
    "uefa.champions": "Champions League",
    "afc.champions": "AFC Champions League",
    "fifa.world": "World Cup",
}

CSS = """
:root { --bg:#fafaf7; --fg:#1a1d1a; --muted:#5d645d; --line:#dcded6; --card:#fff; --accent:#0b6b3a; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101411; --fg:#eceee9; --muted:#9aa39a; --line:#2a312b; --card:#171c18; --accent:#4cc38a; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
a { color:var(--accent); }
header, main, footer { max-width:980px; margin:0 auto; padding:0 16px; }
header { display:flex; flex-wrap:wrap; gap:8px 20px; align-items:baseline; padding-top:20px; padding-bottom:8px; }
header .brand { font-weight:700; font-size:1.2rem; color:var(--fg); text-decoration:none; }
header nav a { margin-right:14px; }
h1 { font-size:1.9rem; line-height:1.2; margin:24px 0 8px; }
h2 { font-size:1.25rem; margin:28px 0 8px; }
.lead { color:var(--muted); max-width:640px; }
.stats { display:flex; flex-wrap:wrap; gap:12px; margin:16px 0; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; }
.stat b { display:block; font-size:1.3rem; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px;
  text-decoration:none; color:var(--fg); }
.card:hover { border-color:var(--accent); }
.card small { display:block; color:var(--muted); }
input[type=search] { width:100%; max-width:420px; padding:10px 12px; font-size:1rem; color:var(--fg);
  background:var(--card); border:1px solid var(--line); border-radius:8px; }
.tablewrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; background:var(--card); border:1px solid var(--line); }
th, td { padding:7px 10px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }
th { font-size:.8rem; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); cursor:pointer; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
tbody tr:nth-child(even) { background:rgba(127,127,127,.06); }
.note { color:var(--muted); font-size:.9rem; }
footer { color:var(--muted); font-size:.85rem; padding-top:28px; padding-bottom:40px; }
"""

JS = """
const fold = t => t.toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');
const num = v => (v === '-' ? -1 : parseFloat(v.replace('#', '')));
document.querySelectorAll('table.sortable th').forEach((th, col) => th.addEventListener('click', () => {
  const body = th.closest('table').tBodies[0];
  const dir = th.dataset.dir === 'desc' ? 1 : -1;
  th.dataset.dir = dir === 1 ? 'asc' : 'desc';
  const rows = [...body.rows];
  const key = r => r.cells[col].innerText;
  rows.sort((a, b) => th.classList.contains('n') ? dir * (num(key(a)) - num(key(b)))
    : dir * key(a).localeCompare(key(b)));
  rows.forEach(r => body.appendChild(r));
}));
const box = document.getElementById('q');
if (box) {
  let data = null;
  const out = document.getElementById('results');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  box.addEventListener('input', async () => {
    const q = fold(box.value.trim());
    if (!q) { out.innerHTML = ''; return; }
    if (!data) data = await (await fetch('players.json')).json();
    const hits = data.filter(p => fold(p.player).includes(q)).slice(0, 25);
    out.innerHTML = hits.length ? '<div class="tablewrap"><table><thead><tr><th>Player</th><th>Competition</th>'
      + '<th>Team</th><th class="n">Rank (on target)</th><th class="n">Apps</th><th class="n">Min</th>'
      + '<th class="n">Shots</th><th class="n">On target</th><th class="n">Goals</th><th class="n">Assists</th>'
      + '</tr></thead><tbody>' + hits.map(p => '<tr><td>' + esc(p.player) + '</td><td>'
      + '<a href="' + esc(p.league) + '.html">' + esc(p.league_name) + '</a></td><td>' + esc(p.team)
      + '</td><td class="n">#' + p.rank + ' of ' + p.field + '</td><td class="n">' + p.apps + '</td><td class="n">'
      + p.minutes + '</td><td class="n">' + p.shots + '</td><td class="n">' + p.shots_on_target
      + '</td><td class="n">' + p.goals + '</td><td class="n">' + p.assists + '</td></tr>').join('')
      + '</tbody></table></div>' : '<p class="note">No player found.</p>';
  });
}
"""


def league_name(slug: str) -> str:
    return LEAGUE_NAMES.get(slug, slug)


def _cell(row: dict, key: str):
    """Apps and minutes are '-' until a lineup has been stored for the player."""
    return row[key] if row["lineup_matches"] else "-"


def _page(title: str, body: str, description: str = "") -> str:
    desc = f'<meta name="description" content="{escape(description)}">' if description else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>{desc}
<link rel="stylesheet" href="style.css"></head>
<body>
<header><a class="brand" href="index.html">Pitchline Live</a>
<nav><a href="index.html">Competitions</a><a href="method.html">Method and data quality</a>
<a href="{REPO_URL}">GitHub</a></nav></header>
<main>{body}</main>
<footer>Data: ESPN match data (unofficial endpoints). Not affiliated with ESPN or any league.
Built from <a href="{REPO_URL}">the open-source pipeline</a>.</footer>
<script src="app.js"></script>
</body></html>
"""


def _cov_text(c: dict) -> str:
    span = f"{c['first']} to {c['last']}" if c.get("first") else "dates unknown"
    return f"{c['matches']:,} matches stored · {span}"


def _index(players: dict, coverage: dict, stamp: str) -> str:
    cards = "".join(
        f'<a class="card" href="{escape(lg)}.html"><b>{escape(league_name(lg))}</b>'
        f"<small>{escape(_cov_text(coverage[lg]))}</small></a>"
        for lg in sorted(coverage, key=league_name))
    total = sum(c["matches"] for c in coverage.values())
    body = f"""
<h1>Who shoots most, and who finishes?</h1>
<p class="lead">Running totals of shots on target, goals and assists for every player in
{len(coverage)} competitions, rebuilt automatically from match data. Search a player or open a competition.</p>
<div class="stats">
<div class="stat"><b>{total:,}</b>matches</div>
<div class="stat"><b>{len(players):,}</b>players</div>
<div class="stat"><b>{len(coverage)}</b>competitions</div>
<div class="stat"><b>{escape(stamp)}</b>last built (UTC)</div></div>
<input id="q" type="search" placeholder="Search a player, e.g. Haaland" autocomplete="off"
aria-label="Search a player">
<div id="results"></div>
<h2>Competitions</h2>
<div class="cards">{cards}</div>
<p class="note">Leaderboards are per competition on purpose: leagues differ in strength and in the number
of matches stored, so a combined ranking would not be a fair comparison.
See <a href="method.html">method and data quality</a>.</p>"""
    return _page("Pitchline Live: shots on target and goals by player", body,
                 "Automatically updated player leaderboards for shots on target, goals and assists.")


def _league_page(lg: str, players: dict, coverage: dict, n: int) -> str:
    rows = "".join(
        f"<tr><td class='n'>{i}</td><td>{escape(r['player'])}</td><td>{escape(r['team'])}</td>"
        f"<td class='n'>{_cell(r, 'apps')}</td><td class='n'>{_cell(r, 'minutes')}</td>"
        f"<td class='n'>{r['shots']}</td><td class='n'>{r['shots_on_target']}</td>"
        f"<td class='n'>{r['goals']}</td><td class='n'>{r['assists']}</td></tr>"
        for i, r in enumerate(top(players, lg, n), 1))
    name = escape(league_name(lg))
    body = f"""
<h1>{name}</h1>
<p class="lead">Top {n} players by shots on target. {escape(_cov_text(coverage[lg]))}.
Click a column to sort.</p>
<div class="tablewrap"><table class="sortable"><thead><tr><th class="n">#</th><th>Player</th><th>Team</th>
<th class="n">Apps</th><th class="n">Min</th><th class="n">Shots</th><th class="n">On target</th>
<th class="n">Goals</th><th class="n">Assists</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="note">Apps and minutes show "-" for players whose matches do not have a stored lineup yet.
Totals cover only the stored matches listed above. See <a href="method.html">method</a>.</p>"""
    return _page(f"{league_name(lg)}: shots on target leaderboard | Pitchline Live", body,
                 f"Shots on target, goals and assists leaders in the {league_name(lg)}.")


def _method(coverage: dict, stamp: str) -> str:
    rows = "".join(
        f"<tr><td>{escape(league_name(lg))}</td><td class='n'>{c['matches']:,}</td>"
        f"<td class='n'>{c['ok']:,} ({100 * c['ok'] / c['matches']:.1f}%)</td>"
        f"<td class='n'>{c['with_lineup']:,}</td><td>{escape(c.get('first') or '-')} to "
        f"{escape(c.get('last') or '-')}</td></tr>"
        for lg, c in sorted(coverage.items(), key=lambda kv: league_name(kv[0])))
    body = f"""
<h1>Method and data quality</h1>
<h2>Where the numbers come from</h2>
<p>Match data comes from ESPN's public match-summary endpoints, which are unofficial and can change without
notice. Shots on target, goals and assists are ESPN's own per-player match statistics, added up across the
stored matches. Players are identified by ESPN athlete id, so two players with the same name stay separate.</p>
<h2>How it is checked</h2>
<p>For every match, the shots parsed from the commentary are compared with ESPN's team totals. A match counts as
reconciled when they agree exactly. Where ESPN's team totals are empty (older seasons), the parsed shots are
compared with the sum of ESPN's player statistics instead. Matches that do not reconcile are flagged and
retried, not hidden.</p>
<div class="tablewrap"><table><thead><tr><th>Competition</th><th class="n">Matches</th>
<th class="n">Reconciled</th><th class="n">With minutes</th><th>Stored range</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="note">Last built {escape(stamp)} UTC.</p>
<h2>Known limits</h2>
<ul>
<li>ESPN counts some blocked shots as on target. This site uses ESPN's own on-target numbers, so a few players
may differ from other providers.</li>
<li>A small number of matches have incomplete commentary at the source and stay flagged.</li>
<li>Totals cover only the stored matches and date ranges shown above, not a full history.</li>
<li>Minutes and appearances exist only for matches stored with a lineup; players without one show "-".</li>
<li>There is no combined ranking across competitions: leagues differ in strength and schedule, so it would
not be a fair comparison.</li>
<li>An expected-goals "danger" ranking is not published yet. It needs to be validated against
independent data first.</li>
</ul>
<h2>How it is built</h2>
<p>A scheduled GitHub Actions job downloads new matches, parses them, checks them and writes one JSON file per
match to Amazon S3. Another job rebuilds this site from those files and publishes it. The code, tests and
design notes are in the <a href="{REPO_URL}">GitHub repository</a>.</p>"""
    return _page("Method and data quality | Pitchline Live", body)


def _players_json(players: dict) -> list[dict]:
    out = []
    for lg in {r["league"] for r in players.values()}:
        ranked = top(players, lg, n=10**9)
        for r in ranked:
            # ties share the better rank, as in the leaderboard lookup
            rank = 1 + sum(1 for o in ranked if (o["shots_on_target"], o["shots"])
                           > (r["shots_on_target"], r["shots"]))
            out.append({"player": r["player"], "team": r["team"], "league": lg, "league_name": league_name(lg),
                        "rank": rank, "field": len(ranked), "apps": _cell(r, "apps"),
                        "minutes": _cell(r, "minutes"), "shots": r["shots"],
                        "shots_on_target": r["shots_on_target"], "goals": r["goals"], "assists": r["assists"]})
    return out


def build(store, out_dir: str | Path, top_n: int = 50, now: datetime | None = None) -> list[Path]:
    players, coverage = load_totals(store)
    if not coverage:
        raise SystemExit("No matches stored yet: nothing to build.")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "style.css": CSS, "app.js": JS,
        "index.html": _index(players, coverage, stamp),
        "method.html": _method(coverage, stamp),
        "players.json": json.dumps(_players_json(players), separators=(",", ":"), ensure_ascii=False),
        ".nojekyll": "",
    }
    for lg in coverage:
        files[f"{lg}.html"] = _league_page(lg, players, coverage, top_n)
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
    paths = build(get_store(a.root), a.out, a.top)
    print(f"Wrote {len(paths)} files to {a.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
