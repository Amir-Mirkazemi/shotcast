/* News page: filter stories by competition and type, and sort by date. */
(function () {
  'use strict';
  const lg = document.getElementById('f-lg'), k = document.getElementById('f-k'), o = document.getElementById('f-o'),
    n = document.getElementById('f-n'), feed = document.getElementById('feed');
  function run() {
    let shown = 0;
    document.querySelectorAll('.note-card').forEach(c => {
      const ok = (lg.value === 'all' || c.dataset.lg === lg.value) && (k.value === 'all' || c.dataset.k === k.value);
      c.hidden = !ok; if (ok) shown++;
    });
    document.querySelectorAll('.news-day-group').forEach(g => { g.hidden = !g.querySelector('.note-card:not([hidden])'); });
    feed.classList.toggle('rev', o.value === 'old');
    n.textContent = shown + (shown === 1 ? ' story' : ' stories');
  }
  [lg, k, o].forEach(e => e.addEventListener('change', run));
  run();
})();
