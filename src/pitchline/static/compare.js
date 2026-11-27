/* Player comparison: pick two players, see totals and shot maps side by side. State lives in the URL (?a=ID&b=ID). */
(function () {
  'use strict';
  const out = document.getElementById('cmp');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const fold = t => t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const OUT = { goal: 'Goal', saved: 'Saved', woodwork: 'Hit woodwork', off_target: 'Off target', blocked: 'Blocked', own_goal: 'Own goal' };
  const PITCH = '<rect class="turf" x="-20" y="-28" width="720" height="580" rx="10"/><rect class="pl" x="0" y="0" width="680" height="525"/>'
    + '<rect class="pl" x="138.5" y="0" width="403" height="165"/><rect class="pl" x="248.4" y="0" width="183.2" height="55"/>'
    + '<rect class="pl goal" x="303.4" y="-18" width="73.2" height="18"/><path class="pl" d="M266.9 165 A91.5 91.5 0 0 0 413.1 165"/>'
    + '<path class="pl" d="M248.5 525 A91.5 91.5 0 0 1 431.5 525"/><circle cx="340" cy="110" r="3" fill="var(--pline)"/>';
  const SHIRT = 'M21 8 L28 5 Q32 11 36 5 L43 8 L58 19 L51 30 L45 26 V58 H19 V26 L13 30 L6 19 Z';
  let index = [], picks = { a: null, b: null }, uid = 0;

  function shirt(p) {
    const [base, alt, pat] = p.kit, id = 'c' + (uid++), parts = p.name.split(/[\s.-]+/).filter(Boolean);
    const ini = ((parts[0] || '?')[0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
    let deco = '';
    if (pat === 'hoops') for (let y = 14; y < 60; y += 12) deco += '<rect x="0" y="' + y + '" width="64" height="6" fill="' + alt + '"/>';
    else if (pat === 'vstripes') for (let x = 13; x < 52; x += 14) deco += '<rect x="' + x + '" y="0" width="7" height="64" fill="' + alt + '"/>';
    else if (pat === 'sash') deco = '<polygon points="6,8 20,8 64,52 64,64 50,64" fill="' + alt + '"/>';
    else if (pat === 'band') deco = '<rect x="0" y="27" width="64" height="9" fill="' + alt + '"/>';
    else if (pat === 'sleeves') deco = '<rect x="0" y="0" width="19" height="64" fill="' + alt + '"/><rect x="45" y="0" width="19" height="64" fill="' + alt + '"/>';
    const light = /^#/.test(base) && (parseInt(base.slice(1, 3), 16) * 0.299 + parseInt(base.slice(3, 5), 16) * 0.587 + parseInt(base.slice(5, 7), 16) * 0.114) > 150
      && (pat === 'solid' || pat === 'sleeves');
    return '<span class="av lg"><svg viewBox="0 0 64 64" aria-hidden="true"><defs><clipPath id="' + id + '"><path d="' + SHIRT + '"/></clipPath></defs>'
      + '<path d="' + SHIRT + '" fill="' + base + '"/><g clip-path="url(#' + id + ')">' + deco + '</g><path d="' + SHIRT + '" fill="none" stroke="rgba(0,0,0,.35)"/>'
      + '<path d="M26 6 Q32 15 38 6" fill="none" stroke="' + alt + '" stroke-width="2.6" stroke-linecap="round"/>'
      + '<text x="32" y="44" text-anchor="middle" font-size="15" font-weight="800" fill="' + (light ? '#111827' : '#fff') + '" stroke="' + (light ? '#fff' : '#0b1020')
      + '" stroke-width="2.2" paint-order="stroke" stroke-linejoin="round" font-family="Inter,Arial,sans-serif">' + esc(ini) + '</text></svg></span>';
  }
  function map(shots) {
    let dots = '', n = 0;
    shots.slice().sort((a, b) => (a.o === 'goal') - (b.o === 'goal')).forEach(s => {
      if (s.x === null) return;
      const cx = s.y / 100 * 680, cy = (100 - s.x) / 100 * 1050;
      if (cy > 540) return;
      n++;
      const tip = [s.m !== null ? s.m + "'" : '', s.d, OUT[s.o] || ''].filter(Boolean).join(' · ');
      dots += '<circle class="dot s-' + esc(s.o) + '" cx="' + cx.toFixed(0) + '" cy="' + cy.toFixed(0) + '" r="' + (s.o === 'goal' ? 11 : 8) + '"><title>' + esc(tip) + '</title></circle>';
    });
    return '<div class="pitch"><svg viewBox="-20 -28 720 580" role="img" aria-label="Shot map">' + PITCH + dots + '</svg></div><p class="note">' + n + ' located shots</p>';
  }
  const ROWS = [['Apps', 'apps'], ['Minutes', 'min'], ['Shots', 'shots'], ['On target', 'sot'], ['On target %', 'acc'],
    ['Goals', 'goals'], ['Assists', 'assists'], ['On target / 90', 'sot90']];
  const val = (p, k) => k === 'acc' ? (p.shots ? Math.round(1000 * p.sot / p.shots) / 10 : null) : p[k];
  const show = (p, k) => { const v = val(p, k); return v === null || v === undefined ? '-' : k === 'acc' ? v.toFixed(1) + '%' : k === 'sot90' ? v.toFixed(2) : Number(v).toLocaleString('en-US'); };

  function render() {
    const { a, b } = picks;
    if (!a && !b) { out.innerHTML = '<p class="note">Pick two players to compare.</p>'; return; }
    const head = p => p ? '<div class="cmp-head" style="--kc:' + esc(p.color) + '">' + shirt(p) + '<div><h2><a href="' + esc(p.url) + '">' + esc(p.name) + '</a></h2><span>' + esc(p.team) + ' · ' + esc(p.comp) + '</span></div></div>'
      : '<div class="cmp-head empty"><span>Pick a second player</span></div>';
    let html = '<div class="cmp-grid">' + head(a) + head(b) + '</div>';
    if (a && b) {
      html += '<div class="tablewrap"><table class="cmp-table"><tbody>' + ROWS.map(([lab, k]) => {
        const x = val(a, k), y = val(b, k), ax = x !== null && y !== null && x > y, by = x !== null && y !== null && y > x;
        return '<tr><td class="n' + (ax ? ' lead' : '') + '">' + show(a, k) + '</td><th scope="row">' + lab + '</th><td class="n' + (by ? ' lead' : '') + '">' + show(b, k) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
    }
    html += '<div class="cmp-grid" id="cmp-maps"><div id="map-a"></div><div id="map-b"></div></div>';
    out.innerHTML = html;
    ['a', 'b'].forEach(s => {
      const p = picks[s]; if (!p) return;
      fetch('shots/' + p.id + '.json').then(r => r.json()).then(sh => { document.getElementById('map-' + s).innerHTML = map(sh); })
        .catch(() => { document.getElementById('map-' + s).innerHTML = '<p class="note">Shot map not available.</p>'; });
    });
  }
  function setUrl() {
    const q = new URLSearchParams();
    if (picks.a) q.set('a', picks.a.id);
    if (picks.b) q.set('b', picks.b.id);
    try { history.replaceState(null, '', q.toString() ? '?' + q : location.pathname); } catch (e) { /* ignore */ }
  }
  function pick(slot, p) {
    picks[slot] = p; document.getElementById('cmp-' + slot).value = p ? p.name : '';
    document.getElementById('cmp-drop-' + slot).classList.remove('open'); setUrl(); render();
  }
  document.querySelectorAll('[data-cmp]').forEach(input => {
    const slot = input.dataset.cmp, drop = document.getElementById('cmp-drop-' + slot);
    input.addEventListener('input', () => {
      const q = fold(input.value.trim());
      if (!q) { drop.classList.remove('open'); return; }
      const hits = index.filter(p => fold(p.name).includes(q)).sort((x, y) => y.sot - x.sot).slice(0, 8);
      drop.innerHTML = hits.length ? hits.map(p => '<a href="#" data-id="' + esc(p.id) + '"><span>' + esc(p.name) + '</span><small>' + esc(p.team) + ' · ' + esc(p.comp) + '</small><b>' + p.sot + '</b></a>').join('')
        : '<p>No player with a profile matches.</p>';
      drop.classList.add('open');
    });
    drop.addEventListener('click', e => {
      const a = e.target.closest('a[data-id]'); if (!a) return;
      e.preventDefault(); pick(slot, index.find(p => p.id === a.dataset.id));
    });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { const a = drop.querySelector('a[data-id]'); if (a) a.click(); } });
    document.addEventListener('click', e => { if (!input.parentElement.contains(e.target)) drop.classList.remove('open'); });
  });
  fetch('compare.json').then(r => r.json()).then(d => {
    index = d;
    const q = new URLSearchParams(location.search);
    ['a', 'b'].forEach(s => { const p = index.find(x => x.id === q.get(s)); if (p) { picks[s] = p; document.getElementById('cmp-' + s).value = p.name; } });
    if (picks.a || picks.b) render();
  }).catch(() => { out.innerHTML = '<p class="note">Player list not available right now.</p>'; });
})();
