// Cutaway panel — visibility / opacity / 3-axis clip plane for any
// Three.js viewer (gltf.html · urdf.html · pointcloud.html).
//
// Why an ES module exporting `attachCutaway({THREE, renderer, scene})`
// instead of a side-effect script? The viewer pages already pull
// three@0.160.0 via importmap. If this module re-imported `three`, you'd
// risk loading a SECOND copy (different module instance), and any
// `instanceof THREE.Object3D` check inside this file would silently
// return false against meshes built by the page's THREE. Routing the
// active THREE namespace through a parameter is the only robust fix.
//
// Design: see CrawlDataAndViewer.md §3.7. Summary —
//   1. Visibility/opacity tree: traverse named descendants of the root
//      object up to depth 2; checkbox + opacity slider per entry.
//   2. 3-axis clip plane: ±X/±Y/±Z, slider in -100..+100% of root bbox.
//      `renderer.localClippingEnabled` toggles only when at least one
//      slider is non-zero, so clean rendering is unchanged at rest.
//   3. Reset: restore visibility/opacity/clip to defaults.
//
// Opacity edits are clone-on-write so the original material instance
// (often shared via cache, e.g., URDF mesh re-use) isn't mutated.

const MAX_TREE_ENTRIES = 12;
const SKIP_TYPES = new Set([
  'AxesHelper', 'GridHelper', 'PointLightHelper',
  'DirectionalLightHelper', 'SpotLightHelper',
]);

export function attachCutaway({ THREE, renderer, scene }) {
  if (!THREE || !renderer || !scene) {
    throw new Error('attachCutaway: THREE, renderer, scene are required');
  }

  // ── Clip planes — fixed normals; constants updated on slider change. ──
  // Slider semantics (per axis): t ∈ [-1, 1].
  //   t = 0  ⇒ plane disabled (no cut).
  //   t > 0  ⇒ cut fraction t from the +axis side; visible side is -axis.
  //            normal = -axis_unit; constant = hi - t*(hi-lo).
  //            That is, visible iff dot(normal,x)+c > 0 ⇔ x_axis < hi-t*(hi-lo).
  //   t < 0  ⇒ cut fraction |t| from the -axis side; visible side is +axis.
  //            normal = +axis_unit; constant = -(lo + |t|*(hi-lo)).
  // Both ends t=±1 fully clip the bbox; the boundary t=0 is excluded from
  // activePlanes() so rendering at rest is byte-identical to no-clip.
  const planes = {
    x: new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0),
    y: new THREE.Plane(new THREE.Vector3(0, -1, 0), 0),
    z: new THREE.Plane(new THREE.Vector3(0, 0, -1), 0),
  };
  const sliderState = { x: 0, y: 0, z: 0 };
  let bbox = null;     // THREE.Box3 of current root, computed in setRoot

  // ── Visibility / opacity bookkeeping ───────────────────────────────────
  // entry = {
  //   obj:     Object3D,           // the toggleable node
  //   meshes:  THREE.Mesh[] | THREE.Points[],  // descendants whose materials we manage
  //   defaults: Map<mesh, {material, opacity, transparent}>,
  // }
  let entries = [];
  let allMeshes = [];   // flat list across all entries — used to apply clip planes

  // ── Build panel DOM ───────────────────────────────────────────────────
  const panel = document.createElement('div');
  panel.className = 'cutaway-panel';
  panel.innerHTML = `
    <div class="cutaway-panel__head">
      <span class="cutaway-panel__title">Cutaway</span>
      <button type="button" class="cutaway-panel__toggle" aria-expanded="true">▾</button>
    </div>
    <div class="cutaway-panel__body">
      <div class="cutaway-panel__sec">
        <div class="cutaway-panel__seclabel">Visibility &amp; opacity</div>
        <div class="cutaway-panel__rows" data-role="vis-rows">
          <div class="cutaway-panel__empty">(no root attached)</div>
        </div>
      </div>
      <div class="cutaway-panel__sec">
        <div class="cutaway-panel__seclabel">Clip plane <span class="cutaway-panel__hint">±drag = which side</span></div>
        <div class="cutaway-panel__rows">
          ${['x', 'y', 'z'].map(ax => `
            <div class="cutaway-panel__row">
              <label class="cutaway-panel__axis">${ax.toUpperCase()}</label>
              <input type="range" min="-100" max="100" step="1" value="0" data-axis="${ax}" data-role="clip">
              <span class="cutaway-panel__pct" data-role="pct" data-axis="${ax}">0%</span>
            </div>`).join('')}
        </div>
      </div>
      <div class="cutaway-panel__sec">
        <button type="button" class="cutaway-panel__reset" data-role="reset">Reset</button>
      </div>
    </div>
  `;
  document.body.appendChild(panel);

  const visRows = panel.querySelector('[data-role="vis-rows"]');
  const body = panel.querySelector('.cutaway-panel__body');
  const toggle = panel.querySelector('.cutaway-panel__toggle');
  toggle.addEventListener('click', () => {
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : '';
    toggle.textContent = open ? '▸' : '▾';
    toggle.setAttribute('aria-expanded', String(!open));
  });

  for (const inp of panel.querySelectorAll('[data-role="clip"]')) {
    inp.addEventListener('input', (e) => {
      const ax = e.target.dataset.axis;
      sliderState[ax] = Number(e.target.value) / 100;
      const pctEl = panel.querySelector(`[data-role="pct"][data-axis="${ax}"]`);
      if (pctEl) pctEl.textContent = `${e.target.value}%`;
      applyClip();
    });
  }
  panel.querySelector('[data-role="reset"]').addEventListener('click', resetAll);

  // ── Public API ────────────────────────────────────────────────────────
  function setRoot(rootObj) {
    if (!rootObj) return;

    // Fresh bbox + materials; do this *before* installing clipping planes
    // so we capture the original material reference per mesh.
    bbox = new THREE.Box3().setFromObject(rootObj);
    if (!isFinite(bbox.min.x) || !isFinite(bbox.max.x)) {
      // Empty group (e.g., URDF before any mesh resolves) — ignore for now;
      // caller should call refresh() once children have been added.
      bbox = null;
      return;
    }

    entries = collectEntries(rootObj);
    allMeshes = entries.flatMap(e => e.meshes);

    // Snapshot original material/opacity for each mesh so reset is exact.
    for (const e of entries) {
      e.defaults = new Map();
      for (const m of e.meshes) {
        e.defaults.set(m, {
          material: m.material,
          opacity: m.material?.opacity ?? 1,
          transparent: m.material?.transparent ?? false,
          visible: m.visible,
        });
      }
    }

    renderVisRows();
    applyClip();
  }

  function refresh() {
    // Re-scan the same root; useful for URDF where meshes resolve async.
    if (!entries.length) return;
    const root = entries[0]?.obj?.parent || scene;
    // Find the highest ancestor we previously rooted on.
    let rootObj = entries[0]?.obj;
    while (rootObj && rootObj.parent && rootObj.parent !== scene) {
      rootObj = rootObj.parent;
    }
    if (rootObj) setRoot(rootObj);
  }

  function dispose() {
    panel.remove();
    for (const m of allMeshes) {
      if (m.material && Array.isArray(m.material.clippingPlanes)) {
        m.material.clippingPlanes = [];
      }
    }
    renderer.localClippingEnabled = false;
  }

  // ── Collection: named descendants up to depth 2 ───────────────────────
  function collectEntries(root) {
    const out = [];
    const visit = (obj, depth) => {
      if (!obj || SKIP_TYPES.has(obj.type)) return;
      const named = obj.name && obj.name.trim().length > 0;
      const hasGeom = !!obj.isMesh || !!obj.isPoints;
      if (depth > 0 && (named || hasGeom)) {
        const meshes = collectMeshes(obj);
        if (meshes.length > 0) {
          out.push({ obj, meshes, defaults: null,
            label: obj.name || `${obj.type}_${obj.id}` });
        }
      }
      if (depth < 2 && obj.children) {
        for (const c of obj.children) visit(c, depth + 1);
      }
    };
    if (root.children?.length) {
      for (const c of root.children) visit(c, 1);
    }
    // If the root has no named/groupable children, fall back to a single
    // entry covering the whole root (Points / single Mesh case).
    if (out.length === 0) {
      const meshes = collectMeshes(root);
      if (meshes.length > 0) {
        out.push({ obj: root, meshes, defaults: null,
          label: root.name || meshes[0]?.type || 'root' });
      }
    }
    return out;
  }

  function collectMeshes(obj) {
    const list = [];
    obj.traverse?.(c => {
      if ((c.isMesh || c.isPoints) && !SKIP_TYPES.has(c.type)) list.push(c);
    });
    if (!obj.traverse) {
      if ((obj.isMesh || obj.isPoints) && !SKIP_TYPES.has(obj.type)) list.push(obj);
    }
    return list;
  }

  // ── Render visibility/opacity rows ────────────────────────────────────
  function renderVisRows() {
    if (entries.length === 0) {
      visRows.innerHTML = '<div class="cutaway-panel__empty">(no named children)</div>';
      return;
    }
    const limited = entries.slice(0, MAX_TREE_ENTRIES);
    const overflow = entries.length - limited.length;
    visRows.innerHTML = limited.map((e, i) => `
      <div class="cutaway-panel__row" data-idx="${i}">
        <label class="cutaway-panel__check">
          <input type="checkbox" checked data-role="vis" data-idx="${i}">
          <span class="cutaway-panel__name" title="${escapeAttr(e.label)}">${escapeHtml(truncate(e.label, 14))}</span>
        </label>
        <input type="range" min="0" max="100" step="1" value="100" data-role="opacity" data-idx="${i}">
      </div>
    `).join('') + (overflow > 0
      ? `<div class="cutaway-panel__more">+ ${overflow} more not shown</div>`
      : '');

    for (const inp of visRows.querySelectorAll('[data-role="vis"]')) {
      inp.addEventListener('change', (e) => {
        const idx = Number(e.target.dataset.idx);
        entries[idx].obj.visible = e.target.checked;
      });
    }
    for (const inp of visRows.querySelectorAll('[data-role="opacity"]')) {
      inp.addEventListener('input', (e) => {
        const idx = Number(e.target.dataset.idx);
        const opacity = Number(e.target.value) / 100;
        applyOpacity(entries[idx], opacity);
      });
    }
  }

  function applyOpacity(entry, opacity) {
    for (const mesh of entry.meshes) {
      // Clone-on-write: don't mutate shared/cached material.
      const def = entry.defaults?.get(mesh);
      if (mesh.material && def && mesh.material === def.material) {
        mesh.material = mesh.material.clone();
        // Re-apply clip planes to the cloned material right away.
        mesh.material.clippingPlanes = activePlanes();
      }
      if (mesh.material) {
        mesh.material.transparent = opacity < 1;
        mesh.material.opacity = opacity;
        mesh.material.depthWrite = opacity >= 1;
        mesh.material.needsUpdate = true;
      }
    }
  }

  // ── Clip plane application ────────────────────────────────────────────
  function applyClip() {
    if (!bbox) return;
    const min = bbox.min, max = bbox.max;
    for (const ax of ['x', 'y', 'z']) {
      const t = sliderState[ax];
      const lo = min[ax], hi = max[ax];
      const plane = planes[ax];
      // Reset normal each call — sign of t determines which side gets cut.
      plane.normal.set(0, 0, 0);
      if (t > 0) {
        // Cut fraction t from +axis side; visible is -axis half.
        plane.normal[ax] = -1;
        plane.constant = hi - t * (hi - lo);
      } else if (t < 0) {
        // Cut fraction |t| from -axis side; visible is +axis half.
        plane.normal[ax] = 1;
        plane.constant = -(lo + (-t) * (hi - lo));
      } else {
        // t === 0: plane is inactive (filtered out by activePlanes).
        plane.normal[ax] = -1;
        plane.constant = hi;
      }
    }
    const active = activePlanes();
    renderer.localClippingEnabled = active.length > 0;
    for (const m of allMeshes) {
      if (!m.material) continue;
      m.material.clippingPlanes = active;
      m.material.clipShadows = false;
    }
  }

  function activePlanes() {
    const list = [];
    for (const ax of ['x', 'y', 'z']) {
      if (sliderState[ax] !== 0) list.push(planes[ax]);
    }
    return list;
  }

  // ── Reset ─────────────────────────────────────────────────────────────
  function resetAll() {
    // Visibility + opacity
    for (const e of entries) {
      e.obj.visible = e.defaults?.values().next().value?.visible ?? true;
      for (const mesh of e.meshes) {
        const def = e.defaults?.get(mesh);
        if (def) {
          mesh.material = def.material;
          if (mesh.material) {
            mesh.material.opacity = def.opacity;
            mesh.material.transparent = def.transparent;
            mesh.material.depthWrite = true;
            mesh.material.clippingPlanes = [];
            mesh.material.needsUpdate = true;
          }
          mesh.visible = def.visible;
        }
      }
    }
    // Sliders + reset
    for (const inp of panel.querySelectorAll('[data-role="opacity"]')) inp.value = '100';
    for (const inp of panel.querySelectorAll('[data-role="vis"]'))     inp.checked = true;
    for (const inp of panel.querySelectorAll('[data-role="clip"]'))    inp.value = '0';
    for (const el  of panel.querySelectorAll('[data-role="pct"]'))     el.textContent = '0%';
    sliderState.x = sliderState.y = sliderState.z = 0;
    renderer.localClippingEnabled = false;
  }

  // ── Helpers ───────────────────────────────────────────────────────────
  function truncate(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c =>
      ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s); }

  return { setRoot, refresh, dispose, panel };
}
