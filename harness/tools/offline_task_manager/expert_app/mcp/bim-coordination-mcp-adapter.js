(function installScienceVisionBimCoordinationMCPAdapter(global) {
  'use strict';

  function requireFiniteNumber(value, name) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`${name} must be a finite number.`);
    return number;
  }

  function requirePoint(value, name) {
    if (!Array.isArray(value) || value.length !== 3) {
      throw new Error(`${name} must be a 3-number world point.`);
    }
    return value.map((item, index) => requireFiniteNumber(item, `${name}[${index}]`));
  }

  function requirePoints(value, name) {
    if (!Array.isArray(value) || value.length < 3) {
      throw new Error(`${name} must list at least 3 world points.`);
    }
    return value.map((item, index) => requirePoint(item, `${name}[${index}]`));
  }

  function formatPlaneSummary(fit) {
    const count = Number(fit && fit.point_count);
    const max = Number(fit && fit.max_departure_m) * 1000;
    const rms = Number(fit && fit.rms_departure_m) * 1000;
    if (!Number.isFinite(max)) return '平面拟合：—';
    return `平面拟合：${count} 点，最大偏离 ${max.toFixed(1)} mm（RMS ${rms.toFixed(1)} mm）`;
  }

  function formatDistanceSummary(measured) {
    const record = measured || {};
    const number = Number(record.distance_m);
    if (!Number.isFinite(number)) return '距离：—';
    const vertical = Number(record.vertical_m);
    const horizontal = Number(record.horizontal_m);
    // Bearing / grid deviation ride along when the segment has a plan length:
    // "how far is this edge rotated from the grid" read straight off a measure.
    const bearing = Number(record.bearing_deg);
    const deviation = Number(record.grid_deviation_deg);
    const direction = Number.isFinite(bearing) && Number.isFinite(deviation)
      ? `｜走向 ${bearing.toFixed(1)}°｜偏网格 ${deviation >= 0 ? '+' : ''}${deviation.toFixed(1)}°`
      : '';
    if (Number.isFinite(vertical) && Number.isFinite(horizontal)) {
      return `距离：${number.toFixed(3)} m（净高 ${vertical.toFixed(3)} m｜水平 ${horizontal.toFixed(3)} m${direction}）`;
    }
    return `距离：${number.toFixed(3)} m`;
  }

  function formatAngleSummary(measured) {
    const record = measured || {};
    const angle = Number(record.angle_deg);
    if (!Number.isFinite(angle)) return '夹角：—';
    const plan = Number(record.plan_angle_deg);
    return Number.isFinite(plan)
      ? `夹角：${angle.toFixed(2)}°（水平投影 ${plan.toFixed(2)}°）`
      : `夹角：${angle.toFixed(2)}°`;
  }

  const AREA_PLANE_NAMES = {fit: '拟合面', xy: 'XY 投影', xz: 'XZ 投影', yz: 'YZ 投影'};
  const AREA_PLANE_KEYS = {fit: 'area_fit_m2', xy: 'area_xy_m2', xz: 'area_xz_m2', yz: 'area_yz_m2'};

  function requirePlane(value) {
    if (value === undefined || value === null) return 'fit';
    if (!Object.prototype.hasOwnProperty.call(AREA_PLANE_KEYS, value)) {
      throw new Error('plane must be one of fit, xy, xz, yz.');
    }
    return value;
  }

  function requireReplaces(value) {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== 'object' || Array.isArray(value)) throw new Error('replaces must be an object.');
    const measurementIndex = value.measurement_index;
    const pointIndex = value.point_index;
    if (!Number.isInteger(measurementIndex) || measurementIndex < 0) {
      throw new Error('replaces.measurement_index must be a non-negative integer.');
    }
    if (!Number.isInteger(pointIndex) || pointIndex < 0) {
      throw new Error('replaces.point_index must be a non-negative integer.');
    }
    return {measurement_index: measurementIndex, point_index: pointIndex};
  }

  // Headline the plane the expert chose; the other three areas still ride along in
  // the record for replay and cross-checking. Same wording across all viewers.
  function formatAreaSummary(measured) {
    const record = measured || {};
    const plane = Object.prototype.hasOwnProperty.call(AREA_PLANE_KEYS, record.plane) ? record.plane : 'fit';
    const area = Number(record[AREA_PLANE_KEYS[plane]]);
    if (!Number.isFinite(area)) return '面积：—';
    const perimeter = Number(record.perimeter_m);
    const departure = Number(record.planar_departure_max_m);
    // Over 2 cm off the fit plane the ring is not one face: flag it in the readout
  // itself, so the expert (and the reviewer reading the record) cannot miss it.
  const warning = departure > 0.02 ? '｜⚠ 不共面' : '';
  return `面积：${area.toFixed(3)} m²（${AREA_PLANE_NAMES[plane]}｜周长 ${perimeter.toFixed(3)} m｜偏离拟合面 ${departure.toFixed(3)} m${warning}）`;
  }


  function requireFitViewInput(args) {
    if (!args || typeof args !== 'object' || typeof args.scope !== 'string' || !args.scope) {
      throw new Error('scope is required.');
    }
    if (!['all', 'visible', 'candidate'].includes(args.scope)) {
      throw new Error('scope must be all, visible, or candidate.');
    }
    if (args.scope !== 'candidate') return {scope: args.scope};
    if (!Object.prototype.hasOwnProperty.call(args, 'index') || args.index === undefined) {
      throw new Error('index is required when scope is candidate.');
    }
    if (!Number.isInteger(args.index) || args.index < 0) {
      throw new Error('index must be a non-negative integer.');
    }
    return {scope: args.scope, index: args.index};
  }

  const cameraTools = Object.freeze([
    {
      name: 'sv3d_orbit',
      replayInput(args) {
        return {
          azimuth_deg: requireFiniteNumber(args && args.azimuth_deg, 'azimuth_deg'),
          elevation_deg: requireFiniteNumber(args && args.elevation_deg, 'elevation_deg'),
        };
      },
      call(api, input) { api.orbitAbsolute(input.azimuth_deg, input.elevation_deg); },
    },
    {
      name: 'viewer_zoom',
      replayInput(args) { return {zoom: requireFiniteNumber(args && args.zoom, 'zoom')}; },
      call(api, input) { api.setZoom(input.zoom); },
    },
    {
      name: 'viewer_pan',
      // Absolute look-at offset (pan_right/pan_up), same shape and meaning as
      // Mol*. It was a relative delta, which replay had to re-apply in order and
      // a slider could not preview -- dragging twice panned twice. Absolute makes
      // one drag idempotent, so the panel's schema-matched preview branch drives
      // it live and records a single clean call.
      replayInput(args) {
        return {
          pan_right: requireFiniteNumber(args && args.pan_right, 'pan_right'),
          pan_up: requireFiniteNumber(args && args.pan_up, 'pan_up'),
        };
      },
      call(api, input) { api.panAbsolute(input.pan_right, input.pan_up); },
    },
    {
      name: 'sv3d_fit_view',
      replayInput: requireFitViewInput,
      call(api, input) { api.fitView(input.scope, input.index); },
    },
  ]);

  function createProvider(api) {
    const registry = global.__ScienceVisionBimToolRegistry;
    if (!registry || !Array.isArray(registry.tools)) {
      throw new Error('BIM coordination tool registry is unavailable.');
    }
    if (!api || typeof api.setView !== 'function' || typeof api.orbitAbsolute !== 'function'
      || typeof api.setZoom !== 'function' || typeof api.panAbsolute !== 'function'
      || typeof api.fitView !== 'function') {
      throw new Error('BIM coordination API is unavailable.');
    }
    const tools = registry.tools;

    function getState() {
      const models = Array.isArray(api.models) ? api.models : [];
      const camera = typeof api.cameraState === 'function' ? api.cameraState() : api.cameraState;
      return {
        viewer: 'bim_coordination',
        view: api.currentView,
        camera: camera || null,
        // The absolute zoom, as a multiple of the fitted view. Reported at the
        // top level rather than inside camera because camera.zoom is the raw
        // three.js value sv3d_set_camera replays, which for a perspective view
        // is a constant 1 and says nothing about how close we are.
        zoom: typeof api.zoomMultiple === 'function' ? api.zoomMultiple() : 1,
        // Report opacity so the opacity slider re-derives the true value after a
        // mode switch instead of defaulting to 100%.
        opacity: typeof api.modelOpacity === 'number' ? api.modelOpacity : 1,
        candidates: models.map((model, index) => ({
          index,
          label: model.label,
          visible: Boolean(model.root && model.root.visible),
        })),
        plan_underlay: { visible: Boolean(api.planVisible) },
        // The bundled viewer exposes no getters for its guide checkboxes; the host
        // bootstrap adds groundVisible / envelopesVisible so state can read them back.
        ground: { visible: Boolean(api.groundVisible) },
        envelopes: { visible: Boolean(api.envelopesVisible) },
        annotation_count: Array.isArray(api.annotations) ? api.annotations.length : 0,
        measurement_count: Array.isArray(api.measurements) ? api.measurements.length : 0,
      };
    }

    async function callTool(name, args) {
      const cameraTool = cameraTools.find(tool => tool.name === name);
      if (cameraTool) {
        const replayInput = cameraTool.replayInput(args);
        cameraTool.call(api, replayInput);
        return {replay_input: replayInput, state: getState()};
      }
      if (name === 'viewer_get_state' || name === 'sv3d_open_multi_scene') return getState();
      // Resolves on the expert's next click on visible geometry. Internal, and the
      // gesture half of 放置标注点: the panel arms it, feeds the world point into
      // sv3d_annotate, and the recorded step carries the literal point so replay
      // never needs a human. The mcp-only lock cooperates -- blockDirectInput lets
      // a canvas click through while a pick is live.
      if (name === 'viewer_pick_point') {
        const hasScreen = args && (args.screen_x !== undefined || args.screen_y !== undefined);
        if (hasScreen) {
          const x = requireFiniteNumber(args.screen_x, 'screen_x');
          const y = requireFiniteNumber(args.screen_y, 'screen_y');
          if (x < 0 || x > 1 || y < 0 || y > 1) throw new Error('screen_x and screen_y must be between 0 and 1.');
          if (typeof api.pickPointAt !== 'function') {
            throw new Error('This viewer build cannot pick by viewport position.');
          }
          return api.pickPointAt(x, y);
        }
        if (typeof api.pickPointOnce !== 'function') {
          throw new Error('BIM coordination viewer does not support point picking.');
        }
        return api.pickPointOnce();
      }
      if (name === 'viewer_pick_vertex') {
        const hasScreen = args && (args.screen_x !== undefined || args.screen_y !== undefined);
        if (hasScreen) {
          const x = requireFiniteNumber(args.screen_x, 'screen_x');
          const y = requireFiniteNumber(args.screen_y, 'screen_y');
          if (x < 0 || x > 1 || y < 0 || y > 1) throw new Error('screen_x and screen_y must be between 0 and 1.');
          if (typeof api.pickVertexAt !== 'function') {
            throw new Error('This viewer build cannot pick a vertex by viewport position.');
          }
          return api.pickVertexAt(x, y);
        }
        if (typeof api.pickVertexOnce !== 'function') {
          throw new Error('BIM coordination viewer does not support vertex picking.');
        }
        return api.pickVertexOnce();
      }
      if (name === 'viewer_reset') {
        api.reset();
        return { reset: true, state: getState() };
      }
      if (name === 'sv3d_set_camera') {
        if (typeof api.setCamera !== 'function') {
          throw new Error('BIM coordination viewer does not support absolute camera restore.');
        }
        api.setCamera(args && args.camera);
        return { camera: args && args.camera, state: getState() };
      }
      if (name === 'sv3d_set_view') {
        api.setView(args.view);
        return { view: args.view, state: getState() };
      }
      if (name === 'sv3d_set_model_visibility') {
        if (args.all === true || !Object.prototype.hasOwnProperty.call(args, 'index')) {
          api.showAll(false);
        } else {
          api.showSolo(args.index);
        }
        return getState();
      }
      if (name === 'sv3d_set_model_opacity') {
        api.applyModelOpacity(args.opacity);
        // Report replay_input like every other handler, so recording does not
        // depend on the receipt.arguments fallback in trajectory-model.js.
        return {
          replay_input: { opacity: args.opacity },
          opacity: args.opacity,
          state: getState(),
        };
      }
      if (name === 'sv3d_register_overlay') {
        api.setPlanVisible(args.visible);
        return { visible: args.visible, state: getState() };
      }
      if (name === 'sv3d_toggle_ground') {
        if (typeof (args && args.visible) !== 'boolean') throw new Error('visible is required.');
        api.setGroundVisible(args.visible);
        return { visible: args.visible, state: getState() };
      }
      if (name === 'sv3d_toggle_envelopes') {
        if (typeof (args && args.visible) !== 'boolean') throw new Error('visible is required.');
        api.setEnvelopesVisible(args.visible);
        return { visible: args.visible, state: getState() };
      }
      if (name === 'sv3d_measure_distance') {
        if (typeof api.measureDistance !== 'function') {
          throw new Error('BIM coordination viewer does not support distance measurement.');
        }
        const start = requirePoint(args && args.start, 'start');
        const end = requirePoint(args && args.end, 'end');
        const measured = api.measureDistance(start, end);
        return {
          replay_input: {start, end},
          result: {
            expert_summary: formatDistanceSummary(measured),
            ...measured,
          },
          state: getState(),
        };
      }
      if (name === 'sv3d_measure_angle') {
        if (typeof api.measureAngle !== 'function') {
          throw new Error('BIM coordination viewer does not support angle measurement.');
        }
        const first = requirePoint(args && args.first, 'first');
        const vertex = requirePoint(args && args.vertex, 'vertex');
        const second = requirePoint(args && args.second, 'second');
        const measured = api.measureAngle(first, vertex, second);
        return {
          replay_input: {first, vertex, second},
          result: {expert_summary: formatAngleSummary(measured), ...measured},
          state: getState(),
        };
      }
      if (name === 'sv3d_fit_plane') {
        if (typeof api.fitPlane !== 'function') {
          throw new Error('BIM coordination viewer does not support plane fitting.');
        }
        const points = requirePoints(args && args.points, 'points');
        const fitted = api.fitPlane(points);
        return {
          replay_input: {points},
          result: {
            expert_summary: formatPlaneSummary(fitted),
            ...fitted,
          },
          state: getState(),
        };
      }
      if (name === 'sv3d_measure_area') {
        if (typeof api.measureArea !== 'function') {
          throw new Error('BIM coordination viewer does not support area measurement.');
        }
        const points = requirePoints(args && args.points, 'points');
        const plane = requirePlane(args && args.plane);
        const replaces = requireReplaces(args && args.replaces);
        const measured = api.measureArea(points, plane, replaces);
        return {
          replay_input: replaces ? {points, plane, replaces} : {points, plane},
          result: {expert_summary: formatAreaSummary(measured), ...measured},
          state: getState(),
        };
      }
      if (name === 'sv3d_clear_measurements') {
        if (typeof api.clearMeasurements !== 'function') {
          throw new Error('BIM coordination viewer does not support clearing measurements.');
        }
        return {...api.clearMeasurements(), state: getState()};
      }
      if (name === 'sv3d_annotate') {
        const point = args && args.point;
        if (!Array.isArray(point) || point.length !== 3
          || !point.every(value => Number.isFinite(Number(value)))) {
          throw new Error('point must be an array of three finite numbers.');
        }
        const annotation = api.annotate(point.map(Number), args.layer);
        return { annotation, state: getState() };
      }
      throw new Error(`Unsupported BIM coordination MCP tool: ${name}.`);
    }

    return Object.freeze({ tools, callTool });
  }

  Object.defineProperty(global, '__ScienceVisionCreateBimCoordinationMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
}(window));
