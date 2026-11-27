/* Interactive charts (Apache ECharts). Each .chart element carries its data in data-items='[["label", n], ...]'. */
(function () {
  'use strict';
  if (!window.echarts) return;
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const charts = [];
  function option(el) {
    const items = JSON.parse(el.dataset.items), muted = css('--muted'), line = css('--line');
    const color = el.dataset.color ? css(el.dataset.color) : css('--accent-hi');
    items.reverse();
    return {
      animationDuration: 600, grid: { left: 4, right: 38, top: 4, bottom: 4, containLabel: true },
      tooltip: { trigger: 'item', backgroundColor: css('--panel2'), borderColor: line, textStyle: { color: css('--text') },
        formatter: p => p.name + ': <b>' + p.value + '</b>' },
      xAxis: { type: 'value', show: false, max: v => v.max * 1.12 },
      yAxis: { type: 'category', data: items.map(i => i[0]), axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: muted, fontFamily: 'Inter, sans-serif' } },
      series: [{ type: 'bar', data: items.map(i => i[1]), barWidth: 14, itemStyle: { color, borderRadius: 7 },
        label: { show: true, position: 'right', color: muted, fontFamily: 'Inter, sans-serif' },
        emphasis: { itemStyle: { color: css('--accent-hi') } }, backgroundStyle: { color: css('--panel2'), borderRadius: 7 },
        showBackground: true }],
    };
  }
  document.querySelectorAll('.chart[data-items]').forEach(el => {
    el.style.height = (JSON.parse(el.dataset.items).length * 32 + 12) + 'px';
    const c = echarts.init(el, null, { renderer: 'svg' });
    c.setOption(option(el)); charts.push([c, el]);
  });
  document.addEventListener('themechange', () => charts.forEach(([c, el]) => c.setOption(option(el), true)));
  window.addEventListener('resize', () => charts.forEach(([c]) => c.resize()));
})();
