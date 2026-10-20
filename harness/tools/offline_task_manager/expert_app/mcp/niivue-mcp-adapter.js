(function installScienceVisionNiivueMCPAdapter(global) {
  'use strict';

  // HUD layer order in viewer/niivue.html: frac2vox [0]=sagittal, [1]=coronal, [2]=axial.
  const MODE_TO_SLICE = Object.freeze({ axial: 0, mpr: 3, render: 4 });
  const SLICE_TO_MODE = Object.freeze({ 0: 'axial', 3: 'mpr', 4: 'render' });
  // NiiVue 0.45: scene.clipPlane is the plane equation; setClipPlane takes
  // [depth, azimuth, elevation]. depth > 1 means clip is off.
  const DEFAULT_CLIP = Object.freeze([2, 0, 0]);
  const MEASURE_KINDS = Object.freeze(['length', 'angle']);
  const MEASURE_PLANES = Object.freeze(['axial', 'coronal', 'sagittal']);
  const PAN_PLANE_TO_SLICE = Object.freeze({ axial: 0, coronal: 1, sagittal: 2 });

  function requireFiniteNumber(value, name) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`${name} must be a finite number.`);
    return number;
  }

  function requireBoolean(value, name) {
    if (typeof value !== 'boolean') throw new Error(`${name} must be a boolean.`);
    return value;
  }

  function requireInteger(value, name) {
    const number = requireFiniteNumber(value, name);
    if (!Number.isInteger(number)) throw new Error(`${name} must be an integer.`);
    return number;
  }

  function copy3(value, fallback) {
    if (value && value.length >= 3) {
      return [Number(value[0]), Number(value[1]), Number(value[2])];
    }
    return fallback.slice();
  }

  function copyVec(value, fallback) {
    if (value && typeof value.length === 'number' && value.length) {
      return Array.from(value, item => Number(item));
    }
    return fallback.slice();
  }

  function volumeDims(volume) {
    const raw = volume && volume.dimsRAS;
    if (!raw || raw.length < 4) throw new Error('NiiVue volume is unavailable.');
    return [Number(raw[1]), Number(raw[2]), Number(raw[3])];
  }

  function normalizeMeasurementPoints(kind, value) {
    const expected = kind === 'length' ? 2 : 3;
    if (!Array.isArray(value) || value.length !== expected
        || !value.every(point => Array.isArray(point) && point.length === 3
          && point.every(component => typeof component === 'number' && Number.isFinite(component)))) {
      throw new Error(
        `${kind} measurement requires exactly ${expected} three-dimensional finite points.`,
      );
    }
    return value.map(point => point.map(Number));
  }

  function measurementValue(kind, points) {
    if (kind === 'length') {
      const value = Math.hypot(
        points[0][0] - points[1][0],
        points[0][1] - points[1][1],
        points[0][2] - points[1][2],
      );
      if (!(value > 0)) throw new Error('Length requires two distinct points.');
      return value;
    }
    const first = points[0].map((value, index) => value - points[1][index]);
    const second = points[2].map((value, index) => value - points[1][index]);
    const firstLength = Math.hypot(...first);
    const secondLength = Math.hypot(...second);
    if (!(firstLength > 0) || !(secondLength > 0)) {
      throw new Error('Angle requires non-zero A-B and C-B vectors.');
    }
    const dot = first.reduce((sum, value, index) => sum + value * second[index], 0);
    const cosine = Math.max(-1, Math.min(1, dot / (firstLength * secondLength)));
    return Math.acos(cosine) * 180 / Math.PI;
  }

  function createProvider(nv) {
    const registry = global.__ScienceVisionNiivueToolRegistry;
    if (!registry || !Array.isArray(registry.tools)) {
      throw new Error('NiiVue tool registry is unavailable.');
    }
    if (!nv || !Array.isArray(nv.volumes) || !nv.volumes[0]) {
      throw new Error('NiiVue volume is unavailable.');
    }

    const primary = nv.volumes[0];
    const overlay = nv.volumes[1];
    const tools = overlay
      ? registry.tools
      : registry.tools.filter(tool => (
        tool.name !== 'sv3d_set_mask_visibility'
        && tool.name !== 'sv3d_set_mask_opacity'
        && tool.name !== 'sv3d_mask_geometry'
      ));
    let lastMaskOpacity = overlay && Number(overlay.opacity) > 0
      ? Number(overlay.opacity)
      : 0.5;
    let clipDepthAziElev = copy3(
      nv.scene && nv.scene.clipPlaneDepthAziElev,
      DEFAULT_CLIP,
    );
    const initial = {
      sliceType: nv.opts.sliceType,
      crosshairPos: copyVec(nv.scene.crosshairPos, [0.5, 0.5, 0.5]),
      cal_min: Number(primary.cal_min),
      cal_max: Number(primary.cal_max),
      volScaleMultiplier: Number(nv.scene.volScaleMultiplier),
      pan2Dxyzmm: copyVec(nv.scene.pan2Dxyzmm, [0, 0, 0, 1]),
      renderAzimuth: Number(nv.scene.renderAzimuth),
      renderElevation: Number(nv.scene.renderElevation),
      clipDepthAziElev: clipDepthAziElev.slice(),
      maskOpacity: overlay ? Number(overlay.opacity) : null,
    };
    const initialPanTarget = Object.freeze({
      plane: 'axial',
      right_fraction: 0,
      up_fraction: 0,
    });
    let panTarget = { ...initialPanTarget };
    let measureWatchGeneration = 0;

    function currentClip() {
      const live = nv.scene && nv.scene.clipPlaneDepthAziElev;
      if (live && live.length >= 3) return copy3(live, clipDepthAziElev);
      return clipDepthAziElev.slice();
    }

    function currentPan2D() {
      const pan = copyVec(nv.scene && nv.scene.pan2Dxyzmm, [0, 0, 0, 1]);
      while (pan.length < 4) pan.push(pan.length === 3 ? 1 : 0);
      return pan;
    }

    function currentZoom() {
      if (Number(nv.opts.sliceType) === MODE_TO_SLICE.render) {
        return Number(nv.scene.volScaleMultiplier);
      }
      return Number(currentPan2D()[3]);
    }

    function applyPan2D(pan) {
      if (typeof nv.setPan2Dxyzmm === 'function') nv.setPan2Dxyzmm(pan.slice());
      else nv.scene.pan2Dxyzmm = pan.slice();
    }

    function isIdentityPan(right, up) {
      return Math.abs(right) <= 1e-9 && Math.abs(up) <= 1e-9;
    }

    // 0,0 is "back to the adapter-load pan". After a live drag hides the tile,
    // screenXY2mm cannot compute that target; applying the snapshotted millimetres
    // is the only restore that does not depend on the broken mapping.
    function restoreIdentityPan(plane) {
      applyPan2D(initial.pan2Dxyzmm);
      panTarget = { plane, right_fraction: 0, up_fraction: 0 };
      if (typeof nv.drawScene === 'function') nv.drawScene();
      return { replay_input: { ...panTarget }, state: getState() };
    }

    function currentMeasurements() {
      const api = global.__svNiiVueMeasurements;
      if (!api || typeof api.list !== 'function') return [];
      const listed = api.list();
      if (Array.isArray(listed)) return listed;
      if (listed && Array.isArray(listed.items)) return listed.items;
      return [];
    }

    function maskState() {
      const present = Boolean(nv.volumes[1]);
      const opacity = present ? Number(nv.volumes[1].opacity) : 0;
      const rawId = global.document && global.document.body && global.document.body.dataset
        ? global.document.body.dataset.maskId
        : undefined;
      let mask_id = null;
      if (rawId != null && /^(0|[1-9]\d*)$/.test(String(rawId))) {
        mask_id = Number(rawId);
      }
      return {
        present,
        visible: present && opacity > 0,
        opacity: present ? opacity : 0,
        mask_id,
      };
    }

    function worldRasMM(frac) {
      const hud = global.ScienceVisionNiiVueMeasurements;
      if (hud && typeof hud.fractionToWorldRASMM === 'function') {
        try {
          const converted = hud.fractionToWorldRASMM(nv, frac);
          if (converted && converted.length >= 3) return copy3(converted, [0, 0, 0]);
        } catch (_error) {}
      }
      return copy3(nv.frac2mm(frac), [0, 0, 0]);
    }

    function getState() {
      const volume = nv.volumes[0];
      const frac = copy3(nv.scene.crosshairPos, [0.5, 0.5, 0.5]);
      const vox = nv.frac2vox(frac);
      const mm = worldRasMM(frac);
      const calMin = Number(volume.cal_min);
      const calMax = Number(volume.cal_max);
      const sliceType = Number(nv.opts.sliceType);
      const clip = currentClip();
      return {
        viewer: 'niivue',
        mode: SLICE_TO_MODE[sliceType] || null,
        slice_type: sliceType,
        crosshair: {
          voxel: copy3(vox, [0, 0, 0]).map(value => Math.round(value)),
          fraction: frac,
          world_ras_mm: copy3(mm, [0, 0, 0]),
        },
        window: calMax - calMin,
        level: (calMax + calMin) / 2,
        cal_min: calMin,
        cal_max: calMax,
        zoom: currentZoom(),
        pan: { ...panTarget },
        render: {
          azimuth_deg: Number(nv.scene.renderAzimuth),
          elevation_deg: Number(nv.scene.renderElevation),
        },
        clip: {
          enabled: clip[0] <= 1,
          depth: clip[0],
          azimuth_deg: clip[1],
          elevation_deg: clip[2],
        },
        mask: maskState(),
        measurements: currentMeasurements(),
        dims: volumeDims(volume),
      };
    }

    function applySliceVoxels(voxel) {
      const frac = nv.vox2frac(voxel);
      nv.scene.crosshairPos = copy3(frac, voxel);
      if (typeof nv.createOnLocationChange === 'function') nv.createOnLocationChange();
      if (typeof nv.drawScene === 'function') nv.drawScene();
    }

    function restoreSnapshot() {
      if (typeof nv.setSliceType === 'function') nv.setSliceType(initial.sliceType);
      else nv.opts.sliceType = initial.sliceType;
      nv.scene.crosshairPos = initial.crosshairPos.slice();
      nv.volumes[0].cal_min = initial.cal_min;
      nv.volumes[0].cal_max = initial.cal_max;
      nv.scene.volScaleMultiplier = initial.volScaleMultiplier;
      applyPan2D(initial.pan2Dxyzmm);
      panTarget = { ...initialPanTarget };
      nv.scene.renderAzimuth = initial.renderAzimuth;
      nv.scene.renderElevation = initial.renderElevation;
      clipDepthAziElev = initial.clipDepthAziElev.slice();
      applyClip(clipDepthAziElev, false);
      if (nv.volumes[1] && initial.maskOpacity != null) {
        applyMaskOpacity(initial.maskOpacity, false);
        if (initial.maskOpacity > 0) lastMaskOpacity = initial.maskOpacity;
      }
      if (typeof nv.updateGLVolume === 'function') nv.updateGLVolume();
      if (typeof nv.drawScene === 'function') nv.drawScene();
    }

    function applyClip(plane, draw) {
      clipDepthAziElev = plane.slice();
      if (nv.scene) nv.scene.clipPlaneDepthAziElev = clipDepthAziElev.slice();
      if (typeof nv.setClipPlane === 'function') nv.setClipPlane(clipDepthAziElev.slice());
      if (draw !== false && typeof nv.drawScene === 'function') nv.drawScene();
    }

    function requireOverlay() {
      if (!nv.volumes[1]) throw new Error('Overlay mask is not loaded.');
      return nv.volumes[1];
    }

    function overlayPixDims(volume) {
      const raw = volume && volume.pixDimsRAS;
      if (raw && raw.length >= 4) {
        return [Number(raw[1]), Number(raw[2]), Number(raw[3])];
      }
      if (raw && raw.length >= 3) {
        return [Number(raw[0]), Number(raw[1]), Number(raw[2])];
      }
      return [1, 1, 1];
    }

    function round4(value) {
      return Math.round(Number(value) * 10000) / 10000;
    }

    function computeMaskGeometry(volume, maskId) {
      const dims = volumeDims(volume);
      const nx = dims[0];
      const ny = dims[1];
      const nz = dims[2];
      const img = volume.img;
      const expected = nx * ny * nz;
      if (!img || typeof img.length !== 'number' || img.length < expected) {
        throw new Error('Overlay mask voxels are unavailable.');
      }
      const selectAll = maskId == null;
      let count = 0;
      let minX = Infinity;
      let minY = Infinity;
      let minZ = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      let maxZ = -Infinity;
      let sumX = 0;
      let sumY = 0;
      let sumZ = 0;
      const axialArea = new Array(nz).fill(0);
      for (let z = 0; z < nz; z += 1) {
        for (let y = 0; y < ny; y += 1) {
          for (let x = 0; x < nx; x += 1) {
            const value = Number(img[x + nx * (y + ny * z)]);
            const hit = selectAll ? value !== 0 : value === maskId;
            if (!hit) continue;
            count += 1;
            if (x < minX) minX = x;
            if (y < minY) minY = y;
            if (z < minZ) minZ = z;
            if (x > maxX) maxX = x;
            if (y > maxY) maxY = y;
            if (z > maxZ) maxZ = z;
            sumX += x;
            sumY += y;
            sumZ += z;
            axialArea[z] += 1;
          }
        }
      }
      if (!count) throw new Error('No mask voxels for the current mask_id.');
      const extent = [maxX - minX + 1, maxY - minY + 1, maxZ - minZ + 1];
      const bboxVolume = extent[0] * extent[1] * extent[2];
      let bestAxial = 0;
      let maxArea = -1;
      let tied = 0;
      for (let z = 0; z < nz; z += 1) {
        const area = axialArea[z];
        if (area > maxArea) {
          maxArea = area;
          bestAxial = z;
          tied = 1;
        } else if (area === maxArea) {
          tied += 1;
        }
      }
      const longest = Math.max(extent[0], extent[1], extent[2]);
      const shortest = Math.min(extent[0], extent[1], extent[2]);
      const axisNames = ['sagittal', 'coronal', 'axial'];
      const longestAxes = [];
      for (let index = 0; index < 3; index += 1) {
        if (extent[index] === longest) longestAxes.push(axisNames[index]);
      }
      const pix = overlayPixDims(volume);
      const voxelVolume = pix[0] * pix[1] * pix[2];
      return {
        mask_id: selectAll ? null : maskId,
        mask_voxel_count: count,
        mask_volume_mm3: count * voxelVolume,
        bbox_min_vox: [minX, minY, minZ],
        bbox_max_vox_inclusive: [maxX, maxY, maxZ],
        extent_vox: extent,
        bbox_volume_vox: bboxVolume,
        bbox_fill_ratio: count / bboxVolume,
        centroid_vox: [round4(sumX / count), round4(sumY / count), round4(sumZ / count)],
        best_axial_slice: bestAxial,
        max_axial_area_vox: maxArea,
        best_slice_tied_count: tied,
        longest_axes: longestAxes,
        longest_to_shortest_extent_ratio: longest / shortest,
      };
    }

    function applyMaskOpacity(opacity, draw) {
      if (typeof nv.setOpacity === 'function') nv.setOpacity(1, opacity);
      else nv.volumes[1].opacity = opacity;
      if (draw !== false) {
        if (typeof nv.updateGLVolume === 'function') nv.updateGLVolume();
        if (typeof nv.drawScene === 'function') nv.drawScene();
      }
    }

    function measurementsApi() {
      const api = global.__svNiiVueMeasurements;
      if (!api || typeof api.setMode !== 'function') {
        throw new Error('NiiVue measurement tools are unavailable.');
      }
      return api;
    }

    function optionalArg(args, name, fallback, label) {
      if (args && Object.prototype.hasOwnProperty.call(args, name) && args[name] !== undefined) {
        return requireFiniteNumber(args[name], label || name);
      }
      return fallback;
    }

    function setSvMcpPick(flag) {
      const root = global.document && global.document.documentElement;
      if (root) root.dataset.svMcpPick = flag;
    }

    function measurementPayload(kind, source) {
      const listed = currentMeasurements();
      const last = listed.length ? listed[listed.length - 1] : null;
      const points = source && Array.isArray(source.points_ras_mm)
        ? source.points_ras_mm
        : ((last && last.points_ras_mm) || []);
      const resolvedKind = (source && source.kind) || (last && (last.type || last.kind)) || kind;
      const plane = (source && source.plane) || (last && last.plane);
      const normalizedPoints = normalizeMeasurementPoints(resolvedKind, points);
      const payload = {
        kind: resolvedKind,
        plane,
        points_ras_mm: normalizedPoints,
        value: String(measurementValue(resolvedKind, normalizedPoints)),
      };
      return payload;
    }

    function attachMeasureCompletion(kind, api) {
      const generation = ++measureWatchGeneration;
      const before = currentMeasurements().length;
      const finish = source => {
        if (generation !== measureWatchGeneration) return;
        measureWatchGeneration += 1;
        setSvMcpPick('0');
        if (typeof api.setMode === 'function') {
          try { api.setMode('navigate'); } catch (_error) {}
        }
        const notify = global.__ScienceVisionNiivueMeasurementCommitted;
        if (typeof notify === 'function') notify(measurementPayload(kind, source));
      };
      if (typeof api.onCompleted === 'function') {
        api.onCompleted(completed => finish(completed));
        return;
      }
      const tick = () => {
        if (generation !== measureWatchGeneration) return;
        if ((global.document && global.document.documentElement.dataset.svMcpPick) !== '1') return;
        const listed = currentMeasurements();
        if (listed.length > before) {
          finish(listed[listed.length - 1]);
          return;
        }
        if (typeof global.requestAnimationFrame === 'function') global.requestAnimationFrame(tick);
      };
      if (typeof global.requestAnimationFrame === 'function') global.requestAnimationFrame(tick);
    }

    async function callTool(name, args) {
      if (name === 'viewer_get_state') return getState();
      if (name === 'viewer_reset') {
        measureWatchGeneration += 1;
        setSvMcpPick('0');
        const api = global.__svNiiVueMeasurements;
        if (api && typeof api.clear === 'function') api.clear();
        restoreSnapshot();
        return { reset: true, state: getState() };
      }
      if (name === 'sv3d_set_view_mode') {
        const mode = args && args.mode;
        if (!Object.prototype.hasOwnProperty.call(MODE_TO_SLICE, mode)) {
          throw new Error('mode must be mpr, axial, or render.');
        }
        measureWatchGeneration += 1;
        setSvMcpPick('0');
        nv.setSliceType(MODE_TO_SLICE[mode]);
        if (typeof nv.drawScene === 'function') nv.drawScene();
        return { replay_input: { mode }, state: getState() };
      }
      if (name === 'sv3d_set_slice') {
        const dims = volumeDims(nv.volumes[0]);
        const current = copy3(nv.frac2vox(copy3(nv.scene.crosshairPos, [0.5, 0.5, 0.5])), [0, 0, 0])
          .map(value => Math.round(value));
        const voxel = current.slice();
        const axes = [['sagittal', 0], ['coronal', 1], ['axial', 2]];
        let specified = 0;
        for (const [axis, index] of axes) {
          if (args && Object.prototype.hasOwnProperty.call(args, axis) && args[axis] !== undefined) {
            voxel[index] = requireInteger(args[axis], axis);
            specified += 1;
          }
        }
        if (!specified) {
          throw new Error('at least one of sagittal, coronal, axial is required.');
        }
        for (let index = 0; index < axes.length; index += 1) {
          const dim = dims[index];
          if (voxel[index] < 0 || voxel[index] > dim - 1) {
            throw new Error(`${axes[index][0]} slice ${voxel[index]} is out of range (0-${dim - 1}).`);
          }
        }
        applySliceVoxels(voxel);
        return {
          replay_input: { sagittal: voxel[0], coronal: voxel[1], axial: voxel[2] },
          state: getState(),
        };
      }
      if (name === 'viewer_window_level') {
        const windowWidth = requireFiniteNumber(args && args.window, 'window');
        if (windowWidth <= 0) throw new Error('window must be greater than 0.');
        const level = requireFiniteNumber(args && args.level, 'level');
        nv.volumes[0].cal_min = level - windowWidth / 2;
        nv.volumes[0].cal_max = level + windowWidth / 2;
        if (typeof nv.updateGLVolume === 'function') nv.updateGLVolume();
        if (typeof nv.drawScene === 'function') nv.drawScene();
        return { replay_input: { window: windowWidth, level }, state: getState() };
      }
      if (name === 'viewer_zoom') {
        const scale = requireFiniteNumber(args && args.scale, 'scale');
        if (scale <= 0) throw new Error('scale must be greater than 0.');
        if (Number(nv.opts.sliceType) === MODE_TO_SLICE.render) {
          nv.scene.volScaleMultiplier = scale;
        } else {
          const pan = currentPan2D();
          pan[3] = scale;
          applyPan2D(pan);
        }
        if (typeof nv.drawScene === 'function') nv.drawScene();
        return { replay_input: { scale }, state: getState() };
      }
      if (name === 'viewer_pan') {
        const plane = args && args.plane;
        const right = requireFiniteNumber(args && args.right_fraction, 'right_fraction');
        const up = requireFiniteNumber(args && args.up_fraction, 'up_fraction');
        if (Number(nv.opts.sliceType) === MODE_TO_SLICE.render) {
          throw new Error('viewer_pan is unavailable in 3D render mode.');
        }
        if (!Object.prototype.hasOwnProperty.call(PAN_PLANE_TO_SLICE, plane)) {
          throw new Error('plane must be axial, coronal, or sagittal.');
        }
        if (!Array.isArray(nv.screenSlices) || typeof nv.screenXY2mm !== 'function') {
          if (isIdentityPan(right, up)) return restoreIdentityPan(plane);
          throw new Error('NiiVue screen-to-world mapping is unavailable for viewer_pan.');
        }
        const planeCode = PAN_PLANE_TO_SLICE[plane];
        const tileIndex = nv.screenSlices.findIndex(tile => (
          tile && Number(tile.axCorSag) === planeCode
          && Array.isArray(tile.leftTopWidthHeight)
          && tile.leftTopWidthHeight.length >= 4
        ));
        if (tileIndex < 0) {
          if (isIdentityPan(right, up)) return restoreIdentityPan(plane);
          throw new Error(`The ${plane} slice tile is not currently visible.`);
        }
        const rect = nv.screenSlices[tileIndex].leftTopWidthHeight.map(Number);
        if (!rect.every(Number.isFinite) || !(rect[2] > 0) || !(rect[3] > 0)) {
          if (isIdentityPan(right, up)) return restoreIdentityPan(plane);
          throw new Error(`The ${plane} slice tile has invalid viewport bounds.`);
        }
        const center = [rect[0] + rect[2] / 2, rect[1] + rect[3] / 2];
        // screenXY2mm rejects points outside a tile. Sample the same vector within
        // the tile and linearly extrapolate so the public [-1, 1] viewport target
        // remains well-defined beyond a half-width/half-height from the centre.
        const sampleScale = Math.max(1, Math.abs(right) * 2.000001, Math.abs(up) * 2.000001);
        const end = [
          center[0] + right / sampleScale * rect[2],
          center[1] - up / sampleScale * rect[3],
        ];
        const endMM = copyVec(nv.screenXY2mm(end[0], end[1]), [NaN, NaN, NaN, NaN]);
        const resolvedTile = Number(endMM[3]);
        const startMM = copyVec(
          nv.screenXY2mm(center[0], center[1], resolvedTile),
          [NaN, NaN, NaN, NaN],
        );
        if (endMM.length < 4 || startMM.length < 3 || !Number.isFinite(resolvedTile)
            || !endMM.slice(0, 3).every(Number.isFinite)
            || !startMM.slice(0, 3).every(Number.isFinite)) {
          if (isIdentityPan(right, up)) return restoreIdentityPan(plane);
          throw new Error(`NiiVue could not map the ${plane} viewport to world millimetres.`);
        }
        const pan = initial.pan2Dxyzmm.slice();
        while (pan.length < 4) pan.push(pan.length === 3 ? 1 : 0);
        const zoom = currentPan2D()[3];
        for (let index = 0; index < 3; index += 1) {
          pan[index] = initial.pan2Dxyzmm[index]
            + zoom * (endMM[index] - startMM[index]) * sampleScale;
        }
        pan[3] = zoom;
        applyPan2D(pan);
        panTarget = { plane, right_fraction: right, up_fraction: up };
        if (typeof nv.drawScene === 'function') nv.drawScene();
        return { replay_input: { ...panTarget }, state: getState() };
      }
      if (name === 'sv3d_set_render_angles') {
        const azimuth = requireFiniteNumber(args && args.azimuth_deg, 'azimuth_deg');
        const elevation = requireFiniteNumber(args && args.elevation_deg, 'elevation_deg');
        nv.scene.renderAzimuth = azimuth;
        nv.scene.renderElevation = elevation;
        if (typeof nv.drawScene === 'function') nv.drawScene();
        return {
          replay_input: { azimuth_deg: azimuth, elevation_deg: elevation },
          state: getState(),
        };
      }
      if (name === 'sv3d_set_clip_plane') {
        const enabled = requireBoolean(args && args.enabled, 'enabled');
        if (!enabled) {
          applyClip(DEFAULT_CLIP.slice());
          return { replay_input: { enabled: false }, state: getState() };
        }
        const current = currentClip();
        const depthFallback = current[0] > 1 ? 0.5 : current[0];
        const depth = optionalArg(args, 'depth', depthFallback);
        const azimuth = optionalArg(args, 'azimuth_deg', current[1]);
        const elevation = optionalArg(args, 'elevation_deg', current[2]);
        applyClip([depth, azimuth, elevation]);
        return {
          replay_input: {
            enabled: true,
            depth,
            azimuth_deg: azimuth,
            elevation_deg: elevation,
          },
          state: getState(),
        };
      }
      if (name === 'sv3d_set_mask_visibility') {
        requireOverlay();
        const visible = requireBoolean(args && args.visible, 'visible');
        const currentOpacity = Number(nv.volumes[1].opacity);
        if (!visible) {
          if (currentOpacity > 0) lastMaskOpacity = currentOpacity;
          applyMaskOpacity(0);
        } else {
          applyMaskOpacity(lastMaskOpacity || 0.5);
        }
        return { replay_input: { visible }, state: getState() };
      }
      if (name === 'sv3d_set_mask_opacity') {
        requireOverlay();
        const opacity = requireFiniteNumber(args && args.opacity, 'opacity');
        if (opacity > 0) lastMaskOpacity = opacity;
        applyMaskOpacity(opacity);
        return { replay_input: { opacity }, state: getState() };
      }
      if (name === 'sv3d_measure') {
        const kind = args && args.kind;
        if (!MEASURE_KINDS.includes(kind)) {
          throw new Error('kind must be length or angle.');
        }
        const mode = SLICE_TO_MODE[Number(nv.opts.sliceType)] || null;
        if (mode === 'render' && !(args && args.commit === true)) {
          throw new Error('sv3d_measure is unavailable in 3D render mode.');
        }
        const api = measurementsApi();
        if (args && args.commit === true) {
          measureWatchGeneration += 1;
          setSvMcpPick('0');
          if (typeof api.setMode === 'function') {
            try { api.setMode('navigate'); } catch (_error) {}
          }
          let points = Array.isArray(args.points_ras_mm) ? args.points_ras_mm : null;
          let plane = args.plane;
          let last = null;
          if (!points) {
            const listed = currentMeasurements();
            last = listed.length ? listed[listed.length - 1] : null;
            if (!last || !Array.isArray(last.points_ras_mm)) {
              throw new Error('Measurement was cancelled before it completed.');
            }
            points = last.points_ras_mm;
            if (!plane) plane = last.plane;
          }
          if (!MEASURE_PLANES.includes(plane)) {
            throw new Error('plane must be axial, coronal, or sagittal.');
          }
          const normalizedPoints = normalizeMeasurementPoints(kind, points);
          const value = measurementValue(kind, normalizedPoints);
          if (typeof api.applyMeasurement === 'function') {
            api.applyMeasurement({ kind, plane, points_ras_mm: normalizedPoints });
          } else if (!last) {
            const listed = currentMeasurements();
            last = listed.length ? listed[listed.length - 1] : null;
            if (!last) throw new Error('NiiVue measurement replay is unavailable.');
          }
          const replayInput = {
            kind,
            commit: true,
            plane,
            points_ras_mm: normalizedPoints,
            value: String(value),
          };
          return { replay_input: replayInput, state: getState() };
        }
        setSvMcpPick('1');
        attachMeasureCompletion(kind, api);
        api.setMode(kind);
        return { replay_input: { kind }, state: getState() };
      }
      if (name === 'sv3d_clear_measurements') {
        measureWatchGeneration += 1;
        setSvMcpPick('0');
        const api = global.__svNiiVueMeasurements;
        if (api && typeof api.clear === 'function') api.clear();
        if (typeof nv.drawScene === 'function') nv.drawScene();
        return { replay_input: {}, state: getState() };
      }
      if (name === 'sv3d_mask_geometry') {
        const overlay = requireOverlay();
        const goto = args && args.goto ? args.goto : 'none';
        if (goto !== 'none' && goto !== 'best_axial') {
          throw new Error('goto must be none or best_axial.');
        }
        const geometry = computeMaskGeometry(overlay, maskState().mask_id);
        if (goto === 'best_axial') {
          const dims = volumeDims(nv.volumes[0]);
          const voxel = geometry.centroid_vox.map(value => Math.round(value));
          voxel[2] = geometry.best_axial_slice;
          for (let index = 0; index < 3; index += 1) {
            voxel[index] = Math.max(0, Math.min(dims[index] - 1, voxel[index]));
          }
          applySliceVoxels(voxel);
        }
        geometry.centroid_world_ras_mm = worldRasMM(
          nv.vox2frac(geometry.centroid_vox),
        );
        return {
          replay_input: { goto },
          result: geometry,
          state: getState(),
        };
      }
      throw new Error(`Unsupported NiiVue MCP tool: ${name}.`);
    }

    return Object.freeze({ tools: Object.freeze(tools.slice()), callTool });
  }

  Object.defineProperty(global, '__ScienceVisionCreateNiivueMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
  // Published expert runtimes (ev2-pilot-20260820) call CreateNiiVue… (capital V).
  Object.defineProperty(global, '__ScienceVisionCreateNiiVueMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
}(window));
