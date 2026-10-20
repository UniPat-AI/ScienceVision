(function installScienceVisionMolstarMCPAdapter(global) {
  'use strict';

  function requireFiniteNumber(value, name) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`${name} must be a finite number.`);
    return number;
  }

  function vecAdd(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
  function vecSub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function vecScale(a, scale) { return [a[0] * scale, a[1] * scale, a[2] * scale]; }
  function vecLength(a) { return Math.hypot(a[0], a[1], a[2]); }
  function vecDot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function vecCross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }
  function vecNormalize(a) {
    const length = vecLength(a);
    if (!(length > 1e-12)) return null;
    return [a[0] / length, a[1] / length, a[2] / length];
  }

  // The screen-right and screen-up axes for a look direction, using gluLookAt's
  // convention: fwd is where the camera looks (= -dir, dir points target→camera),
  // right = normalize(fwd × worldUp), up = right × fwd. worldUp is +Y, the same
  // constant the framing always feeds Mol*. Returns null when the look direction
  // is parallel to +Y (right is undefined there), so callers keep pan at 0.
  function cameraRightUp(fwd) {
    const right = vecNormalize(vecCross(fwd, [0, 1, 0]));
    if (!right) return null;
    return { right, up: vecCross(right, fwd) };
  }

  function asXYZ(value) {
    if (Array.isArray(value) && value.length >= 3) {
      return [Number(value[0]), Number(value[1]), Number(value[2])];
    }
    if (value && typeof value === 'object') {
      return [Number(value.x), Number(value.y), Number(value.z)];
    }
    return null;
  }

  const CAMERA_DECIMALS = 6;
  const MIN_FIT_RADIUS = 0.01;
  // How long to keep re-framing while a scene change is still landing. A budget
  // rather than a retry count, because what bounds this is how long Mol* takes to
  // finish building representations, not how many attempts we happen to make: a
  // settled scene converges on the first pass, a super cell expansion streams in
  // over many frames. See holdSemanticCamera.
  const REFRAME_BUDGET_MS = 1500;
  // How many further passes must report an IDENTICAL pose before holdSemanticCamera
  // calls the request unreachable rather than slow. Costs a handful of frames, and
  // a still pose is the only evidence available that waiting cannot help.
  const UNREACHABLE_REPEATS = 3;

  // Recorded angles come straight from the expert's dialled value, so rounding
  // only removes the ~1e-14 residue of one exact forward/backward conversion.
  // It keeps sameJSON comparisons stable across replays.
  function roundCamera(value) {
    const factor = 10 ** CAMERA_DECIMALS;
    const rounded = Math.round(value * factor) / factor;
    return Object.is(rounded, -0) ? 0 : rounded;
  }

  // Exact equality on all five numbers. Both sides are already rounded to
  // CAMERA_DECIMALS, so this is the same comparison a replay makes: near enough
  // is not enough when the value gets written into a trajectory head. Legacy
  // three-field cameras read pan as 0, so a pan-free head still compares equal
  // to a live pan-free pose.
  function sameSemanticCamera(left, right) {
    return left.azimuth_deg === right.azimuth_deg
      && left.elevation_deg === right.elevation_deg
      && left.zoom === right.zoom
      && (left.pan_right || 0) === (right.pan_right || 0)
      && (left.pan_up || 0) === (right.pan_up || 0);
  }

  // Unit vector from the target towards the camera. Matches the world
  // convention the panel already displays: azimuth = atan2(x, z),
  // polar (from +Y) = acos(y / radius), elevation = 90 - polar.
  function directionFromAngles(azimuthDeg, elevationDeg) {
    const polar = (90 - elevationDeg) * Math.PI / 180;
    const azimuth = azimuthDeg * Math.PI / 180;
    const sinPolar = Math.sin(polar);
    return [
      sinPolar * Math.sin(azimuth),
      Math.cos(polar),
      sinPolar * Math.cos(azimuth),
    ];
  }

  const MEASURE_KINDS = Object.freeze(['distance', 'angle', 'dihedral']);
  const CLASH_THRESHOLD_A = 0.4;
  const CLASH_GRID_A = 5;
  const GEMMI_VDW_A = Object.freeze({
    H: 1.2000000476837158, He: 1.399999976158142, Li: 1.8200000524520874,
    Be: 1.5299999713897705, B: 1.9199999570846558, C: 1.7000000476837158,
    N: 1.5499999523162842, O: 1.5199999809265137, F: 1.4700000286102295,
    Ne: 1.5399999618530273, Na: 2.2699999809265137, Mg: 1.7300000190734863,
    Al: 1.840000033378601, Si: 2.0999999046325684, P: 1.7999999523162842,
    S: 1.7999999523162842, Cl: 1.75, Ar: 1.8799999952316284, K: 2.75,
    Ca: 2.309999942779541, Sc: 2.109999895095825, Ti: 1.9500000476837158,
    V: 1.059999942779541, Cr: 1.1299999952316284, Mn: 1.190000057220459,
    Fe: 1.2599999904632568, Co: 1.1299999952316284, Ni: 1.6299999952316284,
    Cu: 1.399999976158142, Zn: 1.3899999856948853, Ga: 1.8700000047683716,
    Ge: 2.109999895095825, As: 1.850000023841858, Se: 1.899999976158142,
    Br: 1.850000023841858, Kr: 2.0199999809265137, Rb: 3.0299999713897705,
    Sr: 2.490000009536743, Y: 1.6100000143051147, Zr: 1.4199999570846558,
    Nb: 1.3300000429153442, Mo: 1.75, Tc: 2.0, Ru: 1.2000000476837158,
    Rh: 1.2200000286102295, Pd: 1.6299999952316284, Ag: 1.7200000286102295,
    Cd: 1.5800000429153442, In: 1.9299999475479126, Sn: 2.1700000762939453,
    Sb: 2.059999942779541, Te: 2.059999942779541, I: 1.9800000190734863,
    Xe: 2.1600000858306885, Cs: 3.430000066757202, Ba: 2.680000066757202,
    La: 1.8300000429153442, Ce: 1.8600000143051147, Pr: 1.6200000047683716,
    Nd: 1.7899999618530273, Pm: 1.7599999904632568, Sm: 1.7400000095367432,
    Eu: 1.9600000381469727, Gd: 1.690000057220459, Tb: 1.659999966621399,
    Dy: 1.6299999952316284, Ho: 1.6100000143051147, Er: 1.590000033378601,
    Tm: 1.5700000524520874, Yb: 1.5399999618530273, Lu: 1.5299999713897705,
    Hf: 1.399999976158142, Ta: 1.2200000286102295, W: 1.2599999904632568,
    Re: 1.2999999523162842, Os: 1.5800000429153442, Ir: 1.2200000286102295,
    Pt: 1.75, Au: 1.659999966621399, Hg: 1.5499999523162842,
    Tl: 1.9600000381469727, Pb: 2.0199999809265137, Bi: 2.069999933242798,
    Po: 1.9700000286102295, At: 2.0199999809265137, Rn: 2.200000047683716,
    Fr: 3.4800000190734863, Ra: 2.8299999237060547, Ac: 2.119999885559082,
    Th: 1.840000033378601, Pa: 1.600000023841858, U: 1.8600000143051147,
    Np: 1.7100000381469727, Pu: 1.6699999570846558, Am: 1.659999966621399,
    Cm: 1.649999976158142, Bk: 1.6399999856948853, Cf: 1.6299999952316284,
  });

  function clashAtomKey(atom) {
    const id = atom.atom_id || atom;
    return [
      String(id.chain_id || ''),
      String(id.comp_id || ''),
      String(id.seq_id),
      String(id.icode || ''),
      String(id.atom_id || id.name || ''),
    ].join('|');
  }

  function emptyClashResult(excluded) {
    return {
      has_severe_clash: 'no',
      excluded_struct_conn_pairs: excluded || 0,
      expert_summary: '没有可比较的配体-聚合物原子对。',
    };
  }

  function scanClash(nonpolymer, polymer, connectedPairs) {
    if (!nonpolymer.length) return emptyClashResult(0);
    if (!polymer.length) return emptyClashResult(0);
    const connected = new Set(connectedPairs || []);
    const grid = new Map();
    function cellKey(x, y, z) {
      return `${Math.floor(x / CLASH_GRID_A)}|${Math.floor(y / CLASH_GRID_A)}|${Math.floor(z / CLASH_GRID_A)}`;
    }
    polymer.forEach((record, index) => {
      const key = cellKey(record.xyz[0], record.xyz[1], record.xyz[2]);
      const bucket = grid.get(key);
      if (bucket) bucket.push(index);
      else grid.set(key, [index]);
    });
    let best = null;
    let excluded = 0;
    for (let leftIndex = 0; leftIndex < nonpolymer.length; leftIndex += 1) {
      const left = nonpolymer[leftIndex];
      const [x, y, z] = left.xyz;
      const cx = Math.floor(x / CLASH_GRID_A);
      const cy = Math.floor(y / CLASH_GRID_A);
      const cz = Math.floor(z / CLASH_GRID_A);
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          for (let dz = -1; dz <= 1; dz += 1) {
            const bucket = grid.get(`${cx + dx}|${cy + dy}|${cz + dz}`);
            if (!bucket) continue;
            for (const rightIndex of bucket) {
              const right = polymer[rightIndex];
              const pair = `${clashAtomKey(left)}||${clashAtomKey(right)}`;
              const rev = `${clashAtomKey(right)}||${clashAtomKey(left)}`;
              if (connected.has(pair) || connected.has(rev)) {
                excluded += 1;
                continue;
              }
              const dist = Math.hypot(
                x - right.xyz[0], y - right.xyz[1], z - right.xyz[2],
              );
              const overlap = left.vdw_A + right.vdw_A - dist;
              const candidate = { overlap, dist, leftIndex, rightIndex };
              if (!best
                  || candidate.overlap > best.overlap
                  || (candidate.overlap === best.overlap && candidate.dist < best.dist)) {
                best = candidate;
              }
            }
          }
        }
      }
    }
    if (!best) return emptyClashResult(excluded);
    const left = nonpolymer[best.leftIndex];
    const right = polymer[best.rightIndex];
    const gap = Math.abs(best.overlap - CLASH_THRESHOLD_A);
    let relation = 'equal';
    if (best.overlap > CLASH_THRESHOLD_A) relation = 'above';
    else if (best.overlap < CLASH_THRESHOLD_A) relation = 'below';
    return {
      has_severe_clash: best.overlap >= CLASH_THRESHOLD_A ? 'yes' : 'no',
      vdw_overlap_A: Number(best.overlap.toFixed(6)),
      threshold_gap_A: Number(gap.toFixed(6)),
      threshold_relation: relation,
      distance_A: Number(best.dist.toFixed(6)),
      element_pair: [left.atom_id.element, right.atom_id.element],
      nonpolymer_atom_id: left.atom_id,
      polymer_atom_id: right.atom_id,
      nonpolymer_vdw_A: Number(left.vdw_A.toFixed(6)),
      polymer_vdw_A: Number(right.vdw_A.toFixed(6)),
      excluded_struct_conn_pairs: excluded,
      expert_summary: formatClashSummary({
        has_severe_clash: best.overlap >= CLASH_THRESHOLD_A ? 'yes' : 'no',
        vdw_overlap_A: best.overlap,
        nonpolymer_atom_id: left.atom_id,
        polymer_atom_id: right.atom_id,
      }),
    };
  }

  function formatClashAtom(atomId) {
    const id = atomId || {};
    const bits = [id.comp_id, id.chain_id, id.seq_id].filter(
      part => part !== '' && part != null,
    );
    const atom = id.atom_id || id.name || '';
    return `${bits.join(' ')} 的 ${atom}`.trim();
  }

  function formatClashSummary(result) {
    const severe = result.has_severe_clash === 'yes' ? '有' : '没有';
    const overlap = Number(result.vdw_overlap_A);
    const overlapText = Number.isFinite(overlap) ? overlap.toFixed(2) : String(result.vdw_overlap_A);
    return `最大 clash：${severe}，重叠 ${overlapText} Å（配体 ${formatClashAtom(result.nonpolymer_atom_id)} — 聚合物 ${formatClashAtom(result.polymer_atom_id)}）`;
  }

  const MEASURE_SUMMARY_LABELS = Object.freeze({
    distance: '距离',
    angle: '键角',
    dihedral: '二面角',
  });

  function formatMeasureSummary(kind, atoms, value) {
    const title = MEASURE_SUMMARY_LABELS[kind] || kind;
    const number = value == null ? '' : String(value);
    const names = (Array.isArray(atoms) ? atoms : [])
      .map(atom => String(atom && atom.identity || '').trim())
      .filter(Boolean);
    if (!names.length) return `${title}：${number}`;
    return `${title}：${number}（${names.join(' — ')}）`;
  }

  function cleanCifValue(value) {
    if (value == null) return '';
    const text = String(value).replace(/\x00/g, '').trim();
    if (text === '.' || text === '?') return '';
    return text;
  }

  function gemmiElementName(symbol) {
    const raw = cleanCifValue(symbol);
    if (!raw) return '';
    return raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
  }

  function tokenizeCif(text) {
    const tokens = [];
    let index = 0;
    const n = text.length;
    while (index < n) {
      const ch = text[index];
      if (ch === ' ' || ch === '\t' || ch === '\r' || ch === '\n') {
        index += 1;
        continue;
      }
      if (ch === '#') {
        while (index < n && text[index] !== '\n') index += 1;
        continue;
      }
      if (ch === "'" || ch === '"') {
        const quote = ch;
        index += 1;
        let value = '';
        while (index < n && text[index] !== quote) {
          value += text[index];
          index += 1;
        }
        if (index < n) index += 1;
        tokens.push(value);
        continue;
      }
      if (ch === ';' && (index === 0 || text[index - 1] === '\n')) {
        index += 1;
        let value = '';
        while (index < n) {
          if (text[index] === ';' && text[index - 1] === '\n') {
            index += 1;
            break;
          }
          value += text[index];
          index += 1;
        }
        tokens.push(value.replace(/\n$/, ''));
        continue;
      }
      let value = '';
      while (index < n && !/\s/.test(text[index])) {
        value += text[index];
        index += 1;
      }
      tokens.push(value);
    }
    return tokens;
  }

  function parseCifLoops(tokens) {
    const loops = Object.create(null);
    let index = 0;
    while (index < tokens.length) {
      const token = tokens[index];
      if (token === 'loop_') {
        index += 1;
        const tags = [];
        while (index < tokens.length && tokens[index].charAt(0) === '_') {
          tags.push(tokens[index]);
          index += 1;
        }
        const rows = [];
        while (index < tokens.length) {
          const next = tokens[index];
          if (next === 'loop_' || next === 'stop_' || next === 'global_'
              || next.indexOf('data_') === 0 || next.indexOf('save_') === 0
              || next.charAt(0) === '_') {
            break;
          }
          const row = [];
          for (let col = 0; col < tags.length; col += 1) {
            if (index >= tokens.length) break;
            row.push(tokens[index]);
            index += 1;
          }
          if (row.length === tags.length) rows.push(row);
          else break;
        }
        if (tags.length) {
          const map = Object.create(null);
          tags.forEach((tag, col) => { map[tag] = col; });
          loops[tags[0].slice(0, tags[0].indexOf('.') + 1)] = { tags, rows, map };
        }
        continue;
      }
      if (token.charAt(0) === '_' && index + 1 < tokens.length) {
        const value = tokens[index + 1];
        if (value !== 'loop_' && value.charAt(0) !== '_') {
          const prefix = token.slice(0, token.indexOf('.') + 1);
          if (!loops[prefix]) loops[prefix] = { tags: [], rows: [], map: Object.create(null), kv: Object.create(null) };
          if (!loops[prefix].kv) loops[prefix].kv = Object.create(null);
          loops[prefix].kv[token] = value;
          index += 2;
          continue;
        }
      }
      index += 1;
    }
    return loops;
  }

  function cifCell(loop, row, tag) {
    if (loop.kv && Object.prototype.hasOwnProperty.call(loop.kv, tag)) {
      return cleanCifValue(loop.kv[tag]);
    }
    const col = loop.map[tag];
    if (col == null) return '';
    return cleanCifValue(row[col]);
  }

  // Core CIF tags have no dot, so parseCifLoops buckets them under prefix "".
  // A later no-dot loop_ overwrites that bucket and drops the cell kv. Collect
  // non-loop pairs by full tag name instead.
  function parseCifNonLoopPairs(tokens) {
    const kv = Object.create(null);
    let index = 0;
    while (index < tokens.length) {
      const token = tokens[index];
      if (token === 'loop_') {
        index += 1;
        const tags = [];
        while (index < tokens.length && tokens[index].charAt(0) === '_') {
          tags.push(tokens[index]);
          index += 1;
        }
        while (index < tokens.length) {
          const next = tokens[index];
          if (next === 'loop_' || next === 'stop_' || next === 'global_'
              || next.indexOf('data_') === 0 || next.indexOf('save_') === 0
              || next.charAt(0) === '_') {
            break;
          }
          index += tags.length || 1;
        }
        continue;
      }
      if (token.charAt(0) === '_' && index + 1 < tokens.length) {
        const value = tokens[index + 1];
        if (value !== 'loop_' && value.charAt(0) !== '_') {
          kv[token] = value;
          index += 2;
          continue;
        }
      }
      index += 1;
    }
    return kv;
  }

  function firstCifValue(kv, tags) {
    for (const tag of tags) {
      if (Object.prototype.hasOwnProperty.call(kv, tag) && cleanCifValue(kv[tag])) {
        return kv[tag];
      }
    }
    return '';
  }

  function stripCifEsd(value) {
    const text = cleanCifValue(value);
    const match = text.match(/^([+-]?(?:\d+\.?\d*|\.\d+))(?:\(\d+\))?$/);
    return match ? match[1] : text;
  }

  function cifNumber(value) {
    const text = stripCifEsd(value);
    if (!text) return NaN;
    const number = Number(text);
    return Number.isFinite(number) ? number : NaN;
  }

  function unitCellVolumeA3(a, b, c, alphaDeg, betaDeg, gammaDeg) {
    const toRad = Math.PI / 180;
    const cosA = Math.cos(alphaDeg * toRad);
    const cosB = Math.cos(betaDeg * toRad);
    const cosG = Math.cos(gammaDeg * toRad);
    const argument = 1 - cosA * cosA - cosB * cosB - cosG * cosG + 2 * cosA * cosB * cosG;
    if (!(argument > 0)) return NaN;
    return a * b * c * Math.sqrt(argument);
  }

  function formatCellNumber(value) {
    if (!Number.isFinite(value)) return '';
    if (Math.abs(value - Math.round(value)) < 1e-9) return String(Math.round(value));
    return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  }

  function formatUnitCellSummary(result) {
    const a = formatCellNumber(result.cell_lengths_A[0]);
    const b = formatCellNumber(result.cell_lengths_A[1]);
    const c = formatCellNumber(result.cell_lengths_A[2]);
    const alpha = formatCellNumber(result.cell_angles_deg[0]);
    const beta = formatCellNumber(result.cell_angles_deg[1]);
    const gamma = formatCellNumber(result.cell_angles_deg[2]);
    const volume = formatCellNumber(result.unit_cell_volume_A3);
    return `晶胞：${result.spacegroup_hm}；a=${a} Å  b=${b} Å  c=${c} Å；α=${alpha}°  β=${beta}°  γ=${gamma}°；体积 ${volume} Å³`;
  }

  function parseUnitCellCif(text) {
    const kv = parseCifNonLoopPairs(tokenizeCif(String(text || '')));
    const a = cifNumber(firstCifValue(kv, ['_cell.length_a', '_cell_length_a']));
    const b = cifNumber(firstCifValue(kv, ['_cell.length_b', '_cell_length_b']));
    const c = cifNumber(firstCifValue(kv, ['_cell.length_c', '_cell_length_c']));
    const alpha = cifNumber(firstCifValue(kv, ['_cell.angle_alpha', '_cell_angle_alpha']));
    const beta = cifNumber(firstCifValue(kv, ['_cell.angle_beta', '_cell_angle_beta']));
    const gamma = cifNumber(firstCifValue(kv, ['_cell.angle_gamma', '_cell_angle_gamma']));
    if (![a, b, c, alpha, beta, gamma].every(Number.isFinite)) {
      throw new Error('当前文件没有晶胞参数。');
    }
    let volume = cifNumber(firstCifValue(kv, ['_cell.volume', '_cell_volume']));
    if (!Number.isFinite(volume)) volume = unitCellVolumeA3(a, b, c, alpha, beta, gamma);
    if (!Number.isFinite(volume)) {
      throw new Error('当前文件没有晶胞参数。');
    }
    const spacegroup = cleanCifValue(firstCifValue(kv, [
      '_space_group.name_H-M_alt',
      '_symmetry.space_group_name_H-M',
      '_space_group_name_H-M_alt',
      '_symmetry_space_group_name_H-M',
    ]));
    const result = {
      spacegroup_hm: spacegroup,
      cell_lengths_A: [a, b, c],
      cell_angles_deg: [alpha, beta, gamma],
      unit_cell_volume_A3: volume,
    };
    result.expert_summary = formatUnitCellSummary(result);
    return result;
  }

  async function fetchLoadedCifText() {
    let file = '';
    try {
      const href = global.location && global.location.href;
      if (href && href !== 'about:blank') {
        file = new URL(href).searchParams.get('file') || '';
      }
    } catch (_error) {}
    if (!file && typeof global.__svMolstarStructureFile === 'string') {
      file = global.__svMolstarStructureFile;
    }
    if (!file) {
      throw new Error('当前文件没有晶胞参数。');
    }
    let url = file;
    try {
      const href = global.location && global.location.href;
      if (href && href.indexOf('http') === 0) url = new URL(file, href).href;
    } catch (_error) {}
    const response = await global.fetch(url);
    if (!response || !response.ok) {
      throw new Error('当前文件没有晶胞参数。');
    }
    return response.text();
  }

  function loadedStructureFile() {
    let file = '';
    try {
      const href = global.location && global.location.href;
      if (href && href !== 'about:blank') {
        file = new URL(href).searchParams.get('file') || '';
      }
    } catch (_error) {}
    if (!file && typeof global.__svMolstarStructureFile === 'string') {
      file = global.__svMolstarStructureFile;
    }
    return file;
  }

  async function fetchLoadedStructureText() {
    const file = loadedStructureFile();
    if (!file) throw new Error('无法读取当前结构文件。');
    let url = file;
    try {
      const href = global.location && global.location.href;
      if (href && href.indexOf('http') === 0) url = new URL(file, href).href;
    } catch (_error) {}
    const response = await global.fetch(url);
    if (!response || !response.ok) throw new Error('无法读取当前结构文件。');
    return response.text();
  }

  function collisionRole(entityType, residueName) {
    const residue = String(residueName || '').toUpperCase();
    if (entityType === 'NonPolymer' && residue !== 'HOH' && residue !== 'WAT' && residue !== 'DOD') {
      return 'nonpolymer';
    }
    if (entityType === 'Polymer') return 'polymer';
    return null;
  }

  function normalizeEntityType(cifType) {
    const type = String(cifType || '').toLowerCase();
    if (type === 'polymer') return 'Polymer';
    if (type === 'non-polymer' || type === 'nonpolymer') return 'NonPolymer';
    return '';
  }

  function collapseAltloc(records) {
    const grouped = new Map();
    for (const record of records) {
      const atom = record.atom_id;
      const key = [
        atom.entity_id, atom.chain_id, atom.subchain_id, atom.comp_id,
        String(atom.seq_id), atom.icode, atom.atom_id,
      ].join('|');
      const bucket = grouped.get(key);
      if (bucket) bucket.push(record);
      else grouped.set(key, [record]);
    }
    const selected = [];
    const keys = Array.from(grouped.keys()).sort();
    for (const key of keys) {
      const bucket = grouped.get(key);
      bucket.sort((left, right) => {
        const occ = right.occupancy - left.occupancy;
        if (occ) return occ;
        const leftBlank = left.atom_id.altloc ? 1 : 0;
        const rightBlank = right.atom_id.altloc ? 1 : 0;
        if (leftBlank !== rightBlank) return leftBlank - rightBlank;
        if (left.atom_id.altloc < right.atom_id.altloc) return -1;
        if (left.atom_id.altloc > right.atom_id.altloc) return 1;
        return 0;
      });
      selected.push(bucket[0]);
    }
    return selected;
  }

  function parseMmCifClashInput(text) {
    if (!text || !String(text).trim()) {
      throw new Error('无法读取结构连接：CIF 为空。');
    }
    const loops = parseCifLoops(tokenizeCif(String(text)));
    const entityLoop = loops['_entity.'];
    const atomLoop = loops['_atom_site.'];
    if (!atomLoop || !atomLoop.rows.length) {
      throw new Error('无法读取结构连接：CIF 没有 _atom_site。');
    }
    const entityTypeById = Object.create(null);
    const entityRows = entityLoop && entityLoop.rows.length
      ? entityLoop.rows
      : (entityLoop && entityLoop.kv ? [[]] : []);
    for (const row of entityRows) {
      const id = cifCell(entityLoop, row, '_entity.id');
      const type = normalizeEntityType(cifCell(entityLoop, row, '_entity.type'));
      if (id) entityTypeById[id] = type;
    }
    const nonpolymer = [];
    const polymer = [];
    for (const row of atomLoop.rows) {
      const model = cifCell(atomLoop, row, '_atom_site.pdbx_PDB_model_num');
      if (model && model !== '1') continue;
      const element = gemmiElementName(cifCell(atomLoop, row, '_atom_site.type_symbol'));
      const occupancy = Number(cifCell(atomLoop, row, '_atom_site.occupancy') || '1');
      if (!element || element === 'H' || !(occupancy > 0)) continue;
      const entityId = cifCell(atomLoop, row, '_atom_site.label_entity_id');
      const comp = cifCell(atomLoop, row, '_atom_site.auth_comp_id')
        || cifCell(atomLoop, row, '_atom_site.label_comp_id');
      const role = collisionRole(entityTypeById[entityId] || '', comp);
      if (!role) continue;
      const seqRaw = cifCell(atomLoop, row, '_atom_site.auth_seq_id')
        || cifCell(atomLoop, row, '_atom_site.label_seq_id');
      const seqId = Number.parseInt(seqRaw, 10);
      if (!Number.isFinite(seqId)) continue;
      const x = Number(cifCell(atomLoop, row, '_atom_site.Cartn_x'));
      const y = Number(cifCell(atomLoop, row, '_atom_site.Cartn_y'));
      const z = Number(cifCell(atomLoop, row, '_atom_site.Cartn_z'));
      if (![x, y, z].every(Number.isFinite)) continue;
      const vdw = GEMMI_VDW_A[element];
      if (!Number.isFinite(vdw)) {
        throw new Error(`无法读取结构连接：元素 ${element} 没有 Gemmi 半径。`);
      }
      const record = {
        atom_id: {
          entity_id: entityId,
          chain_id: cifCell(atomLoop, row, '_atom_site.auth_asym_id')
            || cifCell(atomLoop, row, '_atom_site.label_asym_id'),
          subchain_id: cifCell(atomLoop, row, '_atom_site.label_asym_id'),
          comp_id: comp,
          seq_id: seqId,
          icode: cifCell(atomLoop, row, '_atom_site.pdbx_PDB_ins_code'),
          atom_id: cifCell(atomLoop, row, '_atom_site.auth_atom_id')
            || cifCell(atomLoop, row, '_atom_site.label_atom_id'),
          altloc: cifCell(atomLoop, row, '_atom_site.label_alt_id'),
          element,
        },
        xyz: [x, y, z],
        occupancy,
        vdw_A: vdw,
      };
      if (role === 'nonpolymer') nonpolymer.push(record);
      else polymer.push(record);
    }
    const collapsedNonpolymer = collapseAltloc(nonpolymer);
    const collapsedPolymer = collapseAltloc(polymer);
    const connected = [];
    const connLoop = loops['_struct_conn.'];
    if (connLoop) {
      for (const row of connLoop.rows) {
        const left = [
          cifCell(connLoop, row, '_struct_conn.ptnr1_auth_asym_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr1_label_asym_id'),
          cifCell(connLoop, row, '_struct_conn.ptnr1_auth_comp_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr1_label_comp_id'),
          cifCell(connLoop, row, '_struct_conn.ptnr1_auth_seq_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr1_label_seq_id'),
          cifCell(connLoop, row, '_struct_conn.pdbx_ptnr1_PDB_ins_code'),
          cifCell(connLoop, row, '_struct_conn.ptnr1_label_atom_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr1_auth_atom_id'),
        ].join('|');
        const right = [
          cifCell(connLoop, row, '_struct_conn.ptnr2_auth_asym_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr2_label_asym_id'),
          cifCell(connLoop, row, '_struct_conn.ptnr2_auth_comp_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr2_label_comp_id'),
          cifCell(connLoop, row, '_struct_conn.ptnr2_auth_seq_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr2_label_seq_id'),
          cifCell(connLoop, row, '_struct_conn.pdbx_ptnr2_PDB_ins_code'),
          cifCell(connLoop, row, '_struct_conn.ptnr2_label_atom_id')
            || cifCell(connLoop, row, '_struct_conn.ptnr2_auth_atom_id'),
        ].join('|');
        connected.push(`${left}||${right}`, `${right}||${left}`);
      }
    }
    return {
      nonpolymer: collapsedNonpolymer,
      polymer: collapsedPolymer,
      connected,
    };
  }

  function parseMmCifMeasureAtoms(text) {
    if (!text || !String(text).trim()) {
      throw new Error('无法读取结构：CIF 为空。');
    }
    const loops = parseCifLoops(tokenizeCif(String(text)));
    const atomLoop = loops['_atom_site.'];
    if (!atomLoop || !atomLoop.rows.length) {
      throw new Error('无法读取结构：CIF 没有 _atom_site。');
    }
    const records = [];
    for (const row of atomLoop.rows) {
      const model = cifCell(atomLoop, row, '_atom_site.pdbx_PDB_model_num');
      if (model && model !== '1') continue;
      const element = gemmiElementName(cifCell(atomLoop, row, '_atom_site.type_symbol'));
      const occupancy = Number(cifCell(atomLoop, row, '_atom_site.occupancy') || '1');
      if (!element || element === 'H' || !(occupancy > 0)) continue;
      const seqRaw = cifCell(atomLoop, row, '_atom_site.auth_seq_id')
        || cifCell(atomLoop, row, '_atom_site.label_seq_id');
      const seqId = Number.parseInt(seqRaw, 10);
      if (!Number.isFinite(seqId)) continue;
      const x = Number(cifCell(atomLoop, row, '_atom_site.Cartn_x'));
      const y = Number(cifCell(atomLoop, row, '_atom_site.Cartn_y'));
      const z = Number(cifCell(atomLoop, row, '_atom_site.Cartn_z'));
      if (![x, y, z].every(Number.isFinite)) continue;
      records.push({
        atom_id: {
          chain_id: cifCell(atomLoop, row, '_atom_site.auth_asym_id')
            || cifCell(atomLoop, row, '_atom_site.label_asym_id'),
          comp_id: cifCell(atomLoop, row, '_atom_site.auth_comp_id')
            || cifCell(atomLoop, row, '_atom_site.label_comp_id'),
          seq_id: seqId,
          icode: cifCell(atomLoop, row, '_atom_site.pdbx_PDB_ins_code'),
          atom_id: cifCell(atomLoop, row, '_atom_site.auth_atom_id')
            || cifCell(atomLoop, row, '_atom_site.label_atom_id'),
          altloc: cifCell(atomLoop, row, '_atom_site.label_alt_id'),
          element,
        },
        xyz: [x, y, z],
        occupancy,
      });
    }
    return collapseAltloc(records);
  }

  const MAX_ATOM_LABELS = 16;

  function looksLikeXyz(text) {
    const first = String(text || '').split(/\r?\n/, 1)[0];
    const count = Number.parseInt(String(first || '').trim(), 10);
    return Number.isInteger(count) && count >= 1;
  }

  function parseXyzAtomSerials(text) {
    const lines = String(text || '').split(/\r?\n/);
    const count = Number.parseInt(String(lines[0] || '').trim(), 10);
    if (!Number.isInteger(count) || count < 1) {
      throw new Error('无法读取结构：XYZ 原子数无效。');
    }
    const records = [];
    for (let index = 0; index < count; index += 1) {
      const parts = String(lines[index + 2] || '').trim().split(/\s+/);
      if (parts.length < 4) continue;
      const x = Number(parts[1]);
      const y = Number(parts[2]);
      const z = Number(parts[3]);
      if (![x, y, z].every(Number.isFinite)) continue;
      records.push({ serial: index + 1, xyz: [x, y, z] });
    }
    if (!records.length) throw new Error('无法读取结构：XYZ 没有原子坐标。');
    return records;
  }

  function parseCifAtomSerials(text) {
    if (!text || !String(text).trim()) {
      throw new Error('无法读取结构：CIF 为空。');
    }
    const loops = parseCifLoops(tokenizeCif(String(text)));
    const atomLoop = loops['_atom_site.'];
    if (!atomLoop || !atomLoop.rows.length) {
      throw new Error('无法读取结构：CIF 没有 _atom_site。');
    }
    const records = [];
    let fallback = 0;
    for (const row of atomLoop.rows) {
      const model = cifCell(atomLoop, row, '_atom_site.pdbx_PDB_model_num');
      if (model && model !== '1') continue;
      const occupancy = Number(cifCell(atomLoop, row, '_atom_site.occupancy') || '1');
      if (!(occupancy > 0)) continue;
      const x = Number(cifCell(atomLoop, row, '_atom_site.Cartn_x'));
      const y = Number(cifCell(atomLoop, row, '_atom_site.Cartn_y'));
      const z = Number(cifCell(atomLoop, row, '_atom_site.Cartn_z'));
      if (![x, y, z].every(Number.isFinite)) continue;
      fallback += 1;
      const serial = Number.parseInt(cifCell(atomLoop, row, '_atom_site.id'), 10);
      records.push({
        serial: Number.isInteger(serial) && serial >= 1 ? serial : fallback,
        xyz: [x, y, z],
      });
    }
    if (!records.length) throw new Error('无法读取结构：CIF 没有可用原子。');
    return records;
  }

  function parseAtomSerials(text, file) {
    const lower = String(file || '').toLowerCase();
    if (lower.includes('.xyz') || (!/_atom_site/.test(String(text || '')) && looksLikeXyz(text))) {
      return parseXyzAtomSerials(text);
    }
    return parseCifAtomSerials(text);
  }

  function normalizeAtomIds(raw) {
    if (!Array.isArray(raw)) return [];
    const seen = new Set();
    const ids = [];
    for (const item of raw) {
      const serial = Number(item);
      if (!Number.isInteger(serial) || serial < 1 || seen.has(serial)) continue;
      seen.add(serial);
      ids.push(serial);
    }
    return ids;
  }

  function measuredAtomLabel(id) {
    return [id.chain_id, id.comp_id, String(id.seq_id), id.atom_id].filter(Boolean).join(' ');
  }

  function requireMeasuredAtom(atoms, args, side) {
    const chain = String(args && args[`chain_${side}`] || '');
    const seq = Number(args && args[`seq_id_${side}`]);
    const atom = String(args && args[`atom_id_${side}`] || '');
    const hasComp = args && Object.prototype.hasOwnProperty.call(args, `comp_id_${side}`);
    const comp = hasComp ? String(args[`comp_id_${side}`] || '') : '';
    const hasIcode = args && Object.prototype.hasOwnProperty.call(args, `icode_${side}`);
    const icode = hasIcode ? String(args[`icode_${side}`] || '') : '';
    if (!chain || !atom || !Number.isInteger(seq)) {
      throw new Error('测距需要 chain、残基号和原子名。');
    }
    const hit = atoms.find(record => {
      const id = record.atom_id;
      if (id.chain_id !== chain || Number(id.seq_id) !== seq || id.atom_id !== atom) return false;
      if ((id.icode || '') !== icode) return false;
      if (comp && id.comp_id !== comp) return false;
      return true;
    });
    if (!hit) {
      if (comp) throw new Error(`找不到链 ${chain} 残基 ${seq}（${comp}）原子 ${atom}。`);
      throw new Error(`找不到链 ${chain} 残基 ${seq} 原子 ${atom}。`);
    }
    return hit;
  }

  function measureAtomsReplayInput(args) {
    const replay = {
      chain_a: String(args.chain_a),
      seq_id_a: Number(args.seq_id_a),
      atom_id_a: String(args.atom_id_a),
      chain_b: String(args.chain_b),
      seq_id_b: Number(args.seq_id_b),
      atom_id_b: String(args.atom_id_b),
    };
    for (const key of ['comp_id_a', 'icode_a', 'comp_id_b', 'icode_b']) {
      if (args && Object.prototype.hasOwnProperty.call(args, key) && args[key] != null && args[key] !== '') {
        replay[key] = String(args[key]);
      }
    }
    return replay;
  }

  // `model` and `bio_assembly` both drive Mol*'s `default` provider; they differ
  // only in the `structure` param applySymmetryPreset passes — model builds the
  // full deposited model (every crystallographic copy), bio_assembly leaves the
  // provider's default (biological Assembly 1). Clash is computed on the deposited
  // atom list, so only `model` guarantees the winning atom is on screen to draw.
  const SYMMETRY_PRESETS = Object.freeze({
    model: 'default',
    bio_assembly: 'default',
    crystal_contacts: 'crystalContacts',
    unit_cell: 'unitcell',
    super_cell: 'supercell',
  });
  const SYMMETRY_PRESET_LABELS = Object.freeze({
    model: '模型',
    bio_assembly: '生物组装体',
    crystal_contacts: '晶体接触',
    unit_cell: '晶胞',
    super_cell: '超胞',
  });
  const SYMMETRY_PRESET_IDS = Object.freeze({
    default: 'preset-trajectory-default',
    crystalContacts: 'preset-trajectory-crystal-contacts',
    unitcell: 'preset-trajectory-unitcell',
    supercell: 'preset-trajectory-supercell',
  });

  // Sentinel message for "already on this preset". The API wrapper in
  // viewer-mcp-api.js rewrites provider errors and DROPS custom properties such
  // as error.code, so the message text is the only channel the replay path has.
  // task-mcp-controls.js matches on it — CHANGING THIS WORDING REQUIRES THE
  // MATCHING CHANGE THERE.
  function symmetryUnchangedMessage(label) {
    return `已经是${label}，未再次切换。`;
  }

  function sameIJK(value, a, b, c) {
    const xyz = asXYZ(value);
    return Boolean(xyz) && xyz[0] === a && xyz[1] === b && xyz[2] === c;
  }

  // The preset is fully determined by the structure-from-model transform's
  // `type` params, so it is derived rather than tracked. A tracked copy is a
  // second source of truth and can disagree with the picture.
  function presetFromStructureType(type) {
    const name = type && type.name;
    // Deposited model vs biological Assembly 1 are DIFFERENT structures (superset
    // vs subset of atoms), so they map to different presets. Conflating them once
    // let the clash guard pass on an Assembly-1 view whose missing atom then threw
    // downstream, and falsely reported 「模型」 while Assembly 1 was on screen.
    if (name === 'model') return 'model';
    if (name === 'assembly') return 'bio_assembly';
    if (name === 'symmetry-mates') return 'crystal_contacts';
    if (name === 'symmetry') {
      const params = type.params || {};
      if (sameIJK(params.ijkMin, 0, 0, 0) && sameIJK(params.ijkMax, 0, 0, 0)) {
        return 'unit_cell';
      }
      if (sameIJK(params.ijkMin, -1, -1, -1) && sameIJK(params.ijkMax, 1, 1, 1)) {
        return 'super_cell';
      }
    }
    return null;
  }

  function createProvider(viewer) {
    const registry = global.__ScienceVisionMolstarToolRegistry;
    if (!registry || !Array.isArray(registry.tools)) {
      throw new Error('Mol* tool registry is unavailable.');
    }
    const plugin = viewer?.plugin;
    const camera = plugin?.canvas3d?.camera;
    if (!plugin || !camera) throw new Error('Mol* plugin camera is unavailable.');
    const hierarchy = plugin.managers?.structure?.hierarchy;
    const chainScopeFactory = global.ScienceVisionMolstarChainScope;
    const chainScope = chainScopeFactory && typeof chainScopeFactory.create === 'function'
      ? chainScopeFactory.create(plugin) : null;
    function setSvMcpPick(flag) {
      const root = global.document && global.document.documentElement;
      if (root) root.dataset.svMcpPick = flag;
    }
    function setLegacyInputMode(mode) {
      const input = plugin.canvas3d && plugin.canvas3d.input;
      if (input && typeof input.setMode === 'function') input.setMode(mode);
    }
    // A one-time snapshot of a fact at entry, not a running ledger.
    let initialSemanticCamera = null;
    let lastSemanticCamera = null;
    // The five-tuple captured when a measurement STARTS, replayed to re-frame
    // when it COMMITS (§3.4a). Held across the two callTool invocations because
    // start and commit are separate MCP calls. A one-shot fact, not a ledger:
    // read once on commit, then cleared.
    let measureFraming = null;
    let residuePickFraming = null;
    let residueOverlays = [];
    const initialVisibility = Array.isArray(hierarchy?.current?.structures)
      ? hierarchy.current.structures.map(ref => !ref?.cell?.state?.isHidden)
      : [];
    const SETTLE_TIMEOUT_MS = 4000;

    // Mol* applies deferred work (structure rebuilds, requestCameraReset) during
    // a commit, and only then refreshes the bounding sphere and the hierarchy.
    // Reading state before that returns the previous frame's facts.
    //
    // `commited` is a BehaviorSubject: subscribing replays the current value
    // synchronously. Resolving on the first emission would return without
    // waiting for anything, so the replayed value is skipped explicitly.
    // The guard is "emitted during the subscribe call", not "payload differs":
    // the payload is a timestamp and two commits in the same millisecond can
    // carry equal values.
    function settleFrame() {
      const canvas3d = plugin.canvas3d;
      const commited = canvas3d && canvas3d.commited;
      if (!commited || typeof commited.subscribe !== 'function'
          || typeof canvas3d.requestDraw !== 'function') {
        throw new Error('Mol* 未提供提交事件，无法确认视图已落定。');
      }
      return new Promise((resolve, reject) => {
        let replaying = true;
        let subscription = null;
        let timer = null;
        const finish = (settle, value) => {
          if (subscription) {
            subscription.unsubscribe();
            subscription = null;
          }
          if (timer !== null) {
            clearTimeout(timer);
            timer = null;
          }
          settle(value);
        };
        timer = setTimeout(
          () => finish(reject, new Error('Mol* 未在预期时间内完成一次提交。')),
          SETTLE_TIMEOUT_MS,
        );
        subscription = commited.subscribe(() => {
          if (replaying) return;
          finish(resolve);
        });
        replaying = false;
        canvas3d.requestDraw();
      });
    }

    // Read the structure tree rather than managers.structure.hierarchy.current:
    // hierarchy sync is skipped while a dataTransaction is open, so the manager
    // view can still be stale right after applyPreset resolves.
    function derivedSymmetryPreset() {
      const cells = plugin.state?.data?.cells;
      if (!cells || typeof cells.forEach !== 'function') return null;
      let preset = null;
      cells.forEach(cell => {
        if (preset !== null) return;
        const transform = cell && cell.transform;
        const id = transform?.transformer?.id || '';
        if (!String(id).endsWith('structure-from-model')) return;
        preset = presetFromStructureType(transform.params && transform.params.type);
      });
      return preset;
    }

    // Fail closed: without a real Mol* provider, do not touch the hierarchy —
    // a wrong guess here would clear the loaded structure.
    function resolveSymmetryProvider(mapped, label) {
      const manager = plugin.managers?.structure?.hierarchy;
      const builder = plugin.builders?.structure?.hierarchy;
      if (!manager || typeof manager.applyPreset !== 'function') {
        throw new Error(`无法展开${label}：Mol* 未提供结构层级 applyPreset。`);
      }
      const trajectories = Array.isArray(manager.current?.trajectories)
        ? manager.current.trajectories.filter(Boolean)
        : [];
      if (!trajectories.length) {
        throw new Error(`无法展开${label}：当前没有已加载的结构轨迹。`);
      }
      const presetId = SYMMETRY_PRESET_IDS[mapped];
      let provider = null;
      if (typeof builder?.resolveProvider === 'function') {
        provider = builder.resolveProvider(mapped) || builder.resolveProvider(presetId) || null;
      }
      if (!provider || typeof provider.apply !== 'function') {
        const collections = [
          builder, builder?.presets, manager.presets,
          global.molstar?.PresetTrajectoryHierarchy,
        ];
        for (const collection of collections) {
          if (!collection || typeof collection !== 'object') continue;
          if (typeof collection[mapped]?.apply === 'function') {
            provider = collection[mapped];
            break;
          }
          const match = Object.values(collection).find(
            item => item && item.id === presetId && typeof item.apply === 'function',
          );
          if (match) { provider = match; break; }
        }
      }
      if (!provider || typeof provider.apply !== 'function') {
        throw new Error(
          `无法展开${label}：找不到 Mol* 的 ${mapped}（${presetId}）预设。未执行切换，以免清空当前结构。`,
        );
      }
      return { manager, trajectories, provider };
    }

    async function applySymmetryPreset(preset) {
      const mapped = SYMMETRY_PRESETS[preset];
      const label = SYMMETRY_PRESET_LABELS[preset] || String(preset || '');
      const { manager, trajectories, provider } = resolveSymmetryProvider(mapped, label);
      // Build the DEPOSITED model (every crystallographic copy) for 「模型」, the
      // same override molstar.html's loadDepositedModel uses. The manager forwards
      // this params object straight to builders.structure.hierarchy.applyPreset.
      // Without it the `default` provider yields biological Assembly 1 — a subset
      // that can hide the atom a clash scan wins on. bio_assembly passes no
      // override, so it keeps that Assembly-1 default on purpose.
      const presetOptions = preset === 'model'
        ? { structure: { name: 'model', params: {} } }
        : undefined;
      try {
        await manager.applyPreset(trajectories, provider, presetOptions);
      } catch (error) {
        const message = error && error.message ? String(error.message) : String(error);
        if (/crystal symmetry|isApplicable|no crystal|spacegroup/i.test(message)) {
          throw new Error(`无法展开${label}：该结构不含晶体对称。`);
        }
        throw new Error(`无法展开${label}：${message}`);
      }
      // applyPreset runs inside plugin.dataTransaction with no options, and
      // State.transaction's catch reverts the tree, logs to the console, and
      // falls through — so the await above resolves even when nothing was
      // rebuilt, which also makes the catch above unreachable for anything
      // thrown inside the transaction. Verify against the state tree instead of
      // trusting the resolve: recording an intent that provably did not happen
      // is worse than an error, and it would leave the panel marking the old
      // preset while the trajectory claims the new one.
      if (derivedSymmetryPreset() !== preset) {
        throw new Error(`无法展开${label}：Mol* 未重建该预设，结构可能不含晶体对称。`);
      }
      // The rebuild already dropped overlay nodes. Drop the ledger without
      // hierarchy.remove on stale refs.
      residueOverlays = [];
      const overlays = measurementsApi();
      if (overlays) overlays.forgetResidueOverlays?.();
    }

    function structureEntries() {
      const current = hierarchy?.current?.structures;
      if (!Array.isArray(current)) return [];
      return current.map((ref, index) => ({
        structure_id: `structure-${index}`,
        index,
        label: String(ref?.cell?.obj?.label || `结构 ${index}`),
        visible: !ref?.cell?.state?.isHidden,
        element_count: Number(ref?.cell?.obj?.data?.elementCount || 0),
        ref,
      }));
    }

    function structureDisplayLabel(entry) {
      const raw = String(entry?.label || '').trim();
      const assembly = /^Assembly\s+(\d+)$/i.exec(raw);
      if (assembly) return `结构 ${assembly[1]}`;
      if (!raw) return `结构 ${Number(entry?.index) + 1}`;
      return raw;
    }

    function structureAlreadyIsolated(entries, entry) {
      return Boolean(entries.length) && entries.every(item => (
        item === entry ? item.visible !== false : item.visible === false
      ));
    }

    // Carbohydrate is its own dimension, not part of polymer. The auto preset
    // draws sugars with the SNFG carbohydrate-symbol representation, whose block
    // geometry encloses the residue's atom spheres: the pick ray hits the symbol,
    // and measurement — which requires an atom — refuses. Folding sugars into
    // polymer (as 'branched' used to be) left no way to clear that symbol short of
    // hiding the whole backbone, so a glycan residue's atoms were unreachable.
    const COMPONENT_TYPES = Object.freeze(['water', 'ligand', 'polymer', 'ion', 'carbohydrate']);
    const REPRESENTATION_STYLES = Object.freeze(['cartoon', 'ball-and-stick', 'spacefill']);
    // Public name for the SNFG cube. Mol* registers that representation as
    // 'carbohydrate'; representationTypeName maps the registry key back.
    function molstarRepresentationType(style) {
      return style === 'carbohydrate-symbol' ? 'carbohydrate' : style;
    }

    function componentTypeName(ref) {
      const raw = ref?.cell?.params?.values?.type?.name
        || ref?.cell?.transform?.params?.type?.name
        || ref?.cell?.obj?.type?.name
        || '';
      const label = String(ref?.cell?.obj?.label || ref?.label || '');
      const lowered = String(raw || '').toLowerCase();
      if (COMPONENT_TYPES.includes(lowered)) return lowered;
      // Mol* names the sugar component 'branched'; the expert-facing word is
      // 糖类 / carbohydrate. Reported as its own type so its SNFG symbol layer can
      // be cleared without touching the protein.
      if (lowered === 'branched') return 'carbohydrate';
      if (/water/i.test(label)) return 'water';
      if (/ligand/i.test(label)) return 'ligand';
      if (/carbohydrate|glycan|branched/i.test(label)) return 'carbohydrate';
      if (/polymer/i.test(label)) return 'polymer';
      if (/ion/i.test(label)) return 'ion';
      return 'other';
    }

    function representationTypeName(rep) {
      const name = rep?.cell?.transform?.params?.type?.name
        || rep?.cell?.params?.values?.type?.name
        || rep?.cell?.obj?.type?.name
        || '';
      const lowered = String(name).toLowerCase();
      if (REPRESENTATION_STYLES.includes(lowered)) return lowered;
      if (lowered === 'ballandstick' || lowered === 'ball_and_stick') return 'ball-and-stick';
      if (lowered === 'carbohydrate' || lowered === 'carbohydrate-symbol') {
        return 'carbohydrate-symbol';
      }
      return lowered || 'other';
    }

    function representationName(ref) {
      const reps = Array.isArray(ref && ref.representations) ? ref.representations : [];
      const names = reps.map(representationTypeName);
      // Auto-preset often stacks ball-and-stick under the SNFG symbol. The cube
      // is what the expert sees and what blocks atom picking, so it wins.
      const snfg = names.find(name => name === 'carbohydrate-symbol');
      if (snfg) return snfg;
      return names[0] || 'other';
    }

    // Components of the ACTIVE scope. While a chain is isolated that is the
    // chain subtree (the root preset components are hidden and off limits), so
    // 显示组分 / 切换表示 and every "当前结构没有 X 组件" guard speak about the chain.
    function componentEntries() {
      if (chainScope && chainScope.isolatedChainId()) {
        return chainScope.scopeComponents().map(item => ({
          type: item.type,
          label: item.label,
          visible: item.visible,
          representation: representationName(item.ref),
          ref: item.ref,
        }));
      }
      const collected = [];
      const structures = hierarchy?.current?.structures;
      if (Array.isArray(structures)) {
        for (const structure of structures) {
          const nested = structure && structure.components;
          if (Array.isArray(nested)) {
            collected.push(...nested.filter(ref => !String(ref?.cell?.obj?.label || '').startsWith('sv-chain:')));
          }
        }
      }
      // Older fakes hung components on hierarchy.current; real Mol* does not.
      if (!collected.length && Array.isArray(hierarchy?.current?.components)) {
        collected.push(...hierarchy.current.components);
      }
      return collected.map(ref => ({
        type: componentTypeName(ref),
        label: String(ref?.cell?.obj?.label || ''),
        visible: !ref?.cell?.state?.isHidden,
        representation: representationName(ref),
        ref,
      }));
    }

    function snapshotComponents() {
      return componentEntries().map(item => ({
        type: item.type,
        visible: item.visible,
        representation: item.representation,
      }));
    }

    function componentsByType(type) {
      return componentEntries().filter(item => item.type === type);
    }

    // Entry default is every auto-preset class shown. Mol* may load a class
    // hidden; snapshot after revealing so reset returns to all-on, and recover
    // (reset + replay) can still re-hide what the expert recorded.
    if (typeof hierarchy?.toggleVisibility === 'function') {
      for (const type of COMPONENT_TYPES) {
        for (const item of componentsByType(type)) {
          if (!item.visible) hierarchy.toggleVisibility([item.ref], 'show');
        }
      }
    }
    const initialComponents = snapshotComponents();

    // Which residue the sequence bar should mark as current. Focus replaces, so
    // this is one value, not a list; viewer_reset clears it along with the focus.
    let focusedResidue = null;
    let atomLabels = { visible: false, atom_ids: [] };

    function atomLabelsState() {
      return {
        visible: Boolean(atomLabels.visible),
        atom_ids: Array.isArray(atomLabels.atom_ids) ? atomLabels.atom_ids.slice() : [],
      };
    }

    function clearAtomLabelOverlay() {
      atomLabels = { visible: false, atom_ids: [] };
      const api = measurementsApi();
      if (api && typeof api.clearAtomLabels === 'function') api.clearAtomLabels();
    }

    async function applyAtomLabels(args) {
      const visible = Boolean(args && args.visible);
      if (!visible) {
        clearAtomLabelOverlay();
        return {
          replay_input: { visible: false },
          result: { expert_summary: '已隐藏原子序号', labels: [] },
          state: getState(),
        };
      }
      const ids = normalizeAtomIds(args && args.atom_ids);
      if (!ids.length) throw new Error('需要原子序号。');
      if (ids.length > MAX_ATOM_LABELS) {
        throw new Error(`一次最多标 ${MAX_ATOM_LABELS} 个原子序号。`);
      }
      const records = parseAtomSerials(await fetchLoadedStructureText(), loadedStructureFile());
      const bySerial = new Map(records.map(item => [item.serial, item]));
      const labels = [];
      for (const id of ids) {
        const hit = bySerial.get(id);
        if (!hit) throw new Error(`找不到原子序号 ${id}。`);
        labels.push({ serial: id, xyz: hit.xyz.slice() });
      }
      const api = measurementsApi();
      if (!api || typeof api.setAtomLabels !== 'function') {
        throw new Error('无法显示原子序号：测量模块不可用。');
      }
      api.setAtomLabels(labels);
      atomLabels = { visible: true, atom_ids: ids.slice() };
      return {
        replay_input: { visible: true, atom_ids: ids.slice() },
        result: {
          labels,
          expert_summary: `已标原子序号：${ids.join('、')}`,
        },
        state: getState(),
      };
    }

    // 「显示链」: see molstar-chain-scope.js. The adapter only frames the camera
    // around the module: read the five semantic numbers first, switch scope,
    // then re-apply the SAME numbers so the expert's pose is kept relative to
    // what is now on screen (isolating shrinks the visible sphere; zoom and
    // pan are measured against it).
    async function applyChainVisibility(args) {
      if (!chainScope) throw new Error('显示链模块不可用。');
      const only = args && args.only != null ? String(args.only) : '';
      if (!only) throw new Error('only 是必需的。');
      let framing = lastSemanticCamera
        || {azimuth_deg: 0, elevation_deg: 0, zoom: 1, pan_right: 0, pan_up: 0};
      try { framing = semanticCamera(); } catch (error) {
        if (!isEmptySphereError(error)) throw error;
      }
      if (only === '*') await chainScope.showAll();
      else await chainScope.isolate(only);
      await holdSemanticCamera(framing, {waitForSphere: true});
    }

    async function applyComponentVisibility(patch) {
      const keys = COMPONENT_TYPES.filter(type => Object.prototype.hasOwnProperty.call(patch, type));
      if (!keys.length) {
        throw new Error('water, ligand, polymer, ion, or carbohydrate is required.');
      }
      for (const type of keys) {
        const entries = componentsByType(type);
        if (patch[type] === true && !entries.length) {
          const label = type === 'water' ? '水分子'
            : (type === 'carbohydrate' ? '糖类' : type);
          throw new Error(`当前结构没有${label}组件。`);
        }
      }
      const unchanged = keys.every(type => {
        const entries = componentsByType(type);
        if (!entries.length && patch[type] === false) return true;
        return entries.length && entries.every(item => item.visible === patch[type]);
      });
      if (unchanged) {
        throw new Error('已经是当前组件显隐，未再次切换。');
      }
      // Hide/show queues no renderables, so boundingSphereVisible stays stale
      // (or goes empty) until a camera reset. Reading the sphere first would
      // fail-close and leave the expert unable to click 显示所有.
      let framing = lastSemanticCamera || {azimuth_deg: 0, elevation_deg: 0, zoom: 1, pan_right: 0, pan_up: 0};
      try { framing = semanticCamera(); } catch (error) {
        if (!isEmptySphereError(error)) throw error;
      }
      if (typeof hierarchy?.toggleVisibility === 'function') {
        for (const type of keys) {
          for (const item of componentsByType(type)) {
            hierarchy.toggleVisibility([item.ref], patch[type] ? 'show' : 'hide');
          }
        }
      }
      const anyVisible = COMPONENT_TYPES.some(
        type => componentsByType(type).some(item => item.visible),
      );
      if (anyVisible) {
        await holdSemanticCamera(framing, {waitForSphere: true});
      }
    }

    async function restoreEntryComponents() {
      if (typeof hierarchy?.toggleVisibility !== 'function') return;
      for (const snapshot of initialComponents) {
        for (const item of componentsByType(snapshot.type)) {
          if (item.representation !== snapshot.representation) {
            await applyRepresentationToRefs([item.ref], snapshot.representation);
          }
        }
      }
      restoreComponentVisibility(initialComponents);
    }

    // addRepresentation can paint a class whose isHidden flag never flipped,
    // so 显示组分 still says "off" while the canvas shows it. Re-apply the
    // snapshot; a hide on an already-hidden node is not enough — show then
    // hide is what actually takes the leaked draw off screen.
    function restoreComponentVisibility(snapshot) {
      if (typeof hierarchy?.toggleVisibility !== 'function') return;
      for (const snap of snapshot) {
        for (const item of componentsByType(snap.type)) {
          if (snap.visible) {
            hierarchy.toggleVisibility([item.ref], 'show');
          } else {
            if (!item.visible) hierarchy.toggleVisibility([item.ref], 'show');
            hierarchy.toggleVisibility([item.ref], 'hide');
          }
        }
      }
    }

    async function applyRepresentationToRefs(refs, style) {
      const manager = plugin.managers?.structure?.component;
      if (!manager || typeof manager.addRepresentation !== 'function') {
        throw new Error('无法切换表示：Mol* 未提供 component.addRepresentation。');
      }
      if (!refs.length) return;
      // Mol* 4.5 StructureComponentManager:
      //   removeRepresentations(components: ReadonlyArray<StructureComponentRef>, pivot?)
      //   addRepresentation(components: ReadonlyArray<StructureComponentRef>, type: string)
      // Passing a single ref is not iterable; passing `{type:{name}}` is not a
      // registry key. Swallowing either miss leaves cartoon in place and still
      // reports success.
      if (typeof manager.removeRepresentations === 'function') {
        await manager.removeRepresentations(refs);
      }
      await manager.addRepresentation(refs, molstarRepresentationType(style));
    }

    function residueOverlaySnapshot() {
      return residueOverlays.map(item => ({...item}));
    }

    async function clearResidueOverlays() {
      residueOverlays = [];
      const api = measurementsApi();
      if (api && typeof api.clearResidueOverlays === 'function') {
        await api.clearResidueOverlays();
      }
    }

    function residueIdentity(args) {
      const chainId = String(args && args.chain_id || '');
      const seqId = Number(args && args.seq_id);
      if (!chainId || !Number.isInteger(seqId)) {
        throw new Error('残基需要 chain_id 和 seq_id。');
      }
      return {
        chain_id: chainId,
        seq_id: seqId,
        comp_id: String(args && args.comp_id || ''),
        icode: String(args && args.icode || ''),
      };
    }

    async function armResidueRepresentation(style) {
      if (!REPRESENTATION_STYLES.includes(style)) {
        throw new Error('style must be cartoon, ball-and-stick, or spacefill.');
      }
      const api = measurementsApi();
      if (!api || typeof api.pickResidue !== 'function') {
        throw new Error('无法点选残基：Mol* 测量模块不可用。');
      }
      residuePickFraming = semanticCamera();
      setSvMcpPick('1');
      setLegacyInputMode('track');
      try {
        if (typeof api.setMode === 'function') await api.setMode(null);
        api.pickResidue(picked => {
          setSvMcpPick('0');
          lockCanvasInput();
          const notify = global.__ScienceVisionMolstarResiduePicked;
          if (typeof notify === 'function') notify({...picked, style});
        });
      } catch (error) {
        residuePickFraming = null;
        setSvMcpPick('0');
        lockCanvasInput();
        throw error;
      }
    }

    async function commitResidueRepresentation(args, style) {
      const identity = residueIdentity(args);
      const api = measurementsApi();
      if (!api || typeof api.applyResidueRepresentation !== 'function') {
        throw new Error('无法给残基加表示：Mol* 测量模块不可用。');
      }
      const capturedFraming = residuePickFraming;
      residuePickFraming = null;
      setSvMcpPick('0');
      lockCanvasInput();
      const framing = capturedFraming || semanticCamera();
      await api.applyResidueRepresentation(identity, style);
      residueOverlays = residueOverlays.filter(item => (
        item.chain_id !== identity.chain_id
        || item.seq_id !== identity.seq_id
        || item.icode !== identity.icode
      ));
      residueOverlays.push({...identity, style});
      await holdSemanticCamera(framing);
      return {
        target: 'residue',
        style,
        chain_id: identity.chain_id,
        seq_id: identity.seq_id,
        comp_id: identity.comp_id,
        icode: identity.icode,
        commit: true,
      };
    }

    async function applyRepresentation(target, style) {
      const carbohydrateSymbol = style === 'carbohydrate-symbol' && target === 'carbohydrate';
      if (!carbohydrateSymbol && !REPRESENTATION_STYLES.includes(style)) {
        throw new Error('style must be cartoon, ball-and-stick, or spacefill.');
      }
      if (target !== 'polymer' && target !== 'ligand' && target !== 'all'
          && target !== 'carbohydrate') {
        throw new Error('target must be polymer, ligand, carbohydrate, or all.');
      }
      // cartoon draws a backbone trace; a ligand or a sugar has none.
      if (style === 'cartoon' && (target === 'ligand' || target === 'carbohydrate')) {
        throw new Error(
          target === 'ligand' ? '配体不能使用 cartoon 表示。' : '糖类不能使用 cartoon 表示。',
        );
      }
      const wanted = target === 'all'
        ? (style === 'cartoon' ? ['polymer'] : ['polymer', 'ligand', 'carbohydrate'])
        : [target];
      const refs = [];
      for (const type of wanted) {
        const entries = componentsByType(type);
        if (!entries.length && target !== 'all') {
          throw new Error(`当前结构没有${type === 'carbohydrate' ? '糖类' : type}组件。`);
        }
        for (const item of entries) refs.push(item.ref);
      }
      if (!refs.length) throw new Error('当前结构没有可改表示的组件。');
      const already = wanted.every(type => (
        componentsByType(type).every(item => item.representation === style)
      ));
      const overlays = residueOverlaySnapshot();
      if (already && !overlays.length) {
        const label = style === 'ball-and-stick' ? '球棍'
          : style === 'carbohydrate-symbol' ? '糖类符号' : style;
        throw new Error(`已经是${label}，未再次切换。`);
      }
      const visibilitySnapshot = snapshotComponents();
      const framing = semanticCamera();
      setSvMcpPick('0');
      lockCanvasInput();
      residuePickFraming = null;
      if (measurementsApi() && typeof measurementsApi().setMode === 'function') {
        await measurementsApi().setMode(null);
      }
      await clearResidueOverlays();
      if (!already) await applyRepresentationToRefs(refs, style);
      restoreComponentVisibility(visibilitySnapshot);
      await holdSemanticCamera(framing);
    }

    function measurementsApi() {
      return global.__svMolstarMeasurements || null;
    }

    function lockCanvasInput() {
      const input = plugin.canvas3d && plugin.canvas3d.input;
      if (input && typeof input.setMode === 'function') {
        try { input.setMode('none'); } catch (_error) {}
      }
    }

    function getState() {
      const measurementState = measurementsApi()?.getState?.();
      return {
        viewer: 'molstar',
        view: 'iso',
        camera: cameraForState(),
        structures: structureEntries().map(item => ({
          structure_id: item.structure_id,
          index: item.index,
          label: item.label,
          visible: item.visible,
          element_count: item.element_count,
        })),
        measurements: measurementState?.measurements || { distances: 0, angles: 0, dihedrals: 0 },
        symmetry_preset: derivedSymmetryPreset(),
        components: componentEntries().map(item => ({
          type: item.type,
          label: item.label,
          visible: item.visible,
          representation: item.representation,
        })),
        chains: chainScope ? chainScope.list() : [],
        focused_residue: focusedResidue ? {...focusedResidue} : null,
        ...(atomLabels.visible ? { atom_labels: atomLabelsState() } : {}),
      };
    }

    function visibleSphere() {
      const sphere = plugin.canvas3d?.boundingSphereVisible
        || plugin.canvas3d?.boundingSphere;
      const center = asXYZ(sphere && sphere.center);
      const radius = Number(sphere && sphere.radius);
      if (!center || !Number.isFinite(radius) || !(radius > 0)) {
        throw new Error('无法取景：Mol* 当前没有可见内容的包围球。');
      }
      return { center, radius };
    }

    function fitDistance(radius) {
      if (typeof camera.getTargetDistance !== 'function') {
        throw new Error('无法取景：Mol* 未提供 getTargetDistance。');
      }
      const distance = Number(camera.getTargetDistance(Math.max(radius, MIN_FIT_RADIUS)));
      if (!Number.isFinite(distance) || !(distance > 0)) {
        throw new Error('无法取景：Mol* 返回的拟合距离无效。');
      }
      return distance;
    }

    // Read the semantic camera back out of the live camera. Nothing is tracked:
    // the five numbers are derived from the actual pose and the actual scene.
    //
    // The pose is measured about snapshot.target -- the real look-at point Mol*
    // holds, i.e. the one requestSemanticCamera last wrote -- NOT the visible
    // sphere centre. That switch is what makes pan expressible: pan lives in
    // `target - centre`, projected onto the camera's own right/up axes and
    // normalised by radius. Angles and zoom come from `position - target`, so
    // read and write stay exact inverses: the camera the state reports can be
    // re-applied without moving anything.
    //
    // The residual along the view axis, dot(target - centre, fwd), is DROPPED by
    // this projection. Right after any re-frame target sits in the pure right/up
    // plane through the centre, so the residual is 0 and the round trip is exact.
    // It only turns non-zero when visible content changes without a re-frame --
    // sv3d_measure grows boundingSphereVisible while the expert picks atoms -- so
    // §3.4a re-frames on commit to zero it again. At pan 0 (target == centre)
    // every term below reduces byte-for-byte to the old sphere-centre read, so
    // legacy three-field trajectories replay unchanged.
    // The sphere zoom and pan are measured against. Normally the whole visible
    // scene, so zoom=1 means "the molecule fits". While a residue is focused it
    // is that residue's environment instead, so zoom/orbit/pan continue from the
    // focused view rather than snapping back to whole-molecule framing — which is
    // what made 「定位残基 → 拖缩放」 jump back out.
    //
    // Matches Mol*'s own focus framing (camera.focusSphere): radius + extraRadius,
    // floored at minRadius, so zoom=1 here means the same thing focusLoci aimed at.
    const FOCUS_EXTRA_RADIUS_A = 4;
    const FOCUS_MIN_RADIUS_A = 5;

    function focusReferenceSphere() {
      if (!focusedResidue) return null;
      const api = measurementsApi();
      if (!api || typeof api.boundingSphereForResidue !== 'function') return null;
      let sphere = null;
      try {
        sphere = api.boundingSphereForResidue(focusedResidue);
      } catch (_error) {
        return null;
      }
      const center = asXYZ(sphere && sphere.center);
      const radius = Number(sphere && sphere.radius);
      if (!center || !Number.isFinite(radius)) return null;
      // Round the reference sphere itself, not just the derived pose. Replay
      // recomputes this sphere from the rebuilt structure; without rounding, a
      // last-digit difference in the atom-position average would shift zoom/pan
      // and the strict state comparison would fail-close the panel. Rounding at
      // the same precision as the camera makes the reference reproducible, so the
      // safety net stays strict instead of being loosened for focus.
      return {
        center: center.map(roundCamera),
        radius: roundCamera(Math.max(radius + FOCUS_EXTRA_RADIUS_A, FOCUS_MIN_RADIUS_A)),
      };
    }

    // No focus -> visibleSphere() exactly as before, so every number a legacy
    // draft recorded is reproduced bit-for-bit (initial_state_sha256 depends on it).
    function referenceSphere() {
      return focusReferenceSphere() || visibleSphere();
    }

    function semanticCamera() {
      const snapshot = camera.getSnapshot();
      const sphere = referenceSphere();
      const position = Array.from(snapshot.position, Number);
      const target = asXYZ(snapshot.target) || sphere.center;
      const toTarget = vecSub(target, position);
      const distance = vecLength(toTarget);
      if (!(distance > 1e-12)) throw new Error('Camera basis is degenerate.');
      const fwd = vecScale(toTarget, 1 / distance);
      const dir = vecScale(fwd, -1); // target -> camera, the world convention
      const polarDeg = Math.acos(
        Math.min(1, Math.max(-1, dir[1])),
      ) * 180 / Math.PI;
      const basis = cameraRightUp(fwd);
      const delta = vecSub(target, sphere.center);
      let panRight = 0;
      let panUp = 0;
      if (basis) {
        panRight = vecDot(delta, basis.right) / sphere.radius;
        panUp = vecDot(delta, basis.up) / sphere.radius;
      }
      const pose = {
        azimuth_deg: roundCamera(Math.atan2(dir[0], dir[2]) * 180 / Math.PI),
        elevation_deg: roundCamera(90 - polarDeg),
        zoom: roundCamera(fitDistance(sphere.radius) / distance),
        pan_right: roundCamera(panRight),
        pan_up: roundCamera(panUp),
      };
      lastSemanticCamera = pose;
      return pose;
    }

    function requireInitialSemanticCamera() {
      if (initialSemanticCamera === null) initialSemanticCamera = semanticCamera();
      return initialSemanticCamera;
    }

    // Apply through Mol*'s own post-commit hook rather than camera.setState:
    // the hook runs after scene.commit(), so the bounding sphere it frames
    // against is the refreshed one, and Mol* sets radiusMax / minFar / the
    // auto min-max distances for us. The bare setState never did, which is a
    // second cause of the "view is suddenly enormous" symptom.
    // applyPreset / model load clear boundingSphereVisible until the next
    // commit. Framing against that empty sphere is a false fail-closed.
    async function ensureVisibleSphere() {
      try { return visibleSphere(); } catch (error) {
        await settleFrame();
        return visibleSphere();
      }
    }

    function isEmptySphereError(error) {
      const message = error && error.message ? String(error.message) : '';
      return message.includes('包围球');
    }

    function cameraForState() {
      try {
        return semanticCamera();
      } catch (error) {
        if (!isEmptySphereError(error)) throw error;
        return lastSemanticCamera || {azimuth_deg: 0, elevation_deg: 0, zoom: 1, pan_right: 0, pan_up: 0};
      }
    }

    function requestSemanticCamera(intent, options = {}) {
      const canvas3d = plugin.canvas3d;
      if (!canvas3d || typeof canvas3d.requestCameraReset !== 'function') {
        throw new Error('无法取景：Mol* 未提供 requestCameraReset。');
      }
      const azimuthDeg = requireFiniteNumber(intent.azimuth_deg, 'azimuth_deg');
      const elevationDeg = requireFiniteNumber(intent.elevation_deg, 'elevation_deg');
      const zoom = requireFiniteNumber(intent.zoom, 'zoom');
      if (!(zoom > 0)) throw new Error('zoom must be greater than zero.');
      // Pan is optional so legacy three-field intents (and sv3d_set_camera heads
      // recorded before pan existed) frame about the centre exactly as before.
      const panRight = intent.pan_right == null ? 0 : requireFiniteNumber(intent.pan_right, 'pan_right');
      const panUp = intent.pan_up == null ? 0 : requireFiniteNumber(intent.pan_up, 'pan_up');
      // A genuinely empty scene (orbit/zoom with nothing on screen) still fails
      // closed. After a preset rebuild the visible sphere is often empty until
      // a camera reset is queued — that reset is what refreshes the sphere, so
      // requiring one first is the chicken-and-egg that made 「模型」 fail.
      if (!options.allowEmptySphere) {
        fitDistance(visibleSphere().radius);
      }
      canvas3d.requestCameraReset({
        durationMs: 0,
        snapshot: (scene, activeCamera) => {
          // Write against the same sphere the read side measures, or the two stop
          // being inverses and a re-applied pose would move the camera. While a
          // residue is focused that is its environment; otherwise the live scene.
          const focusSphere = focusReferenceSphere();
          const sphere = focusSphere || (scene && scene.boundingSphereVisible);
          const center = asXYZ(sphere && sphere.center);
          const radius = Number(sphere && sphere.radius);
          if (!center || !Number.isFinite(radius) || !(radius > 0)) return null;
          const distance = Number(
            activeCamera.getTargetDistance(Math.max(radius, MIN_FIT_RADIUS)),
          ) / zoom;
          const direction = directionFromAngles(azimuthDeg, elevationDeg);
          // Offset the look-at from the centre along the camera's own right/up
          // axes, radius-normalised, so the same pan keeps the same on-screen
          // displacement as the structure scales (§3.1 forward framing). fwd is
          // where the camera looks (-direction); the sign is fixed so pan_right
          // > 0 moves the look-at screen-right and the structure slides left.
          const fwd = vecScale(direction, -1);
          const basis = cameraRightUp(fwd);
          const target = basis
            ? vecAdd(
                center,
                vecAdd(
                  vecScale(basis.right, panRight * radius),
                  vecScale(basis.up, panUp * radius),
                ),
              )
            : center;
          return {
            target,
            position: vecAdd(target, vecScale(direction, distance)),
            up: [0, 1, 0],
          };
        },
      });
    }

    // Request a framing and do not return until the viewer actually reports it.
    //
    // requestSemanticCamera frames against the visible sphere as it stands when the
    // reset is consumed, but a scene change is not finished when the call that
    // started it resolves: Mol* keeps building the expanded representations while
    // its own render loop commits every frame, so the sphere grows afterwards with
    // no call from us. A single request-then-settle-then-read therefore records a
    // pose nobody asked for — a super cell step whose intent was {0, 0, 1} recorded
    // {6.075, -0.277, 2.169}, angles measured about a centre that had since moved
    // and a zoom against a radius 2.17x larger. The screen was right, because a
    // later frame did apply the re-frame; only the number written down was wrong.
    // And that number is the one the trajectory head snaps to, so the next page
    // load replayed the step and buried the camera inside the surface.
    //
    // Each pass frames against a sphere nearer its final size, so this converges as
    // soon as the scene stops moving. Bounded, so a scene that never settles fails
    // the step instead of hanging it — and fails loudly rather than recording a lie.
    function clashCameraDiag(tag, extra) {
      const snap = camera.getSnapshot ? camera.getSnapshot() : null;
      let sphere = null;
      try {
        const live = plugin.canvas3d && (
          plugin.canvas3d.boundingSphereVisible || plugin.canvas3d.boundingSphere
        );
        sphere = {
          center: asXYZ(live && live.center),
          radius: Number(live && live.radius),
        };
      } catch (_error) {}
      let semantic = null;
      try { semantic = semanticCamera(); } catch (error) {
        semantic = { error: String(error && error.message || error) };
      }
      const payload = {
        tag,
        position: asXYZ(snap && snap.position),
        target: asXYZ(snap && snap.target),
        up: asXYZ(snap && snap.up),
        radius: snap && snap.radius,
        radiusMax: snap && snap.radiusMax,
        fov: snap && snap.fov,
        mode: snap && snap.mode,
        snapKeys: snap ? Object.keys(snap) : [],
        sphere,
        semantic,
        extra: extra || null,
      };
      try { global.console.info('[sv-clash-camera]', payload); } catch (_error) {}
      return payload;
    }

    async function holdRawCameraSnapshot(snapshot) {
      // Byte-for-byte restore of Mol*'s own pose, including radius / radiusMax.
      // requestCameraReset re-fits those from the live bounding sphere, so a
      // distance annotation that grew the sphere still pulled the camera in
      // even after position/target/up were restored. setState writes the
      // captured snapshot as-is and does not re-frame.
      if (!snapshot || typeof camera.setState !== 'function') {
        throw new Error('无法还原视角：相机快照不完整。');
      }
      clashCameraDiag('restore.setState.before', {
        capturedRadius: snapshot.radius,
        capturedRadiusMax: snapshot.radiusMax,
      });
      camera.setState(snapshot, 0);
      clashCameraDiag('restore.setState.after');
      await settleFrame();
      clashCameraDiag('restore.settle.after');
    }

    // Wait out an interpolated camera move (camera.focusLoci). Unlike
    // holdSemanticCamera there is no requested pose to converge on: Mol* picks
    // the destination itself, so the only available signal is the pose going
    // still. Two identical committed frames in a row means the animation landed.
    async function settleCameraAnimation() {
      const deadline = Date.now() + REFRAME_BUDGET_MS;
      const poseOf = () => {
        const snapshot = camera.getSnapshot();
        return [
          ...Array.from(snapshot.position, Number),
          ...Array.from(snapshot.target, Number),
          Number(snapshot.radius),
        ].join(',');
      };
      let previous = null;
      let still = 0;
      for (;;) {
        await settleFrame();
        const pose = poseOf();
        still = pose === previous ? still + 1 : 0;
        previous = pose;
        if (still >= 1) return;
        if (Date.now() > deadline) return;
      }
    }

    async function holdSemanticCamera(intent, options = {}) {
      const waitForSphere = Boolean(options.waitForSphere);
      // Recover/history replay walks every recorded step onto a freshly loaded
      // viewer. Live recording must still wait for the sphere to finish growing;
      // replay only needs the mutation applied and one committed frame, otherwise
      // a 待返修 trajectory stalls the expert for 1.5s per step.
      if (options.snap || cameraSnap) {
        const deadline = Date.now() + (waitForSphere ? REFRAME_BUDGET_MS : 0);
        for (;;) {
          try {
            requestSemanticCamera(intent, {allowEmptySphere: waitForSphere});
            await settleFrame();
            semanticCamera();
            return;
          } catch (error) {
            if (!waitForSphere || !isEmptySphereError(error) || Date.now() >= deadline) {
              throw error;
            }
          }
        }
      }
      const deadline = Date.now() + REFRAME_BUDGET_MS;
      let previous = null;
      let unchanged = 0;
      for (;;) {
        let live = null;
        try {
          requestSemanticCamera(intent, {allowEmptySphere: waitForSphere});
          await settleFrame();
          live = semanticCamera();
        } catch (error) {
          if (!waitForSphere || !isEmptySphereError(error) || Date.now() >= deadline) {
            throw error;
          }
        }
        if (live && sameSemanticCamera(live, intent)) return;
        if (live) {
          // Missing the intent has two causes, and they need opposite handling.
          //
          // The scene is still moving: each pass frames against a sphere nearer
          // its final size, so the pose read back keeps CHANGING and converges.
          // That is what the budget below is for.
          //
          // Or the viewer will not go there at all. Mol* clamps the
          // camera-to-target offset to trackball.minDistance/maxDistance on
          // every frame, so a request past either end lands somewhere else and
          // then reports the SAME pose forever, however long we wait. Burning
          // the full budget on it is not patience, it is a stall: a live slider
          // drag applies serially, so dragging through an unreachable stretch of
          // the zoom track cost REFRAME_BUDGET_MS per step.
          //
          // So: bail once the pose has stopped changing. Requiring several
          // identical passes rather than one keeps a rebuild that merely pauses
          // between streamed chunks from being mistaken for a clamp.
          unchanged = previous && sameSemanticCamera(live, previous) ? unchanged + 1 : 0;
          previous = live;
          if (unchanged >= UNREACHABLE_REPEATS) {
            throw new Error('无法取景：Mol* 未接受该视角，通常是缩放已超出该结构的可视范围。');
          }
        }
        if (Date.now() >= deadline) {
          throw new Error('无法取景：Mol* 场景持续变化，无法确认视角已落定。');
        }
      }
    }

    let cameraSnap = false;
    // Whether this call is re-running a recorded operation. A live commit carries
    // recorded-looking atoms too -- the recorder synthesises it from the
    // just-finished measurement -- so the payload alone cannot tell the two
    // apart, and only a replay may redraw, or every live measurement doubles up.
    let replaying = false;

    async function callTool(name, args, context = {}) {
      const previousSnap = cameraSnap;
      const previousReplaying = replaying;
      cameraSnap = Boolean(context && context.replay);
      replaying = cameraSnap;
      try {
        return await dispatchTool(name, args);
      } finally {
        cameraSnap = previousSnap;
        replaying = previousReplaying;
      }
    }

    async function dispatchTool(name, args) {
      // Capture the entry pose before any mutation. Skip on reset: the first
      // call after a page load may be recover's viewer_reset, when Mol* has
      // not yet committed a visible sphere.
      if (name !== 'viewer_reset') {
        try { requireInitialSemanticCamera(); } catch (_) { /* empty sphere */ }
      }
      if (name === 'sv3d_orbit') {
        const replayInput = {
          azimuth_deg: requireFiniteNumber(args && args.azimuth_deg, 'azimuth_deg'),
          elevation_deg: requireFiniteNumber(args && args.elevation_deg, 'elevation_deg'),
        };
        // Carry the live zoom AND pan through: orbiting re-frames the same
        // look-at point, so the expert keeps watching whatever they panned to
        // (the view rotates about the panned target, not the sphere centre).
        const live = semanticCamera();
        await holdSemanticCamera({
          ...replayInput,
          zoom: live.zoom,
          pan_right: live.pan_right,
          pan_up: live.pan_up,
        });
        return { replay_input: replayInput, state: getState() };
      }
      if (name === 'viewer_zoom') {
        const replayInput = { zoom: requireFiniteNumber(args && args.zoom, 'zoom') };
        const live = semanticCamera();
        await holdSemanticCamera({
          azimuth_deg: live.azimuth_deg,
          elevation_deg: live.elevation_deg,
          zoom: replayInput.zoom,
          pan_right: live.pan_right,
          pan_up: live.pan_up,
        });
        return { replay_input: replayInput, state: getState() };
      }
      if (name === 'viewer_pan') {
        const replayInput = {
          pan_right: requireFiniteNumber(args && args.pan_right, 'pan_right'),
          pan_up: requireFiniteNumber(args && args.pan_up, 'pan_up'),
        };
        // Absolute, not accumulated: the request fully replaces the current
        // offset. Angles and zoom come from the live pose so panning alone
        // changes nothing else. This is what keeps the step idempotent on replay
        // (the BIM viewer_pan is a relative accumulator; Mol* must not join it).
        const live = semanticCamera();
        await holdSemanticCamera({
          azimuth_deg: live.azimuth_deg,
          elevation_deg: live.elevation_deg,
          zoom: live.zoom,
          pan_right: replayInput.pan_right,
          pan_up: replayInput.pan_up,
        });
        return { replay_input: replayInput, state: getState() };
      }
      if (name === 'sv3d_set_camera') {
        const next = args && args.camera;
        if (!next || typeof next !== 'object') throw new Error('camera is required.');
        await holdSemanticCamera(next);
        return { camera: next, state: getState() };
      }
      if (name === 'sv3d_measure') {
        const kind = args && args.kind;
        if (!MEASURE_KINDS.includes(kind)) {
          throw new Error('kind must be distance, angle, or dihedral.');
        }
        const api = measurementsApi();
        if (!api || typeof api.setMode !== 'function') {
          throw new Error('Mol* measurement tools are unavailable.');
        }
        if (args && args.commit === true) {
          setSvMcpPick('0');
          lockCanvasInput();
          // Consume the captured framing unconditionally, BEFORE the cancelled
          // check below can throw: a cancelled measurement's capture is dead (the
          // expert moves on and pans elsewhere), and leaving it here would leak
          // it into the next bare commit's re-frame.
          const capturedFraming = measureFraming;
          measureFraming = null;
          const fromRecord = Boolean(args.atoms && args.value);
          // Only a replay redraws: the interactive pick that drew this line
          // happened in a past session, and viewer_reset has since cleared every
          // measurement renderable. A live commit's line is already on screen.
          const redraw = fromRecord && replaying;
          const completed = fromRecord
            ? { kind, atoms: args.atoms, value: args.value }
            : api.consumeLastMeasurement?.();
          if (!completed) throw new Error('Measurement was cancelled before it completed.');
          if (redraw && typeof api.applyMeasurement === 'function') {
            // Redraw from the record BEFORE re-framing: drawing grows the
            // visible bounding sphere, and the framing below has to be the last
            // word for the same reason the live path re-frames after picking.
            // Best-effort on purpose -- an atom the current structure cannot
            // resolve must degrade to "no line" (today's behaviour), never throw
            // and fail-close the whole replay, which would brick the draft.
            try {
              await api.applyMeasurement({
                kind: completed.kind || kind,
                atoms: Array.isArray(completed.atoms) ? completed.atoms : [],
              });
            } catch (error) {
              console.warn('[molstar-mcp] measurement redraw failed', error);
            }
          }
          // Re-frame to the pose captured before the annotation grew the sphere
          // (§3.4a). Drawing the measurement enlarges boundingSphereVisible while
          // the expert picks atoms, and with pan the target no longer equals the
          // sphere centre -- so the view-axis residual the reverse read drops is
          // non-zero, and a round trip would drift. Replaying capturedFraming
          // pins the target back to `new centre + pure right/up offset`, zeroing
          // the residual and restoring exactness. Fall back to the live pose when
          // no start was captured (e.g. a bare commit on replay).
          await holdSemanticCamera(capturedFraming || semanticCamera());
          const replayInput = {
            kind: completed.kind || kind,
            atoms: Array.isArray(completed.atoms) ? completed.atoms : [],
            value: completed.value || '',
            commit: true,
          };
          return {
            replay_input: replayInput,
            result: {
              expert_summary: formatMeasureSummary(
                replayInput.kind,
                replayInput.atoms,
                replayInput.value,
              ),
            },
            state: getState(),
          };
        }
        // Capture the entry framing as a one-shot local before growing the scene.
        // The picking window itself does not re-frame (the view must not jump
        // while the expert works); commit above replays this to zero the residual.
        measureFraming = semanticCamera();
        setSvMcpPick('1');
        setLegacyInputMode('track');
        try {
          await api.setMode(kind);
        } catch (error) {
          // The measurement never started, so the capture above is dead — drop
          // it so it cannot leak into a later commit's re-frame.
          measureFraming = null;
          setSvMcpPick('0');
          lockCanvasInput();
          throw error;
        }
        if (typeof api.onCompleted === 'function') {
          api.onCompleted(completed => {
            setSvMcpPick('0');
            lockCanvasInput();
            const notify = global.__ScienceVisionMolstarMeasurementCommitted;
            if (typeof notify === 'function') notify(completed);
          });
        }
        // Record the intent only: `{kind}` re-enters the same measure mode on
        // replay. A `started: true` marker is a derived fact, it is declared by
        // no registry version, and additionalProperties is false on both the
        // browser validator and the server, so it made the step unrecordable.
        return { replay_input: { kind }, state: getState() };
      }
      if (name === 'sv3d_clear_measurements') {
        setSvMcpPick('0');
        lockCanvasInput();
        const api = measurementsApi();
        const framing = semanticCamera();
        if (api && typeof api.clear === 'function') await api.clear();
        // Removing the measurement renderables changes the visible content, so
        // re-frame for the same reason a preset rebuild does (see below).
        await holdSemanticCamera(framing);
        return { replay_input: {}, state: getState() };
      }
      if (name === 'sv3d_switch_structure') {
        const entries = structureEntries();
        if (!entries.length) throw new Error('Mol* loaded no structures.');
        let entry;
        if (Object.prototype.hasOwnProperty.call(args || {}, 'structure_id')) {
          entry = entries.find(item => item.structure_id === args.structure_id);
        } else if (Object.prototype.hasOwnProperty.call(args || {}, 'index')) {
          entry = entries[args.index];
        } else if (entries.length === 1) {
          entry = entries[0];
        } else {
          throw new Error('A structure_id or index is required when multiple structures are loaded.');
        }
        if (!entry) throw new Error('Requested structure does not exist.');
        if (structureAlreadyIsolated(entries, entry)) {
          throw new Error(`已经是${structureDisplayLabel(entry)}，未再次切换。`);
        }
        // Read the intent BEFORE the mutation; a local, not a ledger. Zero the
        // pan (§2.4 「清 pan」 bucket): switching to a different molecule changes
        // the reference frame, so the old offset points at content that is no
        // longer there. Keeping it would drag the new structure off-screen.
        const framing = { ...semanticCamera(), pan_right: 0, pan_up: 0 };
        if (typeof hierarchy?.toggleVisibility === 'function') {
          for (const item of entries) {
            hierarchy.toggleVisibility([item.ref], item === entry ? 'show' : 'hide');
          }
        }
        const replayInput = { index: entry.index, structure_id: entry.structure_id };
        // Re-frame onto whatever is now visible, at the same angles and the same
        // tightness. Two reasons, and either alone is sufficient:
        //
        //   1. The target is DEFINED as the visible bounding sphere's centre
        //      (design §4.1). Hiding a structure moves that centre, so leaving
        //      the camera put breaks the invariant the whole model rests on: the
        //      next orbit would swing around the old assembly's centre.
        //   2. A visibility flip queues no renderables, so the commit path
        //      short-circuits on !needsCommit and never refreshes the sphere the
        //      adapter reads — `commited` fires every frame regardless. Settling
        //      alone would record `zoom` against the PREVIOUS content, and the
        //      value can fall outside the schema's own [0.125, 32], which makes
        //      the sv3d_set_camera identity round trip fail. Requesting a camera
        //      reset is what forces the refresh.
        await holdSemanticCamera(framing);
        return { replay_input: replayInput, state: getState() };
      }
      if (name === 'sv3d_set_symmetry') {
        const preset = args && args.preset;
        const mapped = SYMMETRY_PRESETS[preset];
        const label = SYMMETRY_PRESET_LABELS[preset] || String(preset || '');
        if (!mapped) throw new Error('preset must be model, bio_assembly, crystal_contacts, unit_cell, or super_cell.');
        if (derivedSymmetryPreset() === preset) {
          throw new Error(symmetryUnchangedMessage(label));
        }
        // Read the intent BEFORE the rebuild; a local, not a ledger. Zero the
        // pan (§2.4 「清 pan」 bucket): a symmetry preset swaps in crystal copies
        // / a unit cell / a super cell, so the reference frame changes and the
        // old offset points at content that is no longer where it was.
        const framing = { ...semanticCamera(), pan_right: 0, pan_up: 0 };
        await applySymmetryPreset(preset);
        // Re-frame against the NEW bounding sphere so the angles and the
        // tightness the expert chose survive the rebuild. The rebuild
        // clears boundingSphereVisible until a camera reset is queued;
        // waiting one settleFrame is not enough (and is the 「模型」 failure).
        await holdSemanticCamera(framing, {waitForSphere: true});
        return { replay_input: { preset }, state: getState() };
      }
      if (name === 'viewer_get_state') {
        // This is the FIRST call after a page load — the trajectory layer reads
        // the viewer to anchor the draft — and at that moment Mol* has often not
        // committed a visible sphere yet, so a bare getState() fails closed on a
        // sphere that is merely not ready. That surfaced as
        // 「草稿存储不可用: viewer_get_state failed: 无法取景…」 with the structure
        // plainly on screen. Wait one commit and re-read, which is precisely what
        // ensureVisibleSphere is for; callTool's entry-pose capture above already
        // tolerates the same condition. A sphere that is genuinely empty still
        // fails, because ensureVisibleSphere rethrows after the retry.
        try {
          await ensureVisibleSphere();
        } catch (error) {
          if (!isEmptySphereError(error)) throw error;
        }
        return getState();
      }
      if (name === 'viewer_reset') {
        setSvMcpPick('0');
        lockCanvasInput();
        residuePickFraming = null;
        focusedResidue = null;
        clearAtomLabelOverlay();
        const api = measurementsApi();
        if (api && typeof api.clear === 'function') await api.clear();
        if (api && typeof api.clearFocus === 'function') api.clearFocus();
        if (api && typeof api.clearResidueOverlays === 'function') await api.clearResidueOverlays();
        // Rebuild the default hierarchy BEFORE restoring the camera: the camera
        // frames against the bounding sphere, which the rebuild changes.
        if (derivedSymmetryPreset() !== 'model') await applySymmetryPreset('model');
        if (typeof hierarchy?.toggleVisibility === 'function') {
          structureEntries().forEach((item, index) => {
            hierarchy.toggleVisibility(
              [item.ref],
              initialVisibility[index] === false ? 'hide' : 'show',
            );
          });
        }
        if (chainScope && chainScope.isolatedChainId()) await chainScope.showAll();
        await restoreEntryComponents();
        await ensureVisibleSphere();
        if (initialSemanticCamera === null) {
          try { requireInitialSemanticCamera(); } catch (_) {
            await holdSemanticCamera({ azimuth_deg: 0, elevation_deg: 0, zoom: 1, pan_right: 0, pan_up: 0 });
            return { reset: true, state: getState() };
          }
        }
        await holdSemanticCamera(requireInitialSemanticCamera());
        return { reset: true, state: getState() };
      }
      if (name === 'sv3d_set_component_visibility') {
        const patch = {};
        for (const type of COMPONENT_TYPES) {
          if (args && Object.prototype.hasOwnProperty.call(args, type)) {
            if (typeof args[type] !== 'boolean') {
              throw new Error(`${type} must be a boolean.`);
            }
            patch[type] = args[type];
          }
        }
        await applyComponentVisibility(patch);
        return { replay_input: patch, state: getState() };
      }
      if (name === 'sv3d_set_representation') {
        const target = args && args.target;
        const style = args && args.style;
        if (target === 'residue') {
          if (args && args.commit === true) {
            const replayInput = await commitResidueRepresentation(args, style);
            return { replay_input: replayInput, state: getState() };
          }
          await armResidueRepresentation(style);
          return { replay_input: { target, style }, state: getState() };
        }
        await applyRepresentation(target, style);
        return { replay_input: { target, style }, state: getState() };
      }
      if (name === 'sv3d_clash') {
        if (derivedSymmetryPreset() !== 'model') {
          // Clash runs on the full deposited atom list, so the winning atom is
          // only guaranteed on screen (drawable) under the deposited model.
          // 生物组装体 / 晶胞 / 超胞 are a different atom set — send the expert back
          // to 「模型」 via sv3d_set_symmetry before scanning.
          throw new Error('请先用 sv3d_set_symmetry 切换到「模型」(完整沉积模型) 再扫描 clash。');
        }
        const injected = structureEntries()[0]?.ref?.cell?.obj?.data?.__svClashInput;
        let input = injected;
        if (!input) {
          let file = '';
          try {
            const href = global.location && global.location.href;
            if (href && href !== 'about:blank') {
              file = new URL(href).searchParams.get('file') || '';
            }
          } catch (_error) {}
          if (!file && typeof global.__svMolstarStructureFile === 'string') {
            file = global.__svMolstarStructureFile;
          }
          if (!file) {
            throw new Error('无法读取结构连接：页面没有 CIF 文件地址。');
          }
          let url = file;
          try {
            const href = global.location && global.location.href;
            if (href && href.indexOf('http') === 0) url = new URL(file, href).href;
          } catch (_error) {}
          const response = await global.fetch(url);
          if (!response || !response.ok) {
            throw new Error('无法读取结构连接：CIF 下载失败。');
          }
          input = parseMmCifClashInput(await response.text());
        }
        if (!input || !Array.isArray(input.nonpolymer) || !Array.isArray(input.polymer)) {
          throw new Error('无法读取结构连接，拒绝扫描以免把共价对当成 clash。');
        }
        if (!Array.isArray(input.connected)) {
          throw new Error('无法读取结构连接，拒绝扫描以免把共价对当成 clash。');
        }
        const result = scanClash(input.nonpolymer, input.polymer, input.connected);
        if (!result.nonpolymer_atom_id || !result.polymer_atom_id) {
          // Completed scan, nothing to mark — do not throw (that painted the
          // expert page red) and do not touch the camera.
          return { replay_input: {}, result, state: getState() };
        }
        // scanClash covers the full deposited model (every crystallographic copy),
        // and the expert page renders that deposited model (molstar.html builds it
        // for embed=expert), so the winning atom is on screen and can be drawn.
        // Capture the raw pose, not the semantic 5-tuple: addDistance grows
        // boundingSphereVisible, and holdSemanticCamera would re-fit zoom
        // against that new sphere (sudden zoom-in). The structure did not
        // change; put the camera back where it was.
        const snapshot = camera.getSnapshot();
        clashCameraDiag('clash.before-draw', {
          capturedRadius: snapshot && snapshot.radius,
          capturedRadiusMax: snapshot && snapshot.radiusMax,
        });
        const api = measurementsApi();
        if (!api || typeof api.addDistance !== 'function') {
          throw new Error('无法标出 clash 那对原子：测量模块没有画距离的接口。');
        }
        await api.addDistance(result.nonpolymer_atom_id, result.polymer_atom_id);
        clashCameraDiag('clash.after-draw');
        await holdRawCameraSnapshot(snapshot);
        clashCameraDiag('clash.after-restore');
        global.setTimeout(() => clashCameraDiag('clash.after-restore+100ms'), 100);
        global.setTimeout(() => clashCameraDiag('clash.after-restore+500ms'), 500);
        return { replay_input: {}, result, state: getState() };
      }
      if (name === 'sv3d_get_unit_cell') {
        const result = parseUnitCellCif(await fetchLoadedCifText());
        return { replay_input: {}, result, state: getState() };
      }
      if (name === 'sv3d_measure_atoms') {
        const replayInput = measureAtomsReplayInput(args || {});
        const atoms = parseMmCifMeasureAtoms(await fetchLoadedCifText());
        const left = requireMeasuredAtom(atoms, args, 'a');
        const right = requireMeasuredAtom(atoms, args, 'b');
        const distance = vecLength(vecSub(left.xyz, right.xyz));
        const api = measurementsApi();
        if (!api || typeof api.addDistance !== 'function') {
          throw new Error('无法标出距离：测量模块没有画距离的接口。');
        }
        await api.addDistance(left.atom_id, right.atom_id);
        const live = semanticCamera();
        const sphere = visibleSphere();
        const mid = [
          (left.xyz[0] + right.xyz[0]) / 2,
          (left.xyz[1] + right.xyz[1]) / 2,
          (left.xyz[2] + right.xyz[2]) / 2,
        ];
        const pairRadius = Math.max(distance / 2, MIN_FIT_RADIUS);
        const snapshot = camera.getSnapshot();
        const position = Array.from(snapshot.position, Number);
        const lookTarget = asXYZ(snapshot.target) || sphere.center;
        const toTarget = vecSub(lookTarget, position);
        const camDistance = vecLength(toTarget);
        const fwd = camDistance > 1e-12 ? vecScale(toTarget, 1 / camDistance) : [0, 0, -1];
        const basis = cameraRightUp(fwd);
        let panRight = 0;
        let panUp = 0;
        if (basis && sphere.radius > 1e-12) {
          const delta = vecSub(mid, sphere.center);
          panRight = vecDot(delta, basis.right) / sphere.radius;
          panUp = vecDot(delta, basis.up) / sphere.radius;
        }
        const zoom = Math.min(32, Math.max(0.125, sphere.radius / pairRadius));
        try {
          await holdSemanticCamera({
            azimuth_deg: live.azimuth_deg,
            elevation_deg: live.elevation_deg,
            zoom,
            pan_right: Math.min(2, Math.max(-2, panRight)),
            pan_up: Math.min(2, Math.max(-2, panUp)),
          });
        } catch (error) {
          const message = error && error.message ? String(error.message) : '';
          if (!message.includes('无法取景')) throw error;
        }
        return {
          replay_input: replayInput,
          result: {
            distance_A: distance,
            atom_a: left.atom_id,
            atom_b: right.atom_id,
            expert_summary: formatMeasureSummary(
              'distance',
              [{identity: measuredAtomLabel(left.atom_id)}, {identity: measuredAtomLabel(right.atom_id)}],
              `${distance.toFixed(3)} Å`,
            ),
          },
          state: getState(),
        };
      }
      if (name === 'sv3d_set_chain_visibility') {
        await applyChainVisibility(args);
        return { replay_input: { only: String(args.only) }, state: getState() };
      }
      if (name === 'sv3d_focus_residue') {
        const identity = residueIdentity(args);
        const api = measurementsApi();
        if (!api || typeof api.focusResidue !== 'function') {
          throw new Error('无法定位残基：Mol* 测量模块不可用。');
        }
        await api.focusResidue(identity);
        // camera.focusLoci interpolates — the view slides to the residue and the
        // call returns immediately. The keyframe screenshot is taken right after
        // this operation, so without waiting it captures the pose from before the
        // slide and the expert's screenshot disagrees with where they landed.
        await settleCameraAnimation();
        focusedResidue = {chain_id: identity.chain_id, seq_id: identity.seq_id};
        if (identity.icode) focusedResidue.icode = identity.icode;
        // Declarative identity only — an agent replays this from chain+seq alone.
        // comp_id is a label, not part of the address, so it stays out.
        const replayInput = {chain_id: identity.chain_id, seq_id: identity.seq_id};
        if (identity.icode) replayInput.icode = identity.icode;
        return { replay_input: replayInput, state: getState() };
      }
      if (name === 'sv3d_set_atom_labels') {
        return applyAtomLabels(args);
      }
      throw new Error(`Unsupported Mol* MCP tool: ${name}.`);
    }

    return Object.freeze({ tools: registry.tools, callTool });
  }

  Object.defineProperty(global, '__ScienceVisionCreateMolstarMCPProvider', {
    configurable: false,
    enumerable: false,
    value: createProvider,
    writable: false,
  });
  Object.defineProperty(global, '__ScienceVisionMolstarClashScan', {
    configurable: false,
    enumerable: false,
    value: scanClash,
    writable: false,
  });
  Object.defineProperty(global, '__ScienceVisionMolstarVdwTable', {
    configurable: false,
    enumerable: false,
    value: GEMMI_VDW_A,
    writable: false,
  });
  Object.defineProperty(global, '__ScienceVisionMolstarParseClashCif', {
    configurable: false,
    enumerable: false,
    value: parseMmCifClashInput,
    writable: false,
  });
  Object.defineProperty(global, '__ScienceVisionMolstarParseUnitCell', {
    configurable: false,
    enumerable: false,
    value: parseUnitCellCif,
    writable: false,
  });
}(window));
