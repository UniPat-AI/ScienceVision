// Pure, browser-agnostic capture logic. No DOM/IndexedDB access here so it can
// run under `node --test`. Browser loads this via <script type="module">.

// Throttle decision for mouse_move (spec §4.1): emit only if >= throttleMs since
// the last emitted sample AND moved more than minDeltaPx.
export function shouldEmitMove(prev, cur, { throttleMs = 50, minDeltaPx = 2 } = {}) {
  if (!prev) return true;
  if (cur.t - prev.t < throttleMs) return false;
  const dx = cur.x - prev.x;
  const dy = cur.y - prev.y;
  return dx * dx + dy * dy > minDeltaPx * minDeltaPx;
}

// Mouse heatmap aggregation (spec §4.3). Sample weight = dwell time until the
// next sample × (pressed 1.0 / hover 0.3). Output normalized to [0,1].
export function aggregateHeatmap(moves, { width, height }) {
  const grid = width >= 1200 || height >= 1200 ? 64 : 32;
  const cells = new Array(grid * grid).fill(0);
  for (let i = 0; i < moves.length; i++) {
    const m = moves[i];
    const next = moves[i + 1];
    const dwell = next ? Math.max(0, next.t - m.t) : 0;
    const weight = dwell * (m.button_state ? 1.0 : 0.3);
    if (weight === 0) continue;
    const gx = Math.min(grid - 1, Math.max(0, Math.floor((m.x / width) * grid)));
    const gy = Math.min(grid - 1, Math.max(0, Math.floor((m.y / height) * grid)));
    cells[gy * grid + gx] += weight;
  }
  let max = 0;
  for (const v of cells) if (v > max) max = v;
  return { grid, cells: max > 0 ? cells.map(v => v / max) : cells };
}

// Derived per-task stats (spec §4.2 "derived").
export function computeDerived(events, checkpoints) {
  let length = 0;
  let prevMove = null;
  let nCam = 0;
  let nSlice = 0;
  for (const e of events) {
    if (e.type === 'mouse_move') {
      if (prevMove) {
        const dx = e.x - prevMove.x;
        const dy = e.y - prevMove.y;
        length += Math.sqrt(dx * dx + dy * dy);
      }
      prevMove = e;
    } else if (e.type === 'camera_change') nCam++;
    else if (e.type === 'slice_change') nSlice++;
  }
  return {
    trajectory_length_px: Math.round(length),
    n_checkpoints: checkpoints.length,
    n_camera_changes: nCam,
    n_slice_changes: nSlice,
  };
}

// Assemble the spec §4.2 trajectory object from a manifest task + captured data.
export function buildTrajectory({ task, expert, timing, events, checkpoints, answer, heatmap_uri }) {
  const derived = computeDerived(events, checkpoints);
  derived.heatmap_uri = heatmap_uri;
  return {
    schema_version: '1.0',
    task_id: task.task_id,
    domain: task.domain,
    subdomain: task.subdomain,
    asset: {
      path: task.asset.path,
      viewer: task.asset.viewer,
      initial_state: task.asset.initial_state || null,
    },
    question: {
      text: task.instruction,
      v_class: task.v_class,
      expected_answer_schema: task.answer_schema,
    },
    expert,
    timing,
    events,
    checkpoints,
    answer,
    derived,
  };
}

// Automatic single-task QC gate (spec §6.1). Returns all failures, not just the first.
export function passesAutoQC(traj) {
  const errors = [];
  const required = ['schema_version', 'task_id', 'domain', 'asset', 'question', 'expert', 'timing', 'events', 'checkpoints', 'answer'];
  for (const f of required) if (!(f in traj)) errors.push(`missing field: ${f}`);
  // Structural checks guard on presence so a wholly-absent field reports once
  // (via the required-keys loop) rather than twice.
  if (traj.checkpoints && traj.checkpoints.length < 2) errors.push('checkpoints < 2');
  if (traj.timing && !(traj.timing.active_ms >= 60000)) errors.push('active_ms < 60000');
  if (traj.answer && (!traj.answer.text || !traj.answer.text.trim())) errors.push('answer.text empty');
  if (traj.events && traj.events.length === 0) errors.push('events empty');
  else if (traj.events && !traj.events.some(e => e.type === 'camera_change' || e.type === 'slice_change'))
    errors.push('no camera_change or slice_change event');
  return { pass: errors.length === 0, errors };
}
