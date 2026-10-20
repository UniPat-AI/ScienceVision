// Renderer for the "QA-Gen Tasks" tab. Fetches viewer/qagen_index.json
// (built by tools/build_qagen_index.py) and writes per-set cards into
// #qagen-grid. See spec §8.2.
import { setBadge } from "/viewer/shared/tabs.js";

const MANIFEST_URL = "/viewer/qagen_index.json";
const MOUNT_ID = "qagen-grid";

function _esc(s) {
  return String(s == null ? "" : s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function _renderCard(set) {
  const name = _esc(set.name);
  const setParam = encodeURIComponent(set.name);
  const kept = Number(set.kept_total || 0).toLocaleString();
  const ratioPct = ((Number(set.kept_ratio) || 0) * 100).toFixed(1);
  const dir = _esc(set.dir);
  const reviewLink = set.has_review_md
    ? `<a class="qagen-meta" href="/${dir}/REVIEW.md">REVIEW.md</a>`
    : "";
  const coverageLink = set.has_coverage_md
    ? `<a class="qagen-meta" href="/${dir}/GENERATION_STATS.md">GENERATION_STATS.md</a>`
    : "";
  return `
    <article class="qagen-card">
      <h3>${name}</h3>
      <div class="qagen-stats">
        <span class="qagen-count">${kept} kept</span>
        · <span>accept ${ratioPct}%</span>
      </div>
      <div class="qagen-actions">
        <a class="qagen-cta" href="/viewer/sciencevision_tasks.html?set=${setParam}">
          open task viewer →
        </a>
        ${reviewLink} ${coverageLink}
      </div>
    </article>`;
}

function _renderError(mount, err) {
  mount.innerHTML = `
    <div class="pane-error">
      Failed to load <code>${_esc(MANIFEST_URL)}</code> (${_esc(err)}).<br>
      Rebuild: <code>python3 tools/build_qagen_index.py</code>
    </div>`;
}

async function init() {
  const mount = document.getElementById(MOUNT_ID);
  if (!mount) return;
  try {
    const r = await fetch(MANIFEST_URL);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const sets = Array.isArray(data.sets) ? data.sets : [];
    if (sets.length === 0) {
      mount.innerHTML = `<div class="pane-empty">No task sets yet.</div>`;
    } else {
      mount.innerHTML = sets.map(_renderCard).join("");
    }
    setBadge("qagen", sets.reduce((n, s) => n + (Number(s.kept_total) || 0), 0));
  } catch (e) {
    _renderError(mount, e.message || String(e));
    setBadge("qagen", "!");
  }
}

document.addEventListener("DOMContentLoaded", init);
