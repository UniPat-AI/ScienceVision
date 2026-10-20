(function installScienceVisionMolstarMCPAdapter() {
  'use strict';

  const emptySchema = Object.freeze({ type: 'object', properties: {}, additionalProperties: false });

  function selectorSchema(description) {
    return {
      type: 'object',
      description,
      properties: {
        structure_id: { type: 'string', description: 'Stable ID returned by viewer_get_state.' },
        structure_index: { type: 'integer', minimum: 0 },
        model_index: { type: 'integer', minimum: 0, description: 'Zero-based model index within the structure.' },
        unit_id: { type: 'integer', minimum: 0, description: 'Mol* unit ID, useful when assembly copies are ambiguous.' },
        chain_id: { type: 'string', description: 'auth_asym_id or label_asym_id.' },
        residue_number: { type: 'integer', description: 'auth_seq_id or label_seq_id.' },
        insertion_code: { type: 'string' },
        residue_name: { type: 'string', description: 'auth_comp_id or label_comp_id.' },
        atom_name: { type: 'string', description: 'auth_atom_id or label_atom_id.' },
        altloc: { type: 'string', description: 'Alternate-location ID; use an empty string for the blank conformer.' },
      },
      additionalProperties: false,
    };
  }

  const tools = Object.freeze([
    {
      name: 'viewer_rotate',
      description: 'Increment the molecular camera azimuth, elevation, and roll in degrees around its current target.',
      inputSchema: {
        type: 'object',
        properties: {
          d_azimuth_deg: { type: 'number' },
          d_elevation_deg: { type: 'number' },
          d_roll_deg: { type: 'number', default: 0 },
        },
        required: ['d_azimuth_deg', 'd_elevation_deg'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_zoom',
      description: 'Multiply camera zoom; factor > 1 moves closer and factor < 1 moves farther away.',
      inputSchema: {
        type: 'object',
        properties: { factor: { type: 'number', minimum: 0.05, maximum: 20 } },
        required: ['factor'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_measure_distance',
      description: 'Resolve two unambiguous atom selectors, draw a Mol* distance annotation, and return the distance in angstroms.',
      inputSchema: {
        type: 'object',
        properties: {
          a: selectorSchema('First atom selector; it must resolve to exactly one displayed atom.'),
          b: selectorSchema('Second atom selector; it must resolve to exactly one displayed atom.'),
        },
        required: ['a', 'b'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_switch_structure',
      description: 'Show exactly one loaded structure, hide the others, and focus the selected structure.',
      inputSchema: {
        type: 'object',
        properties: {
          structure_id: { type: 'string' },
          index: { type: 'integer', minimum: 0 },
        },
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_highlight',
      description: 'Highlight a structure, chain, residue, or atom selection, or clear the current highlight.',
      inputSchema: {
        type: 'object',
        properties: {
          selector: selectorSchema('Selection to highlight.'),
          clear: { type: 'boolean', default: false },
        },
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_focus',
      description: 'Focus the Mol* camera on a structure, chain, residue, or atom selection.',
      inputSchema: {
        type: 'object',
        properties: { selector: selectorSchema('Selection to focus.') },
        required: ['selector'],
        additionalProperties: false,
      },
    },
    {
      name: 'viewer_visibility',
      description: 'Show or hide a whole loaded structure. Partial residue/atom visibility is intentionally unsupported.',
      inputSchema: {
        type: 'object',
        properties: {
          selector: selectorSchema('Whole-structure selector (structure_id or structure_index only).'),
          visible: { type: 'boolean' },
        },
        required: ['selector', 'visible'],
        additionalProperties: false,
      },
    },
    { name: 'viewer_reset', description: 'Restore the initial Mol* camera and structure visibility, and clear highlights.', inputSchema: emptySchema },
    { name: 'viewer_get_state', description: 'Return camera state, stable structure IDs, visibility, and measurement count.', inputSchema: emptySchema },
  ]);

  function vecAdd(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
  function vecSub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function vecScale(a, scale) { return [a[0] * scale, a[1] * scale, a[2] * scale]; }
  function vecLength(a) { return Math.hypot(a[0], a[1], a[2]); }
  function vecNormalize(a) {
    const length = vecLength(a);
    if (!Number.isFinite(length) || length < 1e-12) throw new Error('Camera basis is degenerate.');
    return vecScale(a, 1 / length);
  }
  function vecCross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }
  function rotateAroundAxis(vector, axis, degrees) {
    if (degrees === 0) return vector.slice();
    const unit = vecNormalize(axis);
    const radians = degrees * Math.PI / 180;
    const cosine = Math.cos(radians);
    const sine = Math.sin(radians);
    const cross = vecCross(unit, vector);
    const dot = unit[0] * vector[0] + unit[1] * vector[1] + unit[2] * vector[2];
    return [
      vector[0] * cosine + cross[0] * sine + unit[0] * dot * (1 - cosine),
      vector[1] * cosine + cross[1] * sine + unit[1] * dot * (1 - cosine),
      vector[2] * cosine + cross[2] * sine + unit[2] * dot * (1 - cosine),
    ];
  }

  function columnValue(column, index) {
    if (!column || typeof column.value !== 'function') return undefined;
    const value = column.value(index);
    return value == null || value === '.' || value === '?' ? '' : value;
  }

  function createProvider(viewer) {
    const plugin = viewer?.plugin;
    const camera = plugin?.canvas3d?.camera;
    const hierarchy = plugin?.managers?.structure?.hierarchy;
    if (!plugin || !camera || !hierarchy) throw new Error('Mol* plugin camera or structure hierarchy is unavailable.');
    const currentStructures = hierarchy.current.structures;
    if (!Array.isArray(currentStructures) || !currentStructures.length) throw new Error('Mol* loaded no structures.');

    const registry = currentStructures.map((ref, index) => ({
      id: `structure-${index}`,
      index,
      ref,
    }));
    const initialCameraRaw = camera.getSnapshot();
    const initialCamera = {
      ...initialCameraRaw,
      position: Array.from(initialCameraRaw.position, Number),
      target: Array.from(initialCameraRaw.target, Number),
      up: Array.from(initialCameraRaw.up, Number),
    };
    const initialVisibility = registry.map(item => !item.ref.cell.state.isHidden);

    function structureFromIdentity(selector, indexField = 'structure_index') {
      const hasId = Object.prototype.hasOwnProperty.call(selector, 'structure_id');
      const hasIndex = Object.prototype.hasOwnProperty.call(selector, indexField);
      if (hasId && hasIndex) throw new Error(`Provide either structure_id or ${indexField}, not both.`);
      let entry;
      if (hasId) entry = registry.find(item => item.id === selector.structure_id);
      else if (hasIndex) entry = registry[selector[indexField]];
      else if (registry.length === 1) entry = registry[0];
      else throw new Error(`A structure_id or ${indexField} is required when ${registry.length} structures are loaded.`);
      if (!entry) throw new Error(`Requested structure does not exist. Valid IDs: ${registry.map(item => item.id).join(', ')}.`);
      return entry;
    }

    function structureData(entry) {
      const data = entry.ref.cell.obj?.data;
      if (!data || !Array.isArray(data.units)) throw new Error(`${entry.id} has no accessible Mol* structure data.`);
      return data;
    }

    const atomFilterKeys = new Set([
      'model_index', 'unit_id', 'chain_id', 'residue_number', 'insertion_code',
      'residue_name', 'atom_name', 'altloc',
    ]);

    function hasAtomFilters(selector) {
      return Object.keys(selector).some(key => atomFilterKeys.has(key));
    }

    function resolveLoci(selector, requireSingleAtom = false) {
      const entry = structureFromIdentity(selector);
      const structure = structureData(entry);
      const filtered = hasAtomFilters(selector);
      const elements = [];
      const matches = [];
      for (const unit of structure.units) {
        if (unit.kind !== 0 || !unit.model?.atomicHierarchy || !unit.elements) continue;
        if (Object.prototype.hasOwnProperty.call(selector, 'unit_id') && Number(unit.id) !== selector.unit_id) continue;
        const modelIndex = Array.isArray(structure.models) ? structure.models.indexOf(unit.model) : 0;
        if (Object.prototype.hasOwnProperty.call(selector, 'model_index') && modelIndex !== selector.model_index) continue;
        const hierarchyData = unit.model.atomicHierarchy;
        const atomTable = hierarchyData.atoms || {};
        const residueTable = hierarchyData.residues || {};
        const chainTable = hierarchyData.chains || {};
        const residueIndexFor = hierarchyData.residueAtomSegments?.index;
        const chainIndexFor = hierarchyData.chainAtomSegments?.index;
        const selectedPositions = [];
        for (let position = 0; position < unit.elements.length; position += 1) {
          const element = Number(unit.elements[position]);
          const residueIndex = residueIndexFor ? Number(residueIndexFor[element]) : -1;
          const chainIndex = chainIndexFor ? Number(chainIndexFor[element]) : -1;
          const authChain = String(columnValue(chainTable.auth_asym_id, chainIndex) ?? '');
          const labelChain = String(columnValue(chainTable.label_asym_id, chainIndex) ?? '');
          const authSeq = Number(columnValue(residueTable.auth_seq_id, residueIndex));
          const labelSeq = Number(columnValue(residueTable.label_seq_id, residueIndex));
          const insertionCode = String(columnValue(residueTable.pdbx_PDB_ins_code, residueIndex) ?? '');
          const authComp = String(columnValue(atomTable.auth_comp_id, element) ?? '');
          const labelComp = String(columnValue(atomTable.label_comp_id, element) ?? '');
          const authAtom = String(columnValue(atomTable.auth_atom_id, element) ?? '');
          const labelAtom = String(columnValue(atomTable.label_atom_id, element) ?? '');
          const altloc = String(columnValue(atomTable.label_alt_id, element) ?? '');
          if (filtered) {
            if ('chain_id' in selector && selector.chain_id !== authChain && selector.chain_id !== labelChain) continue;
            if ('residue_number' in selector && selector.residue_number !== authSeq && selector.residue_number !== labelSeq) continue;
            if ('insertion_code' in selector && selector.insertion_code !== insertionCode) continue;
            if ('residue_name' in selector && selector.residue_name !== authComp && selector.residue_name !== labelComp) continue;
            if ('atom_name' in selector && selector.atom_name !== authAtom && selector.atom_name !== labelAtom) continue;
            if ('altloc' in selector && selector.altloc !== altloc) continue;
          }
          selectedPositions.push(position);
          if (requireSingleAtom || matches.length < 20) {
            const coordinate = [0, 0, 0];
            unit.conformation.position(element, coordinate);
            matches.push({
              unit_id: Number(unit.id), model_index: modelIndex,
              chain_id: authChain || labelChain,
              label_chain_id: labelChain,
              residue_number: Number.isFinite(authSeq) ? authSeq : labelSeq,
              label_residue_number: labelSeq,
              insertion_code: insertionCode,
              residue_name: authComp || labelComp,
              atom_name: authAtom || labelAtom,
              altloc,
              element: String(columnValue(atomTable.type_symbol, element) ?? ''),
              coordinate: coordinate.map(Number),
            });
          }
        }
        if (selectedPositions.length) elements.push({ unit, indices: Int32Array.from(selectedPositions) });
      }
      const count = elements.reduce((sum, item) => sum + item.indices.length, 0);
      if (!count) throw new Error(`Selector matched no displayed atoms in ${entry.id}.`);
      if (requireSingleAtom && count !== 1) {
        throw new Error(`Atom selector is ambiguous: it matched ${count} atoms. Add chain_id, residue_number, atom_name, altloc, or unit_id.`);
      }
      return {
        entry,
        count,
        loci: { kind: 'element-loci', structure, elements },
        atom: requireSingleAtom ? matches[0] : null,
      };
    }

    function focusStructure(entry) {
      const sphere = structureData(entry).boundary?.sphere;
      if (!sphere) throw new Error(`${entry.id} has no focusable boundary sphere.`);
      plugin.managers.camera.focusSphere(sphere, { durationMs: 0 });
    }

    function getState() {
      const snapshot = camera.getSnapshot();
      const measurementState = plugin.managers.structure.measurement?.state;
      return {
        viewer: 'molstar',
        camera: {
          position: Array.from(snapshot.position, Number),
          target: Array.from(snapshot.target, Number),
          up: Array.from(snapshot.up, Number),
          mode: String(snapshot.mode),
          fov_radians: Number(snapshot.fov),
          distance: vecLength(vecSub(snapshot.position, snapshot.target)),
        },
        structures: registry.map(item => ({
          // These are session-local identifiers. Source URLs, filenames, asset
          // IDs, and formats stay on the trusted viewer host side.
          structure_id: item.id,
          index: item.index,
          visible: !item.ref.cell.state.isHidden,
          element_count: Number(item.ref.cell.obj?.data?.elementCount || 0),
        })),
        measurements: measurementState ? {
          distances: measurementState.distances.length,
          angles: measurementState.angles.length,
          dihedrals: measurementState.dihedrals.length,
        } : { distances: 0, angles: 0, dihedrals: 0 },
      };
    }

    async function callTool(name, args) {
      if (name === 'viewer_rotate') {
        const snapshot = camera.getSnapshot();
        const target = Array.from(snapshot.target, Number);
        let offset = vecSub(Array.from(snapshot.position, Number), target);
        let up = vecNormalize(Array.from(snapshot.up, Number));
        offset = rotateAroundAxis(offset, up, args.d_azimuth_deg);
        const right = vecNormalize(vecCross(up, offset));
        offset = rotateAroundAxis(offset, right, args.d_elevation_deg);
        up = rotateAroundAxis(up, right, args.d_elevation_deg);
        const viewDirection = vecScale(vecNormalize(offset), -1);
        up = rotateAroundAxis(up, viewDirection, args.d_roll_deg);
        camera.setState({ position: vecAdd(target, offset), target, up: vecNormalize(up) }, 0);
        return { incremental: true, camera: getState().camera };
      }
      if (name === 'viewer_zoom') {
        const snapshot = camera.getSnapshot();
        const target = Array.from(snapshot.target, Number);
        const offset = vecSub(Array.from(snapshot.position, Number), target);
        const currentDistance = vecLength(offset);
        const nextDistance = currentDistance / args.factor;
        if (!Number.isFinite(nextDistance) || nextDistance < 0.01) throw new Error('Requested zoom would place the camera too close to its target.');
        camera.setState({ position: vecAdd(target, vecScale(offset, 1 / args.factor)) }, 0);
        return { factor: args.factor, distance_before: currentDistance, distance_after: nextDistance };
      }
      if (name === 'viewer_measure_distance') {
        const first = resolveLoci(args.a, true);
        const second = resolveLoci(args.b, true);
        const delta = vecSub(first.atom.coordinate, second.atom.coordinate);
        const distance = vecLength(delta);
        const manager = plugin.managers.structure.measurement;
        if (!manager || typeof manager.addDistance !== 'function') {
          throw new Error('Mol* distance annotations are unavailable in this build.');
        }
        const annotation = await manager.addDistance(first.loci, second.loci);
        if (!annotation) throw new Error('Mol* could not create a distance annotation for these atoms.');
        plugin.managers.camera.focusLoci([first.loci, second.loci], { durationMs: 0 });
        return { distance_A: Number(distance.toFixed(6)), a: first.atom, b: second.atom, annotation_created: true };
      }
      if (name === 'viewer_switch_structure') {
        const entry = structureFromIdentity(args, 'index');
        for (const item of registry) hierarchy.toggleVisibility([item.ref], item === entry ? 'show' : 'hide');
        focusStructure(entry);
        return { structure_id: entry.id, index: entry.index, visible_only: true };
      }
      if (name === 'viewer_highlight') {
        const highlights = plugin.managers.interactivity?.lociHighlights;
        if (!highlights) throw new Error('Mol* highlight manager is unavailable.');
        highlights.clearHighlights();
        if (args.clear) return { cleared: true };
        if (!args.selector) throw new Error('selector is required unless clear=true.');
        const selected = resolveLoci(args.selector, false);
        highlights.highlightOnly({ loci: selected.loci }, false);
        return { structure_id: selected.entry.id, matched_atoms: selected.count, highlighted: true };
      }
      if (name === 'viewer_focus') {
        const entry = structureFromIdentity(args.selector);
        if (!hasAtomFilters(args.selector)) {
          focusStructure(entry);
          return { structure_id: entry.id, scope: 'structure' };
        }
        const selected = resolveLoci(args.selector, false);
        plugin.managers.camera.focusLoci(selected.loci, { durationMs: 0 });
        return { structure_id: entry.id, scope: 'selection', matched_atoms: selected.count };
      }
      if (name === 'viewer_visibility') {
        if (hasAtomFilters(args.selector)) {
          throw new Error('Partial atom/residue visibility is unsupported; use only structure_id or structure_index.');
        }
        const entry = structureFromIdentity(args.selector);
        hierarchy.toggleVisibility([entry.ref], args.visible ? 'show' : 'hide');
        return { structure_id: entry.id, visible: args.visible };
      }
      if (name === 'viewer_reset') {
        plugin.managers.interactivity?.lociHighlights?.clearHighlights();
        const measurements = plugin.managers.structure?.measurement;
        if (measurements) {
          if (typeof measurements.clear === 'function') {
            await measurements.clear();
          } else {
            const measurementRefs = [
              ...(measurements.state?.distances || []),
              ...(measurements.state?.angles || []),
              ...(measurements.state?.dihedrals || []),
              ...(measurements.state?.labels || []),
              ...(measurements.state?.orientations || []),
              ...(measurements.state?.planes || []),
            ];
            if (measurementRefs.length && typeof measurements.remove === 'function') {
              await measurements.remove(measurementRefs);
            } else if (measurementRefs.length) {
              throw new Error('Mol* measurement annotations could not be cleared during reset.');
            }
          }
        }
        registry.forEach((item, index) => hierarchy.toggleVisibility([item.ref], initialVisibility[index] ? 'show' : 'hide'));
        camera.setState(initialCamera, 0);
        return { reset: true, state: getState() };
      }
      if (name === 'viewer_get_state') return getState();
      throw new Error(`Unsupported Mol* MCP tool: ${name}.`);
    }

    return Object.freeze({ tools, callTool });
  }

  Object.defineProperty(window, '__ScienceVisionCreateMolstarMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
}());
