import { aggregateHeatmap } from './pure.js';

// Render a normalized grid to an RGBA heatmap PNG (blue→red ramp on transparency).
function renderPng(grid, cells, width, height) {
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext('2d');
  const cw = width / grid;
  const ch = height / grid;
  for (let gy = 0; gy < grid; gy++) {
    for (let gx = 0; gx < grid; gx++) {
      const v = cells[gy * grid + gx];
      if (v <= 0) continue;
      const r = Math.round(255 * Math.min(1, v * 1.5));
      const b = Math.round(255 * (1 - v));
      ctx.fillStyle = `rgba(${r},40,${b},${0.15 + 0.6 * v})`;
      ctx.fillRect(gx * cw, gy * ch, cw + 1, ch + 1);
    }
  }
  return canvas.convertToBlob({ type: 'image/png' });
}

self.onmessage = async (e) => {
  try {
    const { moves, width, height } = e.data;
    const { grid, cells } = aggregateHeatmap(moves, { width, height });
    const pngBlob = await renderPng(grid, cells, width, height);
    self.postMessage({ grid, cells, pngBlob });
  } catch (err) {
    self.postMessage({ error: String(err) });
  }
};
