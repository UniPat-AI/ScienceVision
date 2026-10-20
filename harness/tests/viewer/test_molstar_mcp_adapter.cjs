const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function column(values) {
  return { value: index => values[index] };
}

test('viewer_measure_distance resolves residue_name from atom columns', async () => {
  const context = vm.createContext({
    URL,
    console,
    location: { origin: 'https://viewer.test' },
    window: {},
  });
  const adapterPath = path.resolve(__dirname, '../../viewer/shared/molstar-mcp-adapter.js');
  vm.runInContext(fs.readFileSync(adapterPath, 'utf8'), context, { filename: adapterPath });

  const hierarchyData = {
    atoms: {
      auth_comp_id: column(['ALA', 'GLY']),
      label_comp_id: column(['ALA', 'GLY']),
      auth_atom_id: column(['CA', 'CA']),
      label_atom_id: column(['CA', 'CA']),
      label_alt_id: column(['', '']),
      type_symbol: column(['C', 'C']),
    },
    // Deliberately omit comp-ID columns here. In Mol*, comp IDs are atom columns.
    residues: {
      auth_seq_id: column([10, 11]),
      label_seq_id: column([10, 11]),
      pdbx_PDB_ins_code: column(['', '']),
    },
    chains: {
      auth_asym_id: column(['A']),
      label_asym_id: column(['A']),
    },
    residueAtomSegments: { index: [0, 1] },
    chainAtomSegments: { index: [0, 0] },
  };
  const model = { atomicHierarchy: hierarchyData };
  const unit = {
    kind: 0,
    id: 7,
    model,
    elements: Int32Array.from([0, 1]),
    conformation: {
      position(element, target) {
        const coordinate = element === 0 ? [0, 0, 0] : [3, 4, 0];
        target[0] = coordinate[0];
        target[1] = coordinate[1];
        target[2] = coordinate[2];
      },
    },
  };
  const structure = {
    units: [unit],
    models: [model],
    elementCount: 2,
    boundary: { sphere: { center: [1.5, 2, 0], radius: 2.5 } },
  };
  const structureRef = {
    cell: {
      state: { isHidden: false },
      obj: { data: structure },
    },
  };
  let annotationCalls = 0;
  let measurementClearCalls = 0;
  let focusedLoci = null;
  const cameraSnapshot = {
    position: [0, 0, 10],
    target: [0, 0, 0],
    up: [0, 1, 0],
    mode: 'perspective',
    fov: 1,
  };
  const plugin = {
    canvas3d: {
      camera: {
        getSnapshot: () => cameraSnapshot,
        setState() {},
      },
    },
    managers: {
      camera: {
        focusLoci(loci) { focusedLoci = loci; },
        focusSphere() {},
      },
      interactivity: { lociHighlights: { clearHighlights() {} } },
      structure: {
        hierarchy: {
          current: { structures: [structureRef] },
          toggleVisibility() {},
        },
        measurement: {
          state: { distances: [], angles: [], dihedrals: [] },
          async addDistance(first, second) {
            annotationCalls += 1;
            assert.equal(first.elements[0].indices[0], 0);
            assert.equal(second.elements[0].indices[0], 1);
            return { ref: 'distance-1' };
          },
          async clear() { measurementClearCalls += 1; },
        },
      },
    },
  };

  const provider = context.window.__ScienceVisionCreateMolstarMCPProvider(
    { plugin },
    [{ url: '/private/assets/two-residues.cif?file=secret-asset-id', format: 'mmcif' }],
  );
  const receipt = await provider.callTool('viewer_measure_distance', {
    a: { chain_id: 'A', residue_number: 10, residue_name: 'ALA', atom_name: 'CA' },
    b: { chain_id: 'A', residue_number: 11, residue_name: 'GLY', atom_name: 'CA' },
  });

  assert.equal(receipt.distance_A, 5);
  assert.equal(receipt.a.residue_name, 'ALA');
  assert.equal(receipt.b.residue_name, 'GLY');
  assert.equal(receipt.annotation_created, true);
  assert.equal(annotationCalls, 1);
  assert.equal(focusedLoci.length, 2);
  const state = await provider.callTool('viewer_get_state', {});
  assert.deepEqual(JSON.parse(JSON.stringify(state.structures)), [{
    structure_id: 'structure-0',
    index: 0,
    visible: true,
    element_count: 2,
  }]);
  const serializedState = JSON.stringify(state);
  assert.doesNotMatch(serializedState, /private|assets|two-residues|secret-asset-id|source|format/i);
  await provider.callTool('viewer_reset', {});
  assert.equal(measurementClearCalls, 1);
});
