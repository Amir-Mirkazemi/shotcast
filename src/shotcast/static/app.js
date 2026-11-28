/* Shotcast site script: theme, menu, search, sortable tables, tabs, shot-map animation. No dependencies. */
(function () {
  'use strict';
  const root = document.documentElement;
  const fold = t => t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = v => (v === '-' ? -1 : parseFloat(v.replace(/[#,]/g, '')));

  /* theme */
  const tb = document.getElementById('theme');
  if (tb) {
    const paint = () => {
      const dark = root.dataset.theme === 'dark';
      tb.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
      tb.querySelector('use').setAttribute('href', dark ? '#i-sun' : '#i-moon');
    };
    paint();
    tb.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('theme', root.dataset.theme); } catch (e) { /* storage blocked */ }
      paint();
      document.dispatchEvent(new CustomEvent('themechange'));
    });
  }

  /* mobile menu */
  const mb = document.getElementById('menu-btn'), nav = document.getElementById('nav');
  if (mb && nav) {
    mb.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      mb.setAttribute('aria-expanded', open);
      mb.querySelector('use').setAttribute('href', open ? '#i-x' : '#i-menu');
    });
  }

  /* sortable tables and tabs */
  document.querySelectorAll('table.sortable th').forEach((th, col) => th.addEventListener('click', () => {
    const body = th.closest('table').tBodies[0], dir = th.dataset.dir === 'desc' ? 1 : -1;
    th.dataset.dir = dir === 1 ? 'asc' : 'desc';
    const rows = [...body.rows], key = r => r.cells[col].innerText;
    rows.sort((a, b) => th.classList.contains('n') ? dir * (num(key(a)) - num(key(b))) : dir * key(a).localeCompare(key(b)));
    rows.forEach(r => body.appendChild(r));
  }));
  document.querySelectorAll('.tabs button[data-tab]').forEach(btn => btn.addEventListener('click', () => {
    btn.parentElement.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b === btn));
    document.querySelectorAll('.panel').forEach(p => { p.hidden = p.id !== btn.dataset.tab; });
  }));

  /* player search (header and home page share one index) */
  let index = null;
  const load = () => index ? Promise.resolve(index)
    : fetch('players.json').then(r => r.json()).then(d => (index = d));
  document.querySelectorAll('[data-search]').forEach(input => {
    const drop = document.getElementById(input.dataset.search);
    const show = () => {
      const q = fold(input.value.trim());
      if (!q) { drop.classList.remove('open'); drop.innerHTML = ''; return; }
      load().then(data => {
        const hits = data.filter(p => fold(p.player).includes(q))
          .sort((a, b) => fold(a.player).startsWith(q) === fold(b.player).startsWith(q) ? b.shots_on_target - a.shots_on_target
            : fold(a.player).startsWith(q) ? -1 : 1).slice(0, 8);
        drop.innerHTML = hits.length ? hits.map(p => '<a href="' + esc(p.url || p.league + '.html') + '"><span>' + esc(p.player)
          + '</span><small>' + esc(p.team) + ' · ' + esc(p.league_name) + '</small><b>' + p.shots_on_target + '</b></a>').join('')
          : '<p>No player found. Try a surname.</p>';
        drop.classList.add('open');
      }).catch(() => { drop.innerHTML = '<p>Search is not available right now.</p>'; drop.classList.add('open'); });
    };
    input.addEventListener('input', show);
    input.addEventListener('focus', show);
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { drop.classList.remove('open'); input.blur(); }
      if (e.key === 'Enter') { const a = drop.querySelector('a'); if (a) location.href = a.getAttribute('href'); }
      if (e.key === 'ArrowDown') { const a = drop.querySelector('a'); if (a) { e.preventDefault(); a.focus(); } }
    });
    drop.addEventListener('keydown', e => {
      const a = document.activeElement;
      if (e.key === 'ArrowDown' && a.nextElementSibling) { e.preventDefault(); a.nextElementSibling.focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); (a.previousElementSibling || input).focus(); }
    });
    document.addEventListener('click', e => { if (!input.parentElement.contains(e.target)) drop.classList.remove('open'); });
  });
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      const i = document.querySelector('[data-search]'); if (i) { e.preventDefault(); i.focus(); }
    }
  });
})();

(function () {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const NS = 'http://www.w3.org/2000/svg';
  function play(svg, total) {
    if (reduce) return;
    svg.classList.remove('anim'); svg.querySelectorAll('.trail').forEach(t => t.remove());
    const dots = [...svg.querySelectorAll('.dot')], first = dots[0];
    if (!first) return;
    const step = Math.min(0.06, total / dots.length);
    dots.forEach((d, i) => {
      const cx = +d.getAttribute('cx'), cy = +d.getAttribute('cy'), on = !/s-(off|blocked)/.test(d.getAttribute('class'));
      const delay = (i * step).toFixed(2);
      d.style.setProperty('--d', (+delay + 0.35) + 's');
      if (on) {
        const gx = 340, gy = -8, len = Math.hypot(gx - cx, gy - cy);
        const ln = document.createElementNS(NS, 'path');
        ln.setAttribute('d', 'M' + cx + ' ' + cy + ' L' + gx + ' ' + gy);
        ln.setAttribute('class', 'trail' + (/s-goal/.test(d.getAttribute('class')) ? ' g' : ''));
        ln.style.setProperty('--len', len); ln.style.setProperty('--d', delay + 's');
        svg.insertBefore(ln, first);
      }
    });
    void svg.getBoundingClientRect(); svg.classList.add('anim');
  }
  function count(el) {
    const to = +el.dataset.count; if (reduce || !to) return;
    const t0 = performance.now(), dur = 1400;
    (function tick(t) {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(to * e).toLocaleString('en-US');
      if (k < 1) requestAnimationFrame(tick);
    })(t0);
  }
  const stage = document.getElementById('stage-map');
  if (stage) {
    const svg = stage.querySelector('svg'); play(svg, 3.4);
    const rp = document.getElementById('replay'); if (rp) rp.addEventListener('click', () => play(svg, 3.4));
  }
  document.querySelectorAll('.stage [data-count]').forEach(count);
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) { play(e.target.querySelector('svg'), 1.6); io.unobserve(e.target); }
    }), { threshold: 0.35 });
    document.querySelectorAll('.wcard .pitch').forEach(p => io.observe(p));
  }
})();
