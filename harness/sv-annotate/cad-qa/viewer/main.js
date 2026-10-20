// tools/cad_qa_pack/viewer/main.js
// The 3D pane embeds the site's proven mesh viewer (/viewer/gltf.html) in an
// iframe rather than a bespoke three.js canvas: that viewer already renders in
// the user's browser, so reusing it removes the whole class of "renders to
// buffer but never presents" problems the bespoke canvas hit.
import { renderQA } from "./qa.js";

// Pack root, e.g. https://host/sv-annotate/cad-qa/  (viewer/ is one level down).
const PACK_ROOT = new URL("../", location.href);

function renderList(models, onSelect) {
  const ul = document.getElementById("model-list");
  ul.innerHTML = "";
  models.forEach((m) => {
    const li = document.createElement("li");
    li.textContent = m.title || m.id;
    li.onclick = () => {
      [...ul.children].forEach(c => c.classList.remove("active"));
      li.classList.add("active");
      onSelect(m);
    };
    ul.appendChild(li);
  });
  return ul.firstChild;
}

async function boot() {
  const manifest = await (await fetch("manifest.json")).json();
  const iframe = document.getElementById("v3d");
  const overlay = document.getElementById("v3d-overlay");

  const onSelect = (m) => {
    // Absolute same-origin path to the GLB, e.g. /sv-annotate/cad-qa/models/00_bearing/model.glb
    // The GLBs were re-exported from STEP with per-part CAD colours preserved
    // (cascadio/OCC); `?v=` busts the 24h edge cache so viewers get the
    // coloured models instead of the old single-grey-material build.
    const glbPath = new URL(m.glb, PACK_ROOT).pathname + "?v=meshopt1";
    overlay.textContent = "";
    overlay.classList.add("loading");
    overlay.style.display = "grid";
    // gltf.html detects iframe embedding and hides its own topbar.
    // axes=1 turns on the coordinate-axis gizmo (X/Y/Z triad) so annotators
    // can read the part's CAD coordinate frame — needed for axis-alignment Qs.
    iframe.src = "/viewer/gltf.html?axes=1&file=" + encodeURIComponent(glbPath);
    renderQA(m);
  };

  // The iframe 'load' fires when gltf.html's document is ready; it then shows a
  // dark scene and streams the model in. Hide the overlay at that point — the
  // viewer's own background covers the remaining decode time.
  iframe.addEventListener("load", () => {
    if (iframe.src && iframe.src !== "about:blank") {
      overlay.classList.remove("loading");
      overlay.style.display = "none";
    }
  });

  const first = renderList(manifest.models, onSelect);
  if (first) first.click();
}
boot();
