/* Host-served MCP adapter for the mechanical STEP/IGES OCCT viewer. */
(function installScienceVisionOcctMCPAdapter(global) {
  'use strict';

  function createProvider(api) {
    const registry = global.__ScienceVisionOcctToolRegistry;
    if (!registry || !Array.isArray(registry.tools)) throw new Error('OCCT tool registry is unavailable.');
    if (!api || typeof api.setView !== 'function' || typeof api.orbit !== 'function'
      || typeof api.setZoom !== 'function' || typeof api.fitView !== 'function') {
      throw new Error('OCCT viewer API is unavailable.');
    }
    // Reference-image tools stay registered (legacy drafts replay them) but are
    // `internal` in the registry, so the picker never offers them.
    const tools = registry.tools;
    function requirePoint(value, field) {
      if (!Array.isArray(value) || value.length !== 3
          || !value.every(item => Number.isFinite(Number(item)))) {
        throw new Error(`${field} must be a point [x, y, z].`);
      }
      return value.map(Number);
    }
    // Model units, not metres: STEP carries its own unit and the kernel is not
    // consulted here, so the wording stays unit-less like the other mesh measures.
    function formatSurfaceDistance(measured) {
      const record = measured || {};
      const value = Number(record.value);
      if (!Number.isFinite(value)) return '距离：—';
      const delta = Array.isArray(record.delta) && record.delta.length === 3
        ? record.delta.map(Number) : null;
      if (delta && delta.every(Number.isFinite)) {
        const axis = (label, number) => `${label} ${Math.abs(number).toFixed(3)}`;
        return `距离：${value.toFixed(3)}（${axis('Δx', delta[0])}｜${axis('Δy', delta[1])}｜${axis('Δz', delta[2])}）`;
      }
      return `距离：${value.toFixed(3)}`;
    }
    // One line for the panel's feedback strip after a selection-based measure.
    // Without it the panel said only "succeeded" and the number lived solely in
    // the 3D label, which a zoomed-out expert cannot read. Model units carry no
    // unit word (STEP declares its own); angles are degrees.
    function formatSelectionMeasure(name, measurement) {
      const record = measurement || {};
      const value = Number(record.value);
      if (name === 'sv3d_measure_bounds') {
        const size = Array.isArray(record.size) && record.size.length === 3
          ? record.size.map(Number) : null;
        return size && size.every(Number.isFinite)
          ? `包围盒：${size.map(item => item.toFixed(3)).join(' × ')}`
          : '包围盒：—';
      }
      if (name === 'sv3d_measure_angle') {
        return Number.isFinite(value) ? `夹角：${value.toFixed(2)}°` : '夹角：—';
      }
      if (name === 'sv3d_measure_distance_exact') {
        return Number.isFinite(value) ? `最小间隙：${value.toFixed(3)}（OCCT 内核精确）` : '最小间隙：—';
      }
      if (name === 'sv3d_fit_plane') {
        const departure = Number(record.max_departure);
        const count = Number(record.point_count);
        return Number.isFinite(departure)
          ? `拟合平面：最大偏离 ${departure.toFixed(4)}（${Number.isFinite(count) ? count : '?'} 点）`
          : '拟合平面：—';
      }
      return Number.isFinite(value) ? `中心距：${value.toFixed(3)}` : '中心距：—';
    }
    // The 3D points a selection-based measure resolved its selection indices to,
    // copied onto the result as `points` so the recorded step reads geometrically
    // on its own (an agent training on the trajectory should not have to chase
    // selection_a back to an earlier step). Distance / angle / plane carry their
    // witness points; bounds carries the box corners. replay_input stays the
    // indices, so replay is unchanged.
    function resolvedPoints(measurement) {
      const record = measurement || {};
      if (Array.isArray(record.witness_points)) return record.witness_points;
      if (Array.isArray(record.min) && Array.isArray(record.max)) return [record.min, record.max];
      return null;
    }
    function measured(name, measurement) {
      const points = resolvedPoints(measurement);
      return {
        measurement,
        result: {
          expert_summary: formatSelectionMeasure(name, measurement),
          ...measurement,
          ...(points ? {points} : {}),
        },
        state: state(),
      };
    }
    function state() {
      return {
        viewer: 'occt', view: api.currentView,
        camera: typeof api.cameraState === 'function' ? api.cameraState() : api.cameraState,
        zoom: typeof api.zoomMultiple === 'function' ? api.zoomMultiple() : 1,
        opacity: api.opacity,
        render_mode: api.renderMode,
        parts: Array.isArray(api.parts) ? api.parts : [],
        reference: typeof api.referenceState === 'function' ? api.referenceState() : null,
        inspection: typeof api.inspectionState === 'function'
          ? api.inspectionState() : {selections: [], measurements: []},
        // Same block gltf / architecture report; the panel's cutaway slider reads
        // its bounds from here. Drafts recorded before it exist: sameViewerState
        // drops an uncut block, so they keep replaying.
        cutaway: typeof api.cutawayState === 'function' ? api.cutawayState() : null,
        file: api.file || null,
      };
    }
    async function callTool(name, args = {}) {
      if (name === 'viewer_get_state') return state();
      if (name === 'viewer_reset') { api.reset(); return {reset: true, state: state()}; }
      if (name === 'sv3d_set_view') { api.setView(args.view); return {view: args.view, state: state()}; }
      if (name === 'sv3d_orbit') { api.orbit(args.azimuth_deg, args.elevation_deg); return state(); }
      if (name === 'viewer_zoom') { api.setZoom(args.scale); return state(); }
      if (name === 'sv3d_fit_view') { api.fitView(); return state(); }
      if (name === 'sv3d_set_part_visibility') {
        if (args.all === true) api.setAllPartsVisible(args.visible);
        else api.setPartVisibility(args.index, args.visible);
        return state();
      }
      if (name === 'sv3d_set_model_opacity') {
        api.setModelOpacity(args.opacity); return state();
      }
      if (name === 'sv3d_set_render_mode') {
        api.setRenderMode(args.mode); return state();
      }
      if (name === 'sv3d_set_cutaway') {
        api.setCutaway(args.value_m, args.axis);
        return {axis: args.axis || null, value_m: args.value_m, state: state()};
      }
      if (name === 'sv3d_set_reference_view') {
        await api.setReferenceView(args.reference_index); return state();
      }
      if (name === 'sv3d_set_reference_zoom') {
        api.setReferenceZoom(args.scale); return state();
      }
      if (name === 'sv3d_set_reference_pan') {
        api.setReferencePan(args.pan_x, args.pan_y); return state();
      }
      if (name === 'sv3d_reset_reference_view') {
        api.resetReferenceView(); return state();
      }
      if (name === 'sv3d_select_part') {
        // `selected` mirrors sv3d_set_part_visibility's `visible`: the direction of
        // the click lives in the recorded arguments instead of being inferred from
        // live state, so replay and audit read unambiguously. Omitted means true,
        // which is how drafts recorded as {part_index} keep replaying as selects.
        if (args.selected === false) {
          return {...api.deselectPart(args.part_index), state: state()};
        }
        const selection = api.selectPart(args.part_index); return {selection, state: state()};
      }
      if (name === 'sv3d_pick_entity') {
        const selection = api.pickEntity(args.screen_x, args.screen_y);
        return {selection, state: state()};
      }
      if (name === 'sv3d_clear_selection') {
        return {...api.clearSelection(), state: state()};
      }
      if (name === 'viewer_pick_point') {
        const hasScreen = args && (args.screen_x !== undefined || args.screen_y !== undefined);
        if (hasScreen) {
          const x = Number(args.screen_x);
          const y = Number(args.screen_y);
          if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('screen_x and screen_y must be numbers.');
          if (x < 0 || x > 1 || y < 0 || y > 1) throw new Error('screen_x and screen_y must be between 0 and 1.');
          if (typeof api.pickPointAt !== 'function') {
            throw new Error('This viewer build cannot pick by viewport position.');
          }
          return api.pickPointAt(x, y);
        }
        if (typeof api.pickPointOnce !== 'function') {
          throw new Error('OCCT viewer does not support point picking.');
        }
        return api.pickPointOnce();
      }
      if (name === 'sv3d_measure') {
        const start = requirePoint(args.start, 'start');
        const end = requirePoint(args.end, 'end');
        const measurement = api.measure(start, end);
        return {
          replay_input: {start, end},
          measurement,
          result: {expert_summary: formatSurfaceDistance(measurement), ...measurement},
          state: state(),
        };
      }
      if (name === 'sv3d_measure_distance') {
        const measurement = api.measureDistance(args.selection_a, args.selection_b);
        return measured(name, measurement);
      }
      if (name === 'sv3d_measure_distance_exact') {
        const partA = api.selectionPartIndex(args.selection_a);
        const partB = api.selectionPartIndex(args.selection_b);
        const resp = await fetch(api.apiBase() + '/api/occt/measure-distance-exact', {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRF-Token': api.csrfToken()},
          body: JSON.stringify({file: api.modelFile(), part_a: partA, part_b: partB}),
        });
        const out = await resp.json();
        if (!out.available) {
          throw new Error(out.reason || '精确几何服务暂不可用');
        }
        api.drawExactDistance(out.receipt);
        return measured(name, out.receipt);
      }
      if (name === 'sv3d_measure_angle') {
        const measurement = api.measureAngle(
          args.selection_a, args.selection_b, args.selection_c,
        );
        return measured(name, measurement);
      }
      if (name === 'sv3d_fit_plane') {
        const measurement = api.fitPlane(args.selections);
        return measured(name, measurement);
      }
      if (name === 'sv3d_measure_bounds') {
        const measurement = api.measureBounds(args.part_index);
        return measured(name, measurement);
      }
      if (name === 'sv3d_clear_measurements') {
        return {...api.clearMeasurements(), state: state()};
      }
      if (name === 'sv3d_render') return api.render();
      throw new Error(`Unsupported OCCT MCP tool: ${name}.`);
    }
    return Object.freeze({tools, callTool});
  }
  Object.defineProperty(global, '__ScienceVisionCreateOcctMCPProvider', {
    configurable: false, enumerable: false, writable: false, value: createProvider,
  });
}(window));
