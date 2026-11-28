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
  function formOption(el) {
    const d = JSON.parse(el.dataset.form), muted = css('--muted'), line = css('--line'), n = d.dates.length;
    const font = { color: muted, fontFamily: 'Inter, sans-serif' };
    return {
      animationDuration: 600, color: [css('--accent-hi'), css('--amber')],
      legend: { top: 0, textStyle: font, inactiveColor: line },
      grid: { left: 8, right: 16, top: 38, bottom: n > 30 ? 52 : 28, containLabel: true },
      tooltip: { trigger: 'axis', backgroundColor: css('--panel2'), borderColor: line, textStyle: { color: css('--text') },
        formatter: ps => { const i = ps[0].dataIndex; return d.dates[i] + '<br>' + d.labels[i] + '<br>' + ps.map(p => p.marker + p.seriesName + ': <b>' + (p.value === null ? '-' : p.value) + '</b>').join('<br>'); } },
      xAxis: { type: 'category', data: d.dates, axisLabel: font, axisLine: { lineStyle: { color: line } }, axisTick: { show: false } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: font, splitLine: { lineStyle: { color: line } } },
      dataZoom: n > 30 ? [{ type: 'inside', startValue: Math.max(0, n - 40) }, { type: 'slider', height: 18, startValue: Math.max(0, n - 40), borderColor: line, textStyle: font }] : [],
      series: [
        { name: 'Per match', type: 'bar', data: d.sot, barMaxWidth: 12, itemStyle: { color: css('--panel2'), borderColor: line, borderRadius: [4, 4, 0, 0] } },
        { name: '5-match average', type: 'line', data: d.r5, smooth: true, showSymbol: false, lineStyle: { width: 3 } },
        { name: '10-match average', type: 'line', data: d.r10, smooth: true, showSymbol: false, lineStyle: { width: 3 } },
      ],
    };
  }
  document.querySelectorAll('.chart[data-form]').forEach(el => {
    const c = echarts.init(el, null, { renderer: 'svg' });
    c.setOption(formOption(el)); charts.push([c, el]);
  });
  document.addEventListener('themechange', () => charts.forEach(([c, el]) => c.setOption(el.dataset.form ? formOption(el) : option(el), true)));
  window.addEventListener('resize', () => charts.forEach(([c]) => c.resize()));
})();
