(function exposeNiiVueMeasurements(root, factory) {
  'use strict';

  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && !root.ScienceVisionNiiVueMeasurements) {
    Object.defineProperty(root, 'ScienceVisionNiiVueMeasurements', {
      configurable: false,
      enumerable: true,
      writable: false,
      value: api,
    });
  }
}(typeof window === 'object' ? window : null, function createNiiVueMeasurementsAPI() {
  'use strict';

  const SLICE = Object.freeze({ axial: 0, coronal: 1, sagittal: 2, multiplanar: 3, render: 4 });
  const PLANE_NAME = Object.freeze({ 0: 'Axial', 1: 'Coronal', 2: 'Sagittal' });
  const COLORS = Object.freeze({
    length: Object.freeze([0.20, 0.85, 1.00, 1.00]),
    angle: Object.freeze([1.00, 0.76, 0.24, 1.00]),
    pending: Object.freeze([1.00, 1.00, 1.00, 0.92]),
  });
  const EPSILON = 1e-8;

  function clampCosine(value) {
    return Math.max(-1, Math.min(1, Number(value)));
  }

  function finitePoint(point) {
    return Array.isArray(point) && point.length >= 3
      && point.slice(0, 3).every(value => Number.isFinite(Number(value)));
  }

  function distanceMM(a, b) {
    if (!finitePoint(a) || !finitePoint(b)) return NaN;
    return Math.hypot(
      Number(a[0]) - Number(b[0]),
      Number(a[1]) - Number(b[1]),
      Number(a[2]) - Number(b[2]),
    );
  }

  function angleDegrees(a, vertex, c) {
    if (!finitePoint(a) || !finitePoint(vertex) || !finitePoint(c)) return null;
    const u = [a[0] - vertex[0], a[1] - vertex[1], a[2] - vertex[2]];
    const v = [c[0] - vertex[0], c[1] - vertex[1], c[2] - vertex[2]];
    const uLength = Math.hypot(...u);
    const vLength = Math.hypot(...v);
    if (uLength <= EPSILON || vLength <= EPSILON) return null;
    const cosine = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (uLength * vLength);
    return Math.acos(clampCosine(cosine)) * 180 / Math.PI;
  }

  function undoMeasurementState(state) {
    if (!state || !Array.isArray(state.pending) || !Array.isArray(state.measurements)) return false;
    state.preview = null;
    if (state.pending.length) {
      state.pending.pop();
      return true;
    }
    if (state.measurements.length) {
      state.measurements.pop();
      return true;
    }
    return false;
  }

  function clearMeasurementState(state) {
    if (!state || !Array.isArray(state.pending) || !Array.isArray(state.measurements)) return false;
    const changed = state.pending.length > 0 || state.measurements.length > 0 || Boolean(state.preview);
    state.pending.length = 0;
    state.measurements.length = 0;
    state.preview = null;
    return changed;
  }

  function spatialUnitInfo(nv) {
    const code = Number(nv?.volumes?.[0]?.hdr?.xyzt_units) & 0x07;
    if (code === 1) return Object.freeze({ code, source: 'm', scaleToMM: 1000, assumed: false, label: 'mm' });
    if (code === 2) return Object.freeze({ code, source: 'mm', scaleToMM: 1, assumed: false, label: 'mm' });
    if (code === 3) return Object.freeze({ code, source: 'um', scaleToMM: 0.001, assumed: false, label: 'mm' });
    return Object.freeze({ code, source: 'unknown', scaleToMM: 1, assumed: true, label: 'mm (assumed)' });
  }

  function fractionToWorldRASMM(nv, frac, unit = spatialUnitInfo(nv)) {
    const raw = nv.frac2mm(frac);
    return [0, 1, 2].map(index => Number(raw[index]) * unit.scaleToMM);
  }

  function formatLength(value) {
    if (!Number.isFinite(value)) return 'n/a';
    if (value >= 100) return value.toFixed(0);
    if (value >= 10) return value.toFixed(1);
    return value.toFixed(2);
  }

  function formatCoordinate(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 'n/a';
    if (Math.abs(numeric) < 0.005) return '0.00';
    return `${numeric > 0 ? '+' : ''}${numeric.toFixed(2)}`;
  }

  function install(nv) {
    if (!nv || !nv.canvas || !Array.isArray(nv.volumes) || !nv.volumes.length) {
      throw new Error('niivue-measurements: expected an attached NiiVue instance with a loaded volume');
    }
    if (nv.__svMeasurementController) return nv.__svMeasurementController;
    if (typeof document !== 'object') throw new Error('niivue-measurements requires a browser document');

    const state = {
      mode: 'navigate',
      pending: [],
      measurements: [],
      preview: null,
      pointerDown: null,
      nextLength: 1,
      nextAngle: 1,
      notice: '',
      noticeToken: 0,
    };
    const unit = spatialUnitInfo(nv);
    const canvas = nv.canvas;
    let destroyed = false;
    let renderFrame = null;
    let resultSignature = '';
    let measurementProvider = null;

    const panel = document.createElement('section');
    panel.id = 'sv-niivue-measure-panel';
    panel.setAttribute('aria-label', 'NIfTI measurement and coordinate tools');
    panel.innerHTML = [
      '<div class="sv-nm-heading"><strong>Measure</strong><span id="sv-nm-unit"></span></div>',
      '<div class="sv-nm-modes" role="group" aria-label="Measurement mode">',
      '  <button type="button" data-measure-mode="navigate" class="active">Navigate</button>',
      '  <button type="button" data-measure-mode="length">Length</button>',
      '  <button type="button" data-measure-mode="angle">Angle</button>',
      '</div>',
      '<div id="sv-nm-step" class="sv-nm-step" role="status"></div>',
      '<div class="sv-nm-coordinates">',
      '  <div id="sv-nm-coordinate-title" class="sv-nm-coordinate-title"></div>',
      '  <div id="sv-nm-coordinate-values" class="sv-nm-coordinate-values"></div>',
      '  <div id="sv-nm-voxel" class="sv-nm-voxel"></div>',
      '  <div class="sv-nm-axis-key">X: L(-) / R(+) &nbsp; Y: P(-) / A(+) &nbsp; Z: I(-) / S(+)</div>',
      '</div>',
      '<div class="sv-nm-actions">',
      '  <button type="button" data-measure-action="undo" title="Undo pending point or last measurement">Undo</button>',
      '  <button type="button" data-measure-action="clear" title="Clear all measurements">Clear</button>',
      '</div>',
      '<ol id="sv-nm-results" class="sv-nm-results" aria-label="Measurements"></ol>',
    ].join('');
    document.body.appendChild(panel);

    const modeButtons = Array.from(panel.querySelectorAll('button[data-measure-mode]'));
    const stepElement = panel.querySelector('#sv-nm-step');
    const unitElement = panel.querySelector('#sv-nm-unit');
    const coordinateTitle = panel.querySelector('#sv-nm-coordinate-title');
    const coordinateValues = panel.querySelector('#sv-nm-coordinate-values');
    const voxelElement = panel.querySelector('#sv-nm-voxel');
    const resultList = panel.querySelector('#sv-nm-results');
    const undoButton = panel.querySelector('[data-measure-action="undo"]');
    const clearButton = panel.querySelector('[data-measure-action="clear"]');
    unitElement.textContent = unit.assumed ? 'mm (assumed)' : 'mm';
    unitElement.classList.toggle('assumed', unit.assumed);

    function quantitativeView() {
      return nv.opts.sliceType === SLICE.axial || nv.opts.sliceType === SLICE.multiplanar;
    }

    function measurementMode() {
      return state.mode === 'length' || state.mode === 'angle';
    }

    function dimensionsRAS() {
      const raw = nv.volumes[0]?.dimsRAS || nv.volumes[0]?.hdr?.dims;
      return raw ? [Number(raw[1]), Number(raw[2]), Number(raw[3])] : [1, 1, 1];
    }

    function planeComponent(plane) {
      return 2 - Number(plane);
    }

    function sliceTolerance(plane) {
      const component = planeComponent(plane);
      const dimension = dimensionsRAS()[component];
      return Number.isFinite(dimension) && dimension > 0 ? 0.51 / dimension : 1e-5;
    }

    function sameSlice(a, b) {
      return Boolean(a && b)
        && a.plane === b.plane
        && Math.abs(a.sliceFrac - b.sliceFrac) <= sliceTolerance(a.plane);
    }

    function canvasPoint(event) {
      const rect = canvas.getBoundingClientRect();
      if (!(rect.width > 0) || !(rect.height > 0)) return null;
      return [
        (event.clientX - rect.left) * canvas.width / rect.width,
        (event.clientY - rect.top) * canvas.height / rect.height,
      ];
    }

    function worldRASMM(frac) {
      return fractionToWorldRASMM(nv, frac, unit);
    }

    function makePick(xy, forcedTile = -1) {
      if (!xy || !Array.isArray(nv.screenSlices)) return null;
      const tileIndex = forcedTile >= 0 ? forcedTile : nv.tileIndex(xy[0], xy[1]);
      const tile = nv.screenSlices[tileIndex];
      if (!tile || tile.axCorSag < SLICE.axial || tile.axCorSag > SLICE.sagittal) return null;
      const frac = Array.from(nv.screenXY2TextureFrac(xy[0], xy[1], tileIndex, true), Number).slice(0, 3);
      if (!finitePoint(frac) || frac.some(value => value < 0 || value > 1)) return null;
      const plane = Number(tile.axCorSag);
      const component = planeComponent(plane);
      return {
        frac,
        rasMM: worldRASMM(frac),
        voxel: Array.from(nv.frac2vox(frac), value => Math.round(Number(value))).slice(0, 3),
        plane,
        tileIndex,
        sliceFrac: Number(frac[component]),
      };
    }

    function copyPick(pick) {
      return {
        frac: pick.frac.slice(),
        rasMM: pick.rasMM.slice(),
        voxel: pick.voxel.slice(),
        plane: pick.plane,
        tileIndex: pick.tileIndex,
        sliceFrac: pick.sliceFrac,
      };
    }

    function setNotice(message) {
      state.notice = String(message || '');
      const token = ++state.noticeToken;
      updateControls();
      if (!state.notice) return;
      setTimeout(() => {
        if (destroyed || token !== state.noticeToken) return;
        state.notice = '';
        updateControls();
      }, 2400);
    }

    function setCrosshair(pick) {
      nv.scene.crosshairPos = pick.frac.slice();
      if (typeof nv.createOnLocationChange === 'function') nv.createOnLocationChange(pick.plane);
    }

    function commitLength(first, second) {
      if (!sameSlice(first, second)) {
        setNotice('Length endpoints must be on the same 2D tile and slice.');
        return false;
      }
      const value = distanceMM(first.rasMM, second.rasMM);
      if (!Number.isFinite(value) || value <= EPSILON) {
        setNotice('Length requires two distinct points.');
        return false;
      }
      state.measurements.push({
        id: `L${state.nextLength++}`,
        kind: 'length',
        plane: first.plane,
        sliceFrac: first.sliceFrac,
        points: [copyPick(first), copyPick(second)],
        value,
      });
      state.pending.length = 0;
      state.preview = null;
      setCrosshair(second);
      return true;
    }

    function commitAngle(points) {
      if (points.length !== 3 || !sameSlice(points[0], points[1]) || !sameSlice(points[0], points[2])) {
        setNotice('Angle points A-B-C must be on the same 2D tile and slice.');
        return false;
      }
      const value = angleDegrees(points[0].rasMM, points[1].rasMM, points[2].rasMM);
      if (!Number.isFinite(value)) {
        setNotice('Angle requires non-zero A-B and C-B vectors.');
        return false;
      }
      state.measurements.push({
        id: `A${state.nextAngle++}`,
        kind: 'angle',
        plane: points[0].plane,
        sliceFrac: points[0].sliceFrac,
        points: points.map(copyPick),
        value,
      });
      state.pending.length = 0;
      state.preview = null;
      setCrosshair(points[2]);
      return true;
    }

    function addClickPoint(pick) {
      if (state.pending.length && !sameSlice(state.pending[0], pick)) {
        setNotice(`${state.mode === 'angle' ? 'Angle' : 'Length'} points must stay on the source slice.`);
        return;
      }
      state.pending.push(copyPick(pick));
      setCrosshair(pick);
      if (state.mode === 'length' && state.pending.length === 2) commitLength(state.pending[0], state.pending[1]);
      if (state.mode === 'angle' && state.pending.length === 3) commitAngle(state.pending.slice(0, 3));
    }

    function currentStep() {
      if (!quantitativeView()) return '精确测量请切换 MPR/Axial';
      if (state.notice) return state.notice;
      if (state.mode === 'navigate') return 'Navigate';
      if (state.mode === 'length') {
        return state.pending.length ? 'Length: A set, pick B' : 'Length: pick A then B, or drag A-B';
      }
      const labels = ['A', 'B (vertex)', 'C'];
      return `Angle: pick ${labels[state.pending.length] || 'A'}`;
    }

    function updateControls() {
      const available = quantitativeView();
      if (!available && state.mode !== 'navigate') {
        state.mode = 'navigate';
        state.pending.length = 0;
        state.preview = null;
        state.pointerDown = null;
      }
      for (const button of modeButtons) {
        const mode = button.dataset.measureMode;
        const active = mode === state.mode;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
        button.disabled = mode !== 'navigate' && !available;
      }
      undoButton.disabled = !state.pending.length && !state.measurements.length;
      clearButton.disabled = !state.pending.length && !state.measurements.length && !state.preview;
      panel.classList.toggle('unavailable', !available);
      canvas.classList.toggle('sv-niivue-measuring', available && measurementMode());
      stepElement.textContent = currentStep();
    }

    function setMode(mode) {
      if (!['navigate', 'length', 'angle'].includes(mode)) return;
      if (mode !== 'navigate' && !quantitativeView()) return;
      if (mode !== state.mode) {
        state.pending.length = 0;
        state.preview = null;
        state.pointerDown = null;
      }
      state.mode = mode;
      state.notice = '';
      state.noticeToken += 1;
      updateControls();
      nv.drawScene();
    }

    function currentTileFor(plane, sliceFrac) {
      const component = planeComponent(plane);
      const tolerance = sliceTolerance(plane);
      for (let index = 0; index < (nv.screenSlices || []).length; index += 1) {
        const tile = nv.screenSlices[index];
        if (!tile || Number(tile.axCorSag) !== plane) continue;
        const tileFrac = Number.isFinite(Number(tile.sliceFrac))
          ? Number(tile.sliceFrac)
          : Number(nv.scene.crosshairPos[component]);
        if (Math.abs(tileFrac - sliceFrac) <= tolerance) return tile;
      }
      return null;
    }

    function projectPoint(frac, tile, plane) {
      if (!tile || !Array.isArray(tile.leftTopWidthHeight) || !Array.isArray(tile.leftTopMM)
          || !Array.isArray(tile.fovMM) || !(Math.abs(tile.fovMM[0]) > 0) || !(Math.abs(tile.fovMM[1]) > 0)) {
        return null;
      }
      const mm = nv.frac2mm(frac);
      let horizontal = Number(mm[0]);
      let vertical = Number(mm[1]);
      if (plane === SLICE.coronal) vertical = Number(mm[2]);
      if (plane === SLICE.sagittal) {
        horizontal = Number(mm[1]);
        vertical = Number(mm[2]);
      }
      const rect = tile.leftTopWidthHeight;
      const x = rect[0] + (horizontal - tile.leftTopMM[0]) / tile.fovMM[0] * rect[2];
      const y = rect[1] + rect[3] - (vertical - tile.leftTopMM[1]) / tile.fovMM[1] * rect[3];
      return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
    }

    function drawPoint(point, color, radius) {
      nv.drawCircle([point[0] - radius, point[1] - radius, radius * 2, radius * 2], color.slice(), 1);
    }

    function drawStoredMeasurement(measurement) {
      const tile = currentTileFor(measurement.plane, measurement.sliceFrac);
      if (!tile) return false;
      const projected = measurement.points.map(point => projectPoint(point.frac, tile, measurement.plane));
      if (projected.some(point => !point)) return false;
      const dpr = Number(nv.uiData?.dpr) || 1;
      const thickness = Math.max(2, 1.5 * dpr);
      const radius = Math.max(3, 3 * dpr);
      const color = COLORS[measurement.kind];
      if (measurement.kind === 'length') {
        nv.drawLine([...projected[0], ...projected[1]], thickness, color.slice());
        nv.drawTextBetween(
          [...projected[0], ...projected[1]],
          `${formatLength(measurement.value)} mm${unit.assumed ? '*' : ''}`,
          0.62,
          color.slice(),
        );
      } else {
        nv.drawLine([...projected[0], ...projected[1]], thickness, color.slice());
        nv.drawLine([...projected[1], ...projected[2]], thickness, color.slice());
        nv.drawTextBetween(
          [...projected[1], ...projected[2]],
          `${measurement.value.toFixed(1)} deg`,
          0.62,
          color.slice(),
        );
      }
      projected.forEach(point => drawPoint(point, color, radius));
      return true;
    }

    function drawPending() {
      if (!state.pending.length) return;
      const first = state.pending[0];
      const tile = currentTileFor(first.plane, first.sliceFrac);
      if (!tile) return;
      const points = state.pending.map(point => projectPoint(point.frac, tile, first.plane));
      if (points.some(point => !point)) return;
      const preview = state.preview && sameSlice(first, state.preview)
        ? projectPoint(state.preview.frac, tile, first.plane)
        : null;
      const dpr = Number(nv.uiData?.dpr) || 1;
      const thickness = Math.max(2, 1.5 * dpr);
      const radius = Math.max(3, 3 * dpr);
      for (let index = 1; index < points.length; index += 1) {
        nv.drawLine([...points[index - 1], ...points[index]], thickness, COLORS.pending.slice());
      }
      if (preview) nv.drawLine([...points[points.length - 1], ...preview], thickness, COLORS.pending.slice());
      points.forEach(point => drawPoint(point, COLORS.pending, radius));
    }

    function updateResultList(visibility) {
      const signature = state.measurements.map((item, index) => (
        `${item.id}:${item.value}:${visibility[index] ? 1 : 0}`
      )).join('|');
      if (signature === resultSignature) return;
      resultSignature = signature;
      resultList.replaceChildren();
      state.measurements.forEach((measurement, index) => {
        const item = document.createElement('li');
        const value = measurement.kind === 'length'
          ? `${formatLength(measurement.value)} mm${unit.assumed ? '*' : ''}`
          : `${measurement.value.toFixed(1)} deg`;
        item.textContent = `${measurement.id} ${value} · ${PLANE_NAME[measurement.plane]}`;
        if (!visibility[index]) {
          const status = document.createElement('span');
          status.className = 'sv-nm-off-slice';
          status.textContent = 'off-slice';
          item.append(' ', status);
        }
        resultList.appendChild(item);
      });
    }

    function renderMeasurements() {
      if (destroyed || !nv.initialized) return;
      updateControls();
      const visibility = state.measurements.map(drawStoredMeasurement);
      drawPending();
      updateResultList(visibility);
      if (nv.gl && typeof nv.gl.finish === 'function') nv.gl.finish();
    }

    function queueRender() {
      if (renderFrame !== null || destroyed) return;
      renderFrame = requestAnimationFrame(() => {
        renderFrame = null;
        if (!destroyed) nv.drawScene();
      });
    }

    function pointerDown(event) {
      if (!quantitativeView() || !measurementMode() || event.button !== 0) return;
      const xy = canvasPoint(event);
      const pick = makePick(xy);
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!pick) {
        setNotice('Pick a 2D slice tile; 3D render picking is not quantitative.');
        return;
      }
      state.pointerDown = {
        pointerId: event.pointerId,
        client: [event.clientX, event.clientY],
        pick,
      };
      state.preview = copyPick(pick);
      if (typeof canvas.setPointerCapture === 'function') canvas.setPointerCapture(event.pointerId);
      queueRender();
    }

    function pointerMove(event) {
      if (!quantitativeView() || !measurementMode()) return;
      const xy = canvasPoint(event);
      if (!state.pointerDown) {
        if (state.pending.length) {
          const candidate = makePick(xy);
          state.preview = candidate && sameSlice(state.pending[0], candidate) ? copyPick(candidate) : null;
          queueRender();
        }
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      const candidate = makePick(xy, state.pointerDown.pick.tileIndex);
      state.preview = candidate && sameSlice(state.pointerDown.pick, candidate) ? copyPick(candidate) : null;
      queueRender();
    }

    function pointerUp(event) {
      if (!state.pointerDown || event.pointerId !== state.pointerDown.pointerId) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const down = state.pointerDown;
      state.pointerDown = null;
      const moved = Math.hypot(event.clientX - down.client[0], event.clientY - down.client[1]) >= 4;
      const end = makePick(canvasPoint(event), down.pick.tileIndex);
      state.preview = null;
      if (state.mode === 'length' && moved) {
        state.pending.length = 0;
        if (end) commitLength(down.pick, end);
        else setNotice('Release the length endpoint on the source tile.');
      } else if (state.mode === 'angle' && moved) {
        setNotice('Angle uses three clicks: A, B (vertex), C.');
      } else {
        addClickPoint(down.pick);
      }
      updateControls();
      nv.drawScene();
    }

    function pointerCancel(event) {
      if (!state.pointerDown || event.pointerId !== state.pointerDown.pointerId) return;
      state.pointerDown = null;
      state.preview = null;
      queueRender();
    }

    function blockNativeLeftMouse(event) {
      if (!quantitativeView() || !measurementMode()) return;
      if (event.button === 0 || (Number(event.buttons) & 1) === 1) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }

    function directionalCoordinate(value, negative, positive) {
      const direction = value < -0.005 ? negative : value > 0.005 ? positive : '0';
      return `${formatCoordinate(value)} ${direction}`;
    }

    function updateCoordinates() {
      const frac = Array.from(nv.scene.crosshairPos || [], Number).slice(0, 3);
      if (!finitePoint(frac)) return;
      const mm = worldRASMM(frac);
      const voxel = Array.from(nv.frac2vox(frac), value => Math.round(Number(value))).slice(0, 3);
      coordinateTitle.textContent = `World RAS (${unit.label})`;
      coordinateValues.textContent = [
        `X ${directionalCoordinate(mm[0], 'L', 'R')}`,
        `Y ${directionalCoordinate(mm[1], 'P', 'A')}`,
        `Z ${directionalCoordinate(mm[2], 'I', 'S')}`,
      ].join('  ·  ');
      voxelElement.textContent = `Voxel (RAS-reordered): [${voxel.join(', ')}]`;
    }

    function exportMeasurements() {
      return {
        schema: 'sv.niivue.measurements/1',
        coordinate_space: 'world_RAS',
        units: 'mm',
        units_assumed: unit.assumed,
        items: state.measurements.map(measurement => ({
          id: measurement.id,
          type: measurement.kind,
          plane: PLANE_NAME[measurement.plane].toLowerCase(),
          slice_fraction: measurement.sliceFrac,
          points_ras_mm: measurement.points.map(point => point.rasMM.slice()),
          points_voxel_ras: measurement.points.map(point => point.voxel.slice()),
          ...(measurement.kind === 'length'
            ? { length_mm: measurement.value }
            : { angle_deg: measurement.value }),
        })),
      };
    }

    function exportCrosshair() {
      const frac = Array.from(nv.scene.crosshairPos || [], Number).slice(0, 3);
      return finitePoint(frac) ? worldRASMM(frac) : null;
    }

    function panelClick(event) {
      const modeButton = event.target.closest('button[data-measure-mode]');
      if (modeButton) {
        setMode(modeButton.dataset.measureMode);
        return;
      }
      const actionButton = event.target.closest('button[data-measure-action]');
      if (!actionButton) return;
      if (actionButton.dataset.measureAction === 'undo') undoMeasurementState(state);
      if (actionButton.dataset.measureAction === 'clear') clearMeasurementState(state);
      state.notice = '';
      updateControls();
      nv.drawScene();
    }
    panel.addEventListener('click', panelClick);

    canvas.addEventListener('pointerdown', pointerDown, true);
    canvas.addEventListener('pointermove', pointerMove, true);
    canvas.addEventListener('pointerup', pointerUp, true);
    canvas.addEventListener('pointercancel', pointerCancel, true);
    const blockedMouseEvents = ['mousedown', 'mousemove', 'mouseup', 'click', 'dblclick'];
    for (const eventName of blockedMouseEvents) {
      canvas.addEventListener(eventName, blockNativeLeftMouse, true);
    }

    const originalOnLocationChange = nv.onLocationChange;
    const onLocationChangeWithCoordinates = location => {
      if (typeof originalOnLocationChange === 'function') originalOnLocationChange(location);
      updateCoordinates();
    };
    nv.onLocationChange = onLocationChangeWithCoordinates;

    const originalDrawScene = nv.drawScene.bind(nv);
    function drawSceneWithMeasurements(...args) {
      const result = originalDrawScene(...args);
      try {
        renderMeasurements();
      } catch (error) {
        console.error('[niivue-measurements] render failed', error);
      }
      return result;
    }
    nv.drawScene = drawSceneWithMeasurements;

    const controller = Object.freeze({
      list: exportMeasurements,
      crosshairRASMM: exportCrosshair,
      unitInfo: () => ({ ...unit }),
      setMode,
      undo() {
        const changed = undoMeasurementState(state);
        if (changed) nv.drawScene();
        return changed;
      },
      clear() {
        const changed = clearMeasurementState(state);
        if (changed) nv.drawScene();
        return changed;
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        if (renderFrame !== null) cancelAnimationFrame(renderFrame);
        renderFrame = null;
        panel.removeEventListener('click', panelClick);
        canvas.removeEventListener('pointerdown', pointerDown, true);
        canvas.removeEventListener('pointermove', pointerMove, true);
        canvas.removeEventListener('pointerup', pointerUp, true);
        canvas.removeEventListener('pointercancel', pointerCancel, true);
        for (const eventName of blockedMouseEvents) {
          canvas.removeEventListener(eventName, blockNativeLeftMouse, true);
        }
        panel.remove();
        canvas.classList.remove('sv-niivue-measuring');
        if (nv.drawScene === drawSceneWithMeasurements) nv.drawScene = originalDrawScene;
        if (nv.onLocationChange === onLocationChangeWithCoordinates) nv.onLocationChange = originalOnLocationChange;
        if (nv.__svMeasurementController === controller) delete nv.__svMeasurementController;
        if (window.__ScienceVisionNiiVueMeasurementProvider === measurementProvider) {
          delete window.__ScienceVisionNiiVueMeasurementProvider;
        }
      },
    });

    Object.defineProperty(nv, '__svMeasurementController', {
      configurable: true,
      enumerable: false,
      writable: false,
      value: controller,
    });
    measurementProvider = Object.freeze({ list: exportMeasurements });
    Object.defineProperty(window, '__ScienceVisionNiiVueMeasurementProvider', {
      configurable: true,
      enumerable: false,
      writable: false,
      value: measurementProvider,
    });

    updateControls();
    updateCoordinates();
    nv.drawScene();
    return controller;
  }

  return Object.freeze({
    install,
    clampCosine,
    distanceMM,
    angleDegrees,
    undoMeasurementState,
    clearMeasurementState,
    spatialUnitInfo,
    fractionToWorldRASMM,
  });
}));
