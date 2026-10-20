// Renderer for the "3D-Grounded QA" tab. Fetches datasets/_matched/index.json
// and writes a summary line + per-dataset card grid into #matched-grid.
// See docs/superpowers/specs/2026-06-03-viewer-spa-tabs-design.md §8.1.
import { setBadge } from "/viewer/shared/tabs.js";

const MANIFEST_URL = "/datasets/_matched/index.json";
const MOUNT_ID = "matched-grid";

function _esc(s) {
  return String(s == null ? "" : s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function _renderCard(file) {
  const name = _esc(file.dataset);
  const count = Number(file.count || 0).toLocaleString();
  const repoPath = _esc(file.path_repo || "");
  const datasetParam = encodeURIComponent(file.dataset);
  return `
    <article class="matched-card">
      <h3>${name}</h3>
      <div class="matched-count">${count} matched</div>
      <div class="matched-actions">
        <a class="matched-cta" href="/viewer/match3d.html?dataset=${datasetParam}">
          open matched viewer →
        </a>
        ${repoPath ? `<a class="matched-meta" href="/${repoPath}">${repoPath}</a>` : ""}
      </div>
    </article>`;
}

function _renderError(mount, err) {
  mount.innerHTML = `
    <div class="pane-error">
      Failed to load <code>${_esc(MANIFEST_URL)}</code> (${_esc(err)}).<br>
      Rebuild: <code>python3 scripts/match/build_matched_3d_qa.py</code>
    </div>`;
}

async function init() {
  const mount = document.getElementById(MOUNT_ID);
  if (!mount) return; // page doesn't use this module — silent no-op
  try {
    const r = await fetch(MANIFEST_URL);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const files = Array.isArray(data.files) ? data.files : [];
    const summary = `
      <div class="matched-summary">
        <b>${Number(data.total || 0).toLocaleString()}</b> matched rows
        · scope <code>${_esc(data.scope || "-")}</code>
        · generated ${_esc(data.generated_at || "-")}
      </div>`;
    mount.innerHTML = summary + files.map(_renderCard).join("");
    setBadge("matched", data.total ?? files.length);
  } catch (e) {
    _renderError(mount, e.message || String(e));
    setBadge("matched", "!");
  }
}

document.addEventListener("DOMContentLoaded", init);
