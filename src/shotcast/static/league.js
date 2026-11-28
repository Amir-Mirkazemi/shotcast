/* Competition page: filter the leaderboards by season and team using data/<league>.json. */
(function () {
  'use strict';
  const lb = document.getElementById('lb');
  if (!lb) return;
  const selS = document.getElementById('fl-season'), selT = document.getElementById('fl-team'),
    out = document.getElementById('filtered'), note = document.getElementById('fl-note'),
    reset = document.getElementById('fl-reset'), bar = document.getElementById('filters');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const KEYS = { sot: 'so', goals: 'g', assists: 'as', p90: 'p90', shots: 'sh' };
  const LABEL = { sot: 'On target', goals: 'Goals', assists: 'Assists', p90: 'On target per 90', shots: 'Shots' };
  let data = null, metric = 'sot';

  const active = () => selS.value !== 'all' || selT.value !== 'all';
  function fill(sel, values, keep) {
    const first = sel.options[0];
    sel.innerHTML = ''; sel.appendChild(first);
    values.forEach(v => { const o = document.createElement('option'); o.value = o.textContent = v; sel.appendChild(o); });
    sel.value = values.includes(keep) ? keep : 'all';
  }
  function teamsIn(season) {
    return [...new Set(data.rows.filter(r => season === 'all' || r.s === season).map(r => r.t))].sort();
  }
  function aggregate() {
    const by = new Map();
    data.rows.forEach(r => {
      if ((selS.value !== 'all' && r.s !== selS.value) || (selT.value !== 'all' && r.t !== selT.value)) return;
      let p = by.get(r.id);
      if (!p) { p = { id: r.id, n: r.n, u: r.u, a: 0, m: 0, sh: 0, so: 0, g: 0, as: 0, teams: {} }; by.set(r.id, p); }
      p.a += r.a; p.m += r.m; p.sh += r.sh; p.so += r.so; p.g += r.g; p.as += r.as;
      p.teams[r.t] = (p.teams[r.t] || 0) + r.m + r.so;
    });
    return [...by.values()].map(p => {
      p.t = Object.keys(p.teams).sort((x, y) => p.teams[y] - p.teams[x])[0];
      p.p90 = p.m >= 600 ? p.so * 90 / p.m : null;
      return p;
    });
  }
  function render() {
    const key = KEYS[metric];
    const rows = aggregate().filter(p => p[key] !== null).sort((x, y) => (y[key] - x[key]) || (y.so - x.so) || x.n.localeCompare(y.n)).slice(0, 50);
    const top = Math.max(1, ...rows.map(p => p[key]));
    const cell = (k, v) => k === key
      ? '<td class="n hl"><i style="--w:' + Math.round(100 * v / top) + '%"></i><b>' + fmt(k, v) + '</b></td>'
      : '<td class="n">' + fmt(k, v) + '</td>';
    const fmt = (k, v) => v === null ? '-' : (k === 'p90' ? v.toFixed(2) : v.toLocaleString('en-US'));
    out.innerHTML = rows.length
      ? '<div class="tablewrap"><table><thead><tr><th class="n">#</th><th>Player</th><th class="n opt">Apps</th><th class="n opt">Min</th>'
        + '<th class="n">Shots</th><th class="n">On target</th><th class="n">Goals</th><th class="n">Assists</th><th class="n opt">Per 90</th></tr></thead><tbody>'
        + rows.map((p, i) => '<tr><td class="rk">' + (i + 1) + '</td><td class="who">' + esc(p.n) + '<small>' + esc(p.t) + '</small></td>'
          + '<td class="n opt">' + p.a + '</td><td class="n opt">' + (p.a ? p.m.toLocaleString('en-US') : '-') + '</td>'
          + cell('sh', p.sh) + cell('so', p.so) + cell('g', p.g) + cell('as', p.as) + cell('p90', p.p90).replace('class="n', 'class="n opt') + '</tr>').join('')
        + '</tbody></table></div>'
      : '<p class="note">No players match this selection in the stored data.</p>';
    note.textContent = LABEL[metric] + ' · ' + (selS.value === 'all' ? 'all seasons' : selS.value) + (selT.value === 'all' ? '' : ' · ' + selT.value);
  }
  function update() {
    const on = active();
    lb.classList.toggle('filtered', on); out.hidden = !on; reset.hidden = !on;
    if (on) render(); else note.textContent = '';
  }
  lb.querySelectorAll('.tabs button[data-tab]').forEach(b => b.addEventListener('click', () => {
    metric = b.dataset.tab.replace('t-', ''); if (active()) render();
  }));
  selS.addEventListener('change', () => { fill(selT, teamsIn(selS.value), selT.value); update(); });
  selT.addEventListener('change', update);
  reset.addEventListener('click', () => { selS.value = 'all'; fill(selT, teamsIn('all'), 'all'); update(); });
  fetch(lb.dataset.src).then(r => r.json()).then(d => {
    data = d; fill(selS, d.seasons, 'all'); fill(selT, teamsIn('all'), 'all'); bar.hidden = false;
  }).catch(() => { /* filters stay hidden; the full-history boards still work */ });
})();
