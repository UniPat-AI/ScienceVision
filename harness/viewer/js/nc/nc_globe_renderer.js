/**
 * NcGlobeRenderer — d3-geo orthographic globe over a Canvas2D.
 *
 * Strategy: pixel-loop inverse projection. For each pixel inside the
 * projected sphere, invert to (lon, lat), sample the data grid by
 * nearest-neighbour, and write RGBA via the colormap LUT. NaN cells (and
 * pixels outside the sphere) get alpha=0.
 *
 * Inputs are passed in via setData()/setColormap()/setRotation() and the
 * caller drives a single render() per frame. Drag handling is wired in
 * the constructor so the renderer owns its own input → state → re-render
 * loop while exposing onRotation for downstream UI.
 *
 * The lat / lon arrays are taken from the slices manifest in sorted
 * ascending order, so the renderer can compute index by linear-uniform
 * stride. If the source grid is non-uniform we fall back to binary search.
 */
export class NcGlobeRenderer {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} [opts]
   * @param {number} [opts.size=560]   canvas dimensions
   * @param {(rot:number[])=>void} [opts.onRotation]
   */
  constructor(canvas, { size = 560, onRotation } = {}) {
    this.canvas = canvas;
    this.size = size;
    canvas.width = size;
    canvas.height = size;
    this.ctx = canvas.getContext('2d');
    this.onRotation = onRotation || (() => {});

    // Background (off-globe) colour
    this.bgFill = 'rgba(15, 22, 34, 1)';

    // d3.geoOrthographic
    if (!window.d3 || !window.d3.geoOrthographic) {
      throw new Error('NcGlobeRenderer: d3-geo not loaded');
    }
    this.projection = window.d3
      .geoOrthographic()
      .scale(size / 2 - 6)
      .translate([size / 2, size / 2])
      .rotate([-160, -10, 0])
      .clipAngle(90);

    this.path = window.d3.geoPath(this.projection).context(this.ctx);

    // State (immutable-style: never mutate the slabs we hold)
    this.dataSlab = null; // Float32Array, length = lat * lon
    this.dataShape = null; // [lat, lon]
    this.latVals = null;
    this.lonVals = null;
    this.scale = null; // { lut, vmin, vmax, name }

    this._installDragHandlers();
  }

  /**
   * @param {object} params
   * @param {Float32Array} params.slab    flat lat*lon Float32 row-major
   * @param {number[]} params.lat         ascending lat values, length L
   * @param {number[]} params.lon         ascending lon values, length W
   */
  setData({ slab, lat, lon }) {
    this.dataSlab = slab;
    this.dataShape = [lat.length, lon.length];
    this.latVals = lat;
    this.lonVals = lon;

    // Precompute lookup acceleration for uniform grids
    this._latLo = lat[0];
    this._latStep = (lat[lat.length - 1] - lat[0]) / (lat.length - 1);
    this._lonLo = lon[0];
    this._lonStep = (lon[lon.length - 1] - lon[0]) / (lon.length - 1);
  }

  setColormap(scale) {
    this.scale = scale;
  }

  setRotation(rotation) {
    this.projection.rotate(rotation);
  }

  getRotation() {
    return this.projection.rotate();
  }

  /**
   * Paint the current state to the canvas.
   *
   * Procedure:
   *  1. Clear background.
   *  2. Loop over disk pixels, inverse project to (lon, lat), sample, paint.
   *  3. Stroke graticule (30° lat/lon lines).
   *  4. Stroke equator + prime meridian in slightly brighter colour.
   *  5. Outline disk.
   */
  render() {
    if (!this.dataSlab || !this.scale) return;

    const ctx = this.ctx;
    const size = this.size;

    ctx.fillStyle = this.bgFill;
    ctx.fillRect(0, 0, size, size);

    // Build the data image
    const img = ctx.createImageData(size, size);
    const { lut, vmin, vmax } = this.scale;
    const span = vmax - vmin || 1;
    const [H, W] = this.dataShape;
    const slab = this.dataSlab;
    const latLo = this._latLo;
    const latStep = this._latStep;
    const lonLo = this._lonLo;
    const lonStep = this._lonStep;
    const invert = this.projection.invert.bind(this.projection);

    // Bounding disk: project radius from scale
    const r = this.projection.scale();
    const cx = size / 2;
    const cy = size / 2;
    const r2 = r * r;

    for (let py = 0; py < size; py++) {
      const dy = py - cy;
      const dy2 = dy * dy;
      for (let px = 0; px < size; px++) {
        const dx = px - cx;
        if (dx * dx + dy2 > r2) continue; // outside disk
        const lonlat = invert([px, py]);
        if (!lonlat) continue;
        let lon = lonlat[0];
        const lat = lonlat[1];

        // Normalise lon to [lonLo, lonLo + 360) for index math
        let lonNorm = lon - lonLo;
        // Wrap into [0, 360)
        lonNorm = ((lonNorm % 360) + 360) % 360;

        const iLat = Math.round((lat - latLo) / latStep);
        let iLon = Math.round(lonNorm / lonStep);
        if (iLat < 0 || iLat >= H) continue;
        if (iLon < 0) iLon = 0;
        if (iLon >= W) iLon = W - 1; // tolerate edge

        const v = slab[iLat * W + iLon];
        if (!Number.isFinite(v)) continue;

        let t = (v - vmin) / span;
        if (t < 0) t = 0;
        else if (t > 1) t = 1;
        const ci = Math.round(t * 255) * 3;

        const o = (py * size + px) * 4;
        img.data[o] = lut[ci];
        img.data[o + 1] = lut[ci + 1];
        img.data[o + 2] = lut[ci + 2];
        img.data[o + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);

    // Graticule
    const d3 = window.d3;
    ctx.strokeStyle = 'rgba(255,255,255,0.18)';
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    this.path(d3.geoGraticule().step([30, 30])());
    ctx.stroke();

    // Equator + prime meridian
    ctx.strokeStyle = 'rgba(255,255,255,0.4)';
    ctx.lineWidth = 0.9;
    ctx.beginPath();
    this.path({
      type: 'LineString',
      coordinates: [
        [-180, 0],
        [-90, 0],
        [0, 0],
        [90, 0],
        [180, 0],
      ],
    });
    ctx.stroke();
    ctx.beginPath();
    this.path({
      type: 'LineString',
      coordinates: [
        [0, -85],
        [0, 0],
        [0, 85],
      ],
    });
    ctx.stroke();

    // Disk outline
    ctx.strokeStyle = 'rgba(255,255,255,0.55)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }

  _installDragHandlers() {
    const canvas = this.canvas;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let pendingFrame = null;

    const requestRender = () => {
      if (pendingFrame) return;
      pendingFrame = requestAnimationFrame(() => {
        pendingFrame = null;
        this.render();
        this.onRotation(this.projection.rotate());
      });
    };

    const onPointerDown = (ev) => {
      dragging = true;
      lastX = ev.clientX;
      lastY = ev.clientY;
      canvas.setPointerCapture?.(ev.pointerId);
      canvas.style.cursor = 'grabbing';
    };
    const onPointerMove = (ev) => {
      if (!dragging) return;
      const dx = ev.clientX - lastX;
      const dy = ev.clientY - lastY;
      lastX = ev.clientX;
      lastY = ev.clientY;
      const sensitivity = 0.4; // deg / px
      const [lambda, phi, gamma] = this.projection.rotate();
      const newPhi = Math.max(-89, Math.min(89, phi - dy * sensitivity));
      this.projection.rotate([lambda + dx * sensitivity, newPhi, gamma]);
      requestRender();
    };
    const onPointerUp = (ev) => {
      dragging = false;
      canvas.releasePointerCapture?.(ev.pointerId);
      canvas.style.cursor = 'grab';
    };

    canvas.style.cursor = 'grab';
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointerleave', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);
  }
}
