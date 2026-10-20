(function installScienceVisionArchitectureInspectionMCPAdapter(global) {
  'use strict';

  function createProvider(api) {
    const registry = global.__ScienceVisionArchitectureInspectionToolRegistry;
    if (!registry || !Array.isArray(registry.tools)) {
      throw new Error('Architecture inspection tool registry is unavailable.');
    }
    if (!api || typeof api.setView !== 'function') {
      throw new Error('Architecture inspection API is unavailable.');
    }
    const tools = registry.tools;

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


    function getState() {
      const camera = typeof api.cameraState === 'function' ? api.cameraState() : api.cameraState;
      return {
        viewer: 'architecture_inspection',
        view: api.currentView,
        camera: camera || null,
        zoom: typeof api.zoomMultiple === 'function'
          ? api.zoomMultiple()
          : (camera && typeof camera.zoom === 'number' ? camera.zoom : null),
        layers: typeof api.layers === 'function' ? api.layers() : (api.layers || []),
        cutaway: typeof api.cutawayState === 'function' ? api.cutawayState() : null,
        amplification: api.amplification,
        // The slider's ends. Without them the panel can only offer a value it has no
        // range for, which is why 设置变形显示倍率 rendered nothing at all.
        amplification_range: typeof api.amplificationRange === 'function'
          ? api.amplificationRange() : null,
        annotation_count: Array.isArray(api.annotations) ? api.annotations.length : 0,
        operation_count: Array.isArray(api.operationTrace) ? api.operationTrace.length : 0,
        scenario_kind: api.scenarioKind || null,
      };
    }

    async function callTool(name, args) {
      if (name === 'viewer_get_state' || name === 'sv3d_open_scene') return getState();
      if (name === 'viewer_reset') {
        api.reset();
        return { reset: true, state: getState() };
      }
      if (name === 'sv3d_orbit') {
        // Absolute, matching molstar / bim_coordination. It was a delta, which the
        // panel had no branch for -- so 环绕旋转相机 rendered nothing at all -- and
        // which a slider cannot preview, because re-applying it orbits twice.
        api.orbitAbsolute(args.azimuth_deg, args.elevation_deg);
        return { azimuth_deg: args.azimuth_deg, elevation_deg: args.elevation_deg, state: getState() };
      }
      if (name === 'sv3d_set_view') {
        api.setView(args.view);
        return { view: args.view, state: getState() };
      }
      if (name === 'sv3d_set_orthographic_view') {
        if (typeof api.setOrthographicView !== 'function') {
          throw new Error('Architecture inspection viewer does not support orthographic views.');
        }
        api.setOrthographicView(args.view);
        return { view: args.view, projection: 'orthographic', state: getState() };
      }
      if (name === 'viewer_zoom') {
        if (typeof api.setZoom !== 'function') {
          throw new Error('Architecture inspection viewer does not support zoom.');
        }
        const zoom = requireFiniteNumber(args && args.zoom, 'zoom');
        api.setZoom(zoom);
        return { zoom, state: getState() };
      }
      if (name === 'viewer_pan') {
        if (typeof api.panAbsolute !== 'function') {
          throw new Error('Architecture inspection viewer does not support pan.');
        }
        const pan_right = requireFiniteNumber(args && args.pan_right, 'pan_right');
        const pan_up = requireFiniteNumber(args && args.pan_up, 'pan_up');
        api.panAbsolute(pan_right, pan_up);
        return { pan_right, pan_up, state: getState() };
      }
      if (name === 'sv3d_toggle_layer') {
        api.setLayerVisible(args.layer, args.visible);
        return getState();
      }
      if (name === 'sv3d_set_cutaway') {
        api.setCutaway(args.value_m, args.axis);
        return { axis: args.axis || 'z', value_m: args.value_m, state: getState() };
      }
      if (name === 'sv3d_set_deformation_amplification') {
        api.setAmplification(args.amplification);
        return { amplification: args.amplification, state: getState() };
      }
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
          throw new Error('Architecture inspection viewer does not support point picking.');
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
          throw new Error('Architecture inspection viewer does not support vertex picking.');
        }
        return api.pickVertexOnce();
      }
      if (name === 'sv3d_measure') {
        const start = requirePoint(args && args.start, 'start');
        const end = requirePoint(args && args.end, 'end');
        const measured = api.measure(start, end);
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
          throw new Error('Architecture inspection viewer does not support angle measurement.');
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
          throw new Error('Architecture inspection viewer does not support plane fitting.');
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
          throw new Error('Architecture inspection viewer does not support area measurement.');
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
          throw new Error('Architecture inspection viewer does not support clearing measurements.');
        }
        return {...api.clearMeasurements(), state: getState()};
      }
      if (name === 'sv3d_render') {
        return api.render();
      }
      if (name === 'sv3d_annotate') {
        return api.annotate(args.point, args.layer);
      }
      if (name === 'sv3d_save_operation_trace') {
        return api.saveTrace();
      }
      throw new Error(`Unsupported architecture inspection MCP tool: ${name}.`);
    }

    return Object.freeze({ tools, callTool });
  }

  Object.defineProperty(window, '__ScienceVisionCreateArchitectureInspectionMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
}(window));
