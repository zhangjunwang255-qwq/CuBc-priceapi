import test from 'node:test';
import assert from 'node:assert/strict';
import { layoutSeries } from './ratio-chart.mjs';

const points = [
  { time: '2026-08-14T14:00:00+08:00', ratio: 1.10, partial: false },
  { time: '2026-08-17T09:00:00+08:00', ratio: 1.12, partial: false },
  { time: '2026-08-17T10:00:00+08:00', ratio: 1.11, partial: true },
];

test('trading bars have equal x spacing across a weekend', () => {
  const layout = layoutSeries(points, 600, 260);
  assert.equal(layout.length, 3);
  assert.ok(Math.abs((layout[1].x - layout[0].x) - (layout[2].x - layout[1].x)) < 0.001);
  assert.deepEqual(layout.map(point => point.time), points.map(point => point.time));
  assert.equal(layout[2].partial, true);
});

test('each chart scales y independently from its own points', () => {
  const low = layoutSeries(points, 600, 260);
  const high = layoutSeries(points.map(point => ({ ...point, ratio: point.ratio + 1 })), 600, 260);
  for (let index = 0; index < low.length; index++) {
    assert.ok(Math.abs(low[index].y - high[index].y) < 0.001);
  }
});

test('one point stays centered and empty input stays empty', () => {
  const single = layoutSeries([points[0]], 600, 260);
  assert.equal(single.length, 1);
  assert.equal(single[0].x, (58 + 600 - 18) / 2);
  assert.ok(Number.isFinite(single[0].y));
  assert.deepEqual(layoutSeries([], 600, 260), []);
});
