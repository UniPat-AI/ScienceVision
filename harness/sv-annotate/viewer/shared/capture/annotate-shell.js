import { CaptureSession } from './capture-core.js';
import { makeAdapter } from './viewer-adapters.js';
import { buildTrajectory, passesAutoQC } from './pure.js';

const taskId = new URLSearchParams(location.search).get('annotate');
if (taskId) main(taskId).catch(err => {
  console.error('[annotate]', err);
  // Surface in the panel if it was already built; else console is sufficient
  // (the viewer-init timeout rejects before the panel exists).
  const errEl = document.querySelector('#sv-annotate .err');
  if (errEl) { errEl.style.color = '#f85149'; errEl.textContent = String(err); }
});

async function loadTask(id) {
  const set = new URLSearchParams(location.search).get('set');
  const url = set
    ? `/QA-Gen/tasks/${encodeURIComponent(set)}/ScienceVision_${set.replace(/[^A-Za-z0-9_-]/g, '')}_final_tasks.jsonl`
    : '/sv-annotate/tasks/expert_annotation/caffeine_phase0.jsonl';
  const res = await fetch(url);
  if (!res.ok) throw new Error(`task set fetch failed: HTTP ${res.status} (${url})`);
  const records = (await res.text()).trim().split('\n').filter(Boolean).map(JSON.parse);
  const t = records.find(r => r.task_id === id);
  if (!t) throw new Error(`task not found: ${id} in ${url}`);
  return t;
}

function injectCss() {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/sv-annotate/viewer/shared/capture/annotate.css';
  document.head.appendChild(link);
}

// Wraps the heatmap worker in a Promise.
// Critical: rejects when the worker posts {error} so submit never hangs.
function runHeatmap(moves, size) {
  return new Promise((resolve, reject) => {
    const w = new Worker('/sv-annotate/viewer/shared/capture/heatmap-worker.js', { type: 'module' });
    w.onmessage = (e) => {
      w.terminate();
      if (e.data.error) {
        reject(new Error(`heatmap worker: ${e.data.error}`));
      } else {
        resolve(e.data);
      }
    };
    w.onerror = (err) => { w.terminate(); reject(err); };
    w.postMessage({ moves, width: size.width, height: size.height });
  });
}

function blobToDataUrl(blob) {
  return new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });
}

function download(name, obj) {
  const blob = new Blob([JSON.stringify(obj)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  URL.revokeObjectURL(a.href);
}

async function main(id) {
  injectCss();
  const task = await loadTask(id);

  // Wait until molstar exposes its viewer (Task 7), then build the adapter.
  // Time out after 30s so a failed Mol* init rejects instead of hanging forever
  // (surfaced by main()'s .catch, and in the panel if it exists).
  await new Promise((res, rej) => {
    const t0 = performance.now();
    const iv = setInterval(() => {
      if (window.__sv_viewer) { clearInterval(iv); res(); }
      else if (performance.now() - t0 > 30000) { clearInterval(iv); rej(new Error('viewer failed to initialize (no __sv_viewer after 30s)')); }
    }, 100);
  });
  const adapter = makeAdapter(task.asset.viewer);
  const startedAt = new Date().toISOString();
  const session = new CaptureSession(adapter, { sessionKey: id });
  session.start();

  const panel = document.createElement('div');
  panel.id = 'sv-annotate';
  // Static skeleton only; variable task.* fields are set via textContent below
  // to avoid HTML injection from a crafted JSONL record.
  panel.innerHTML = `
    <h3>Task <span class="vclass" id="sv-vclass"></span></h3>
    <div class="q" id="sv-q"></div>
    <div id="sv-fields" style="color:#8b949e;font-size:11px"></div>
    <button id="sv-cp" class="secondary">Mark key step</button>
    <div class="cp-count" id="sv-cpcount">checkpoints: 0</div>
    <hr style="border-color:#30363d">
    <label>Answer<textarea id="sv-answer" rows="4"></textarea></label>
    <label>Confidence (1-5)<input id="sv-conf" type="number" min="1" max="5" value="3"></label>
    <label>Difficulty (1-5)<input id="sv-diff" type="number" min="1" max="5" value="3"></label>
    <label>Asset correction (optional)<textarea id="sv-corr" rows="2"></textarea></label>
    <button id="sv-submit">Submit &amp; download</button>
    <div class="err" id="sv-err"></div>`;
  document.body.appendChild(panel);
  panel.querySelector('#sv-vclass').textContent = task.v_class;
  panel.querySelector('#sv-q').textContent = task.instruction;
  panel.querySelector('#sv-fields').textContent = 'expected: ' + task.answer_schema.fields.join(', ');

  const cpCount = panel.querySelector('#sv-cpcount');
  panel.querySelector('#sv-cp').onclick = async () => {
    const reasoning = prompt('Reasoning for this key step:');
    if (reasoning == null) return;
    await session.markCheckpoint(reasoning);
    cpCount.textContent = `checkpoints: ${session.checkpoints.length}`;
  };

  panel.querySelector('#sv-submit').onclick = async () => {
    const errEl = panel.querySelector('#sv-err');
    const btn = panel.querySelector('#sv-submit');
    btn.disabled = true;
    errEl.style.color = '#f85149';
    errEl.textContent = '';
    try {
      const events = await session.getEvents();
      const canvas = adapter.getCanvas();
      const r = canvas.getBoundingClientRect();
      const size = { width: Math.round(r.width), height: Math.round(r.height) };
      const moves = events.filter(e => e.type === 'mouse_move');
      const hm = await runHeatmap(moves, size);
      const heatmapDataUrl = await blobToDataUrl(hm.pngBlob);

      const answer = {
        text: panel.querySelector('#sv-answer').value,
        confidence: Number(panel.querySelector('#sv-conf').value),
        difficulty_rating: Number(panel.querySelector('#sv-diff').value),
        asset_correction_comment: panel.querySelector('#sv-corr').value.trim() || null,
      };
      // Externalize checkpoint screenshots to paths; keep the images in the bundle.
      const checkpoints = session.checkpoints.map((cp, i) => ({
        t: cp.t, viewer_state: cp.viewer_state, reasoning: cp.reasoning,
        screenshot_uri: `checkpoints/${id}/${String(cp.t).padStart(7, '0')}.png`,
      }));
      const traj = buildTrajectory({
        task,
        expert: { id: 'anon_local', session_id: id },
        timing: { started_at: startedAt, submitted_at: new Date().toISOString(), active_ms: session.activeMs },
        events, checkpoints, answer,
        heatmap_uri: `heatmaps/${id}.png`,
      });

      const qc = passesAutoQC(traj);
      if (!qc.pass) {
        errEl.textContent = 'QC failed:\n- ' + qc.errors.join('\n- ');
        btn.disabled = false;   // QC failure is recoverable — allow resubmit
        return;
      }

      const bundle = {
        trajectory: traj,
        assets: {
          [`heatmaps/${id}.png`]: heatmapDataUrl,
          ...Object.fromEntries(session.checkpoints.map((cp, i) => [checkpoints[i].screenshot_uri, cp.screenshot])),
        },
      };
      download(`${id}.bundle.json`, bundle);
      await session.buffer.clear();
      session.stop();
      errEl.style.color = '#3fb950';
      errEl.textContent = 'Submitted — bundle downloaded.';
    } catch (err) {
      errEl.textContent = String(err);
      btn.disabled = false;
    }
  };
}
