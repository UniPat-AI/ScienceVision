(function installScienceVisionNiiVueMCPAdapter() {
  'use strict';

  const SLICE = Object.freeze({ axial: 0, coronal: 1, sagittal: 2, multiplanar: 3, render: 4 });
  const AXIS_COMPONENT = Object.freeze({ sagittal: 0, coronal: 1, axial: 2 });
  const emptySchema = Object.freeze({ type: 'object', properties: {}, additionalProperties: false });

  const tools = Object.freeze([
    {
      name: 'viewer_rotate',
      description: 'Increment the 3D volume-render azimuth/elevation in degrees. Non-zero roll is unsupported by NiiVue.',
      inputSchema: {
        type: 'object',
        properties: {
          d_azimuth_deg: { type: 'number', description: 'Incremental azimuth in degrees.' },
          d_elevation_deg: { type: 'number', description: 'Incremental elevation in degrees.' },
          d_roll_deg: { type: 'number', default: 0, description: 'Must be 0; NiiVue has no roll control.' },
        },
        required: ['d_azimuth_deg', 'd_elevation_deg'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_slice',
      description: 'Show an anatomical slice and move the crosshair by voxel index or normalized fraction.',
      inputSchema: {
        type: 'object',
        properties: {
          axis: { type: 'string', enum: ['axial', 'coronal', 'sagittal'] },
          index: { type: 'integer', minimum: 0, description: 'Inclusive zero-based voxel index.' },
          fraction: { type: 'number', minimum: 0, maximum: 1, description: 'Normalized slice position.' },
        },
        required: ['axis'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_window_level',
      description: 'Set display window center and positive width for a volume layer.',
      inputSchema: {
        type: 'object',
        properties: {
          center: { type: 'number' },
          width: { type: 'number', exclusiveMinimum: 0 },
          layer: { type: 'integer', minimum: 0, default: 0 },
        },
        required: ['center', 'width'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_bbox',
      description: 'Compute the inclusive non-zero voxel bounding box of an overlay/mask layer; optionally center the crosshair.',
      inputSchema: {
        type: 'object',
        properties: {
          layer: { type: 'integer', minimum: 1, default: 1, description: 'Overlay layer index; the base image is layer 0.' },
          center: { type: 'boolean', default: false },
        },
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_set_view',
      description: 'Switch between 3D render, MPR, or a single anatomical plane.',
      inputSchema: {
        type: 'object',
        properties: { mode: { type: 'string', enum: ['render', 'multiplanar', 'axial', 'coronal', 'sagittal'] } },
        required: ['mode'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_crosshair',
      description: 'Move the crosshair using a fixed three-number voxel or normalized-fraction coordinate.',
      inputSchema: {
        type: 'object',
        properties: {
          space: { type: 'string', enum: ['voxel', 'fraction'], default: 'voxel' },
          position: {
            type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3,
            description: '[x, y, z] in voxel or normalized fraction space.',
          },
        },
        required: ['position'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_zoom',
      description: 'Multiply 2D and 3D zoom; factor > 1 zooms in.',
      inputSchema: {
        type: 'object',
        properties: { factor: { type: 'number', minimum: 0.05, maximum: 20 } },
        required: ['factor'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_pan',
      description: 'Increment the 2D pan vector in world millimeters.',
      inputSchema: {
        type: 'object',
        properties: {
          delta_mm: { type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3 },
        },
        required: ['delta_mm'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_visibility',
      description: 'Set a volume layer opacity or visible state. Layer 0 is the base image; overlays start at 1.',
      inputSchema: {
        type: 'object',
        properties: {
          layer: { type: 'integer', minimum: 0 },
          visible: { type: 'boolean' },
          opacity: { type: 'number', minimum: 0, maximum: 1 },
        },
        required: ['layer'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_clip_plane',
      description: 'Set or disable the 3D clip plane using NiiVue depth/azimuth/elevation parameters.',
      inputSchema: {
        type: 'object',
        properties: {
          enabled: { type: 'boolean', default: true },
          depth: { type: 'number', minimum: -1, maximum: 1, default: 0 },
          azimuth_deg: { type: 'number', default: 0 },
          elevation_deg: { type: 'number', minimum: -90, maximum: 90, default: 0 },
        },
        additionalProperties: false,
      },
    },
    { name: 'viewer_reset', description: 'Restore the view and loaded-layer display state captured after initial load.', inputSchema: emptySchema },
    { name: 'viewer_get_state', description: 'Return the current NiiVue view, crosshair, layer, zoom, pan, and clip state.', inputSchema: emptySchema },
  ]);

  function dimensions(volume) {
    const raw = volume?.dimsRAS || volume?.hdr?.dims;
    const dims = raw && [Number(raw[1]), Number(raw[2]), Number(raw[3])];
    if (!dims || dims.some(value => !Number.isInteger(value) || value <= 0)) {
      throw new Error('Loaded volume dimensions are unavailable or invalid.');
    }
    return dims;
  }

  function volumeAt(nv, index) {
    if (!Number.isInteger(index) || index < 0 || index >= nv.volumes.length) {
      throw new Error(`Layer ${index} is out of bounds; loaded layer count is ${nv.volumes.length}.`);
    }
    return nv.volumes[index];
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function syncViewButtons(mode) {
    for (const button of document.querySelectorAll('#view-toggle button[data-mode]')) {
      button.classList.toggle('active', button.dataset.mode === mode);
    }
    const hint = document.getElementById('gesture-hint');
    if (hint) {
      hint.textContent = mode === 'render'
        ? 'drag = rotate · wheel = clip · right-drag = pan'
        : 'drag = pan · wheel = slice · right-drag = window/level';
    }
  }

  function notifyLocation(nv) {
    if (typeof nv.createOnLocationChange === 'function') nv.createOnLocationChange();
    nv.drawScene();
  }

  function createProvider(nv) {
    if (!nv || !Array.isArray(nv.volumes) || !nv.volumes.length) {
      throw new Error('NiiVue is not initialized with a volume.');
    }
    const initial = {
      sliceType: nv.opts.sliceType,
      crosshair: Array.from(nv.scene.crosshairPos.slice(0, 3), Number),
      pan: Array.from(nv.scene.pan2Dxyzmm, Number),
      scale: Number(nv.scene.volScaleMultiplier),
      azimuth: Number(nv.scene.renderAzimuth),
      elevation: Number(nv.scene.renderElevation),
      clip: Array.from(nv.scene.clipPlaneDepthAziElev, Number),
      layers: nv.volumes.map(volume => ({
        opacity: Number(volume.opacity),
        calMin: Number(volume.cal_min),
        calMax: Number(volume.cal_max),
      })),
    };
    const lastVisibleOpacity = nv.volumes.map(volume => Number(volume.opacity) > 0 ? Number(volume.opacity) : 1);

    function setCrosshair(position, space) {
      const base = volumeAt(nv, 0);
      const dims = dimensions(base);
      let fraction;
      if (space === 'fraction') {
        if (position.some(value => value < 0 || value > 1)) throw new Error('Fraction coordinates must each be within [0, 1].');
        fraction = position.slice();
      } else {
        const voxel = position.map((value, index) => {
          if (!Number.isInteger(value)) throw new Error(`Voxel coordinate ${index} must be an integer.`);
          if (value < 0 || value >= dims[index]) {
            throw new Error(`Voxel coordinate ${index}=${value} is outside [0, ${dims[index] - 1}].`);
          }
          return value;
        });
        fraction = Array.from(nv.vox2frac(voxel), Number).slice(0, 3);
      }
      if (fraction.length !== 3 || fraction.some(value => !Number.isFinite(value))) {
        throw new Error('NiiVue could not convert the requested crosshair coordinate.');
      }
      nv.scene.crosshairPos = fraction;
      notifyLocation(nv);
      return {
        fraction: fraction.map(value => Number(value.toFixed(6))),
        voxel: Array.from(nv.frac2vox(fraction), value => Math.round(Number(value))).slice(0, 3),
        world_mm: Array.from(nv.frac2mm(fraction), value => Number(Number(value).toFixed(4))).slice(0, 3),
      };
    }

    function getState() {
      const fraction = Array.from(nv.scene.crosshairPos.slice(0, 3), Number);
      const mode = Object.keys(SLICE).find(key => SLICE[key] === nv.opts.sliceType) || 'unknown';
      return {
        viewer: 'niivue',
        mode,
        render: {
          azimuth_deg: Number(nv.scene.renderAzimuth),
          elevation_deg: Number(nv.scene.renderElevation),
        },
        crosshair: {
          fraction,
          voxel: Array.from(nv.frac2vox(fraction), value => Math.round(Number(value))).slice(0, 3),
          world_mm: Array.from(nv.frac2mm(fraction), Number).slice(0, 3),
        },
        zoom: {
          volume_scale: Number(nv.scene.volScaleMultiplier),
          slice_scale: Number(nv.scene.pan2Dxyzmm[3]),
        },
        pan_mm: Array.from(nv.scene.pan2Dxyzmm.slice(0, 3), Number),
        clip_plane_depth_azimuth_elevation: Array.from(nv.scene.clipPlaneDepthAziElev, Number),
        layers: nv.volumes.map((volume, index) => ({
          index,
          name: String(volume.name || volume.id || `layer-${index}`),
          opacity: Number(volume.opacity),
          visible: Number(volume.opacity) > 0,
          cal_min: Number(volume.cal_min),
          cal_max: Number(volume.cal_max),
          dimensions: dimensions(volume),
        })),
      };
    }

    async function callTool(name, args) {
      if (name === 'viewer_rotate') {
        if (args.d_roll_deg !== 0) throw new Error('NiiVue 0.45 does not expose camera roll; d_roll_deg must be 0.');
        const previousMode = Object.keys(SLICE).find(key => SLICE[key] === nv.opts.sliceType) || 'unknown';
        if (nv.opts.sliceType !== SLICE.render) {
          nv.setSliceType(SLICE.render);
          syncViewButtons('render');
        }
        const azimuth = Number(nv.scene.renderAzimuth) + args.d_azimuth_deg;
        const elevation = clamp(Number(nv.scene.renderElevation) + args.d_elevation_deg, -90, 90);
        nv.setRenderAzimuthElevation(azimuth, elevation);
        return {
          previous_mode: previousMode,
          current_mode: 'render',
          azimuth_deg: azimuth,
          elevation_deg: elevation,
          incremental: true,
        };
      }
      if (name === 'viewer_slice') {
        const hasIndex = Object.prototype.hasOwnProperty.call(args, 'index');
        const hasFraction = Object.prototype.hasOwnProperty.call(args, 'fraction');
        if (hasIndex === hasFraction) throw new Error('Provide exactly one of index or fraction.');
        const dims = dimensions(volumeAt(nv, 0));
        const component = AXIS_COMPONENT[args.axis];
        const index = hasIndex ? args.index : Math.round(args.fraction * (dims[component] - 1));
        if (index < 0 || index >= dims[component]) {
          throw new Error(`${args.axis} index ${index} is outside [0, ${dims[component] - 1}].`);
        }
        const voxel = Array.from(nv.frac2vox(nv.scene.crosshairPos), value => Math.round(Number(value))).slice(0, 3);
        voxel[component] = index;
        nv.setSliceType(SLICE[args.axis]);
        syncViewButtons(args.axis);
        const location = setCrosshair(voxel, 'voxel');
        return { axis: args.axis, index, ...location };
      }
      if (name === 'viewer_window_level') {
        const volume = volumeAt(nv, args.layer);
        volume.cal_min = args.center - args.width / 2;
        volume.cal_max = args.center + args.width / 2;
        if (typeof nv.onIntensityChange === 'function') nv.onIntensityChange(volume);
        nv.updateGLVolume();
        return { layer: args.layer, center: args.center, width: args.width, min: volume.cal_min, max: volume.cal_max };
      }
      if (name === 'viewer_bbox') {
        const volume = volumeAt(nv, args.layer);
        if (!volume.img || !ArrayBuffer.isView(volume.img)) throw new Error(`Overlay layer ${args.layer} has no typed voxel buffer.`);
        const dims = dimensions(volume);
        const voxelCount = dims[0] * dims[1] * dims[2];
        const voxels = typeof volume.img2RAS === 'function' ? volume.img2RAS() : volume.img;
        if (!voxels || voxels.length < voxelCount) throw new Error(`Overlay layer ${args.layer} voxel buffer is shorter than its dimensions.`);
        const min = dims.slice();
        const max = [-1, -1, -1];
        let count = 0;
        for (let z = 0; z < dims[2]; z += 1) {
          const zOffset = z * dims[0] * dims[1];
          for (let y = 0; y < dims[1]; y += 1) {
            const rowOffset = zOffset + y * dims[0];
            for (let x = 0; x < dims[0]; x += 1) {
              const value = Number(voxels[rowOffset + x]);
              if (!Number.isFinite(value) || value === 0) continue;
              count += 1;
              if (x < min[0]) min[0] = x;
              if (y < min[1]) min[1] = y;
              if (z < min[2]) min[2] = z;
              if (x > max[0]) max[0] = x;
              if (y > max[1]) max[1] = y;
              if (z > max[2]) max[2] = z;
            }
          }
        }
        if (!count) throw new Error(`Overlay layer ${args.layer} contains no non-zero voxels.`);
        const extent = max.map((value, index) => value - min[index] + 1);
        const centerVoxel = min.map((value, index) => Math.round((value + max[index]) / 2));
        let location = null;
        if (args.center) location = setCrosshair(centerVoxel, 'voxel');
        return { layer: args.layer, min_inclusive: min, max_inclusive: max, extent, nonzero_voxel_count: count, center_voxel: centerVoxel, centered: args.center, location };
      }
      if (name === 'viewer_set_view') {
        nv.setSliceType(SLICE[args.mode]);
        syncViewButtons(args.mode);
        return { mode: args.mode };
      }
      if (name === 'viewer_crosshair') return setCrosshair(args.position, args.space);
      if (name === 'viewer_zoom') {
        nv.scene.volScaleMultiplier = clamp(Number(nv.scene.volScaleMultiplier) * args.factor, 0.01, 1000);
        const pan = Array.from(nv.scene.pan2Dxyzmm, Number);
        pan[3] = clamp(pan[3] * args.factor, 0.01, 1000);
        nv.scene.pan2Dxyzmm = pan;
        nv.drawScene();
        return { factor: args.factor, volume_scale: nv.scene.volScaleMultiplier, slice_scale: pan[3] };
      }
      if (name === 'viewer_pan') {
        const pan = Array.from(nv.scene.pan2Dxyzmm, Number);
        for (let index = 0; index < 3; index += 1) pan[index] += args.delta_mm[index];
        nv.setPan2Dxyzmm(pan);
        return { pan_mm: pan.slice(0, 3), incremental: true };
      }
      if (name === 'viewer_visibility') {
        if (!Object.prototype.hasOwnProperty.call(args, 'visible')
            && !Object.prototype.hasOwnProperty.call(args, 'opacity')) {
          throw new Error('Provide visible, opacity, or both.');
        }
        const volume = volumeAt(nv, args.layer);
        let opacity = Number(volume.opacity);
        if (Object.prototype.hasOwnProperty.call(args, 'opacity')) opacity = args.opacity;
        if (args.visible === false) {
          if (opacity > 0) lastVisibleOpacity[args.layer] = opacity;
          opacity = 0;
        } else if (args.visible === true && opacity === 0) {
          opacity = lastVisibleOpacity[args.layer] || 1;
        }
        if (opacity > 0) lastVisibleOpacity[args.layer] = opacity;
        nv.setOpacity(args.layer, opacity);
        return { layer: args.layer, opacity, visible: opacity > 0 };
      }
      if (name === 'viewer_clip_plane') {
        const clip = args.enabled ? [args.depth, args.azimuth_deg, args.elevation_deg] : [2, 0, 0];
        nv.setClipPlane(clip);
        return { enabled: args.enabled, depth_azimuth_elevation: clip };
      }
      if (name === 'viewer_reset') {
        nv.opts.sliceType = initial.sliceType;
        nv.scene.crosshairPos = initial.crosshair.slice();
        nv.scene.pan2Dxyzmm = initial.pan.slice();
        nv.scene.volScaleMultiplier = initial.scale;
        nv.scene.renderAzimuth = initial.azimuth;
        nv.scene.renderElevation = initial.elevation;
        nv.scene.clipPlaneDepthAziElev = initial.clip.slice();
        initial.layers.forEach((saved, index) => {
          if (!nv.volumes[index]) return;
          nv.volumes[index].opacity = saved.opacity;
          nv.volumes[index].cal_min = saved.calMin;
          nv.volumes[index].cal_max = saved.calMax;
          if (saved.opacity > 0) lastVisibleOpacity[index] = saved.opacity;
        });
        nv.setClipPlane(initial.clip.slice());
        nv.updateGLVolume();
        notifyLocation(nv);
        const mode = Object.keys(SLICE).find(key => SLICE[key] === initial.sliceType) || 'multiplanar';
        syncViewButtons(mode);
        return { reset: true, state: getState() };
      }
      if (name === 'viewer_get_state') return getState();
      throw new Error(`Unsupported NiiVue MCP tool: ${name}.`);
    }

    return Object.freeze({ tools, callTool });
  }

  Object.defineProperty(window, '__ScienceVisionCreateNiiVueMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
}());
