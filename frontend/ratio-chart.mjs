const SVG_NS = 'http://www.w3.org/2000/svg';
const MARGIN = { left: 58, right: 18, top: 18, bottom: 36 };

function range(points) {
  const values = points.map(point => point.ratio);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = Math.max((max - min) * 0.1, 0.0001);
  return [min - padding, max + padding];
}

export function layoutSeries(points, width, height) {
  const valid = points.filter(point => Number.isFinite(point.ratio));
  if (!valid.length) return [];
  const [min, max] = range(valid);
  const plotWidth = Math.max(1, width - MARGIN.left - MARGIN.right);
  const plotHeight = Math.max(1, height - MARGIN.top - MARGIN.bottom);
  return valid.map((point, index) => ({
    ...point,
    x: MARGIN.left + (valid.length === 1 ? plotWidth / 2 : index * plotWidth / (valid.length - 1)),
    y: MARGIN.top + (max - point.ratio) * plotHeight / (max - min),
  }));
}

function element(name, attributes = {}, text = '') {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text) node.textContent = text;
  return node;
}

function formatTime(value) {
  return value.replace('T', ' ').slice(0, 16);
}

export function renderRatioChart(svg, points, options = {}) {
  const width = Math.max(320, options.width || svg.parentElement.clientWidth || 640);
  const height = options.height || 284;
  const color = options.color || '#58a6ff';
  const laidOut = layoutSeries(points, width, height);
  svg.replaceChildren();
  svg.onpointermove = null;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', options.label || '一小时比值折线图');
  if (!laidOut.length) return laidOut;

  const [min, max] = range(laidOut);
  const plotBottom = height - MARGIN.bottom;
  const plotRight = width - MARGIN.right;
  for (let step = 0; step <= 4; step++) {
    const y = MARGIN.top + step * (plotBottom - MARGIN.top) / 4;
    const value = max - step * (max - min) / 4;
    svg.append(element('line', {
      x1: MARGIN.left, y1: y, x2: plotRight, y2: y,
      stroke: '#30363d', 'stroke-width': 1,
    }));
    svg.append(element('text', {
      x: MARGIN.left - 8, y: y + 4, 'text-anchor': 'end',
      fill: '#8b949e', 'font-size': 11,
    }, value.toFixed(4)));
  }

  const tickIndices = new Set([0, laidOut.length - 1]);
  const intervals = width < 500 ? 2 : 4;
  for (let tick = 1; tick < intervals; tick++) {
    tickIndices.add(Math.round(tick * (laidOut.length - 1) / intervals));
  }
  for (const index of [...tickIndices].sort((a, b) => a - b)) {
    const point = laidOut[index];
    const anchor = index === 0 ? 'start' : index === laidOut.length - 1 ? 'end' : 'middle';
    svg.append(element('text', {
      x: point.x, y: height - 10, 'text-anchor': anchor,
      fill: '#8b949e', 'font-size': 11,
    }, formatTime(point.time).slice(5)));
  }

  if (laidOut.length > 1) {
    svg.append(element('polyline', {
      points: laidOut.map(point => `${point.x},${point.y}`).join(' '),
      fill: 'none', stroke: color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));
  }

  const guide = element('line', {
    x1: 0, y1: MARGIN.top, x2: 0, y2: plotBottom,
    stroke: '#8b949e', 'stroke-dasharray': '4 4', visibility: 'hidden',
  });
  const marker = element('circle', {
    cx: 0, cy: 0, r: 5, fill: color, stroke: '#161b22', 'stroke-width': 2,
    visibility: 'hidden',
  });
  svg.append(guide, marker);
  const hover = options.hoverEl;
  function select(index) {
    const point = laidOut[index];
    guide.setAttribute('x1', point.x);
    guide.setAttribute('x2', point.x);
    guide.setAttribute('visibility', 'visible');
    marker.setAttribute('cx', point.x);
    marker.setAttribute('cy', point.y);
    marker.setAttribute('visibility', 'visible');
    if (hover) hover.textContent = `${formatTime(point.time)} 北京时间 · ${point.ratio.toFixed(4)}${point.partial ? ' · 实时，未收盘' : ' · 已收盘'}`;
  }
  select(laidOut.length - 1);
  svg.onpointermove = event => {
    const bounds = svg.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * width / bounds.width;
    const index = laidOut.length === 1 ? 0 : Math.round((x - MARGIN.left) * (laidOut.length - 1) / (plotRight - MARGIN.left));
    select(Math.min(laidOut.length - 1, Math.max(0, index)));
  };
  return laidOut;
}
