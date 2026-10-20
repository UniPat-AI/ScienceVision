(() => {
  'use strict';

  const molstarFormats = Object.freeze({
    mmcif: 'mmcif', cif: 'cifCore', pdb: 'pdb', bcif: 'mmcif',
    sdf: 'sdf', mol: 'mol', mol2: 'mol2', xyz: 'xyz',
  });
  const niivueSlice = Object.freeze({ axial: 0, coronal: 1, sagittal: 2, multiplanar: 3, render: 4 });
  let molstarInstance = null;
  let niivueInstance = null;

  const byId = id => document.getElementById(id);
  const status = byId('status');
  const taskList = byId('task-list');
  const prompt = byId('prompt');
  const taskId = byId('task-id');
  const taskTitle = byId('task-title');
  const operations = byId('operations');
  const empty = byId('viewer-empty');
  const molstarRoot = byId('molstar-viewer');
  const niivueCanvas = byId('niivue-viewer');

  async function fetchJson(path) {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error(`Local reviewer route failed (${response.status}).`);
    return response.json();
  }

  function showError(error) {
    status.textContent = error.message || String(error);
    empty.hidden = false;
    molstarRoot.hidden = true;
    niivueCanvas.hidden = true;
  }

  function loadScript(path) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-reviewer-runtime="${path}"]`);
      if (existing) { resolve(); return; }
      const script = document.createElement('script');
      script.src = path;
      script.dataset.reviewerRuntime = path;
      script.onload = resolve;
      script.onerror = () => reject(new Error('Local viewer runtime could not load.'));
      document.head.appendChild(script);
    });
  }

  function clearViewer() {
    empty.hidden = true;
    molstarRoot.hidden = true;
    niivueCanvas.hidden = true;
    molstarRoot.replaceChildren();
    if (niivueInstance && typeof niivueInstance.dispose === 'function') niivueInstance.dispose();
    niivueInstance = null;
    molstarInstance = null;
  }

  function showOperations(scene) {
    operations.replaceChildren(...scene.allowed_operations.map(operation => {
      const tag = document.createElement('span');
      tag.textContent = operation;
      return tag;
    }));
  }

  async function openMolstar(scene) {
    await loadScript('/vendor/molstar/molstar.js');
    if (!window.molstar || !window.molstar.Viewer) throw new Error('Mol* runtime is unavailable.');
    molstarRoot.hidden = false;
    molstarInstance = await window.molstar.Viewer.create('molstar-viewer', {
      layoutIsExpanded: false,
      layoutShowControls: true,
      layoutShowLeftPanel: true,
      layoutShowSequence: false,
      layoutShowLog: false,
      layoutShowRemoteState: false,
      collapseLeftPanel: false,
      viewportShowExpand: true,
      viewportShowSelectionMode: true,
      disabledExtensions: ['volseg'],
    });
    await molstarInstance.loadStructureFromUrl(scene.primary_url, molstarFormats[scene.format], scene.format === 'bcif');
  }

  async function openNiivue(scene) {
    await loadScript('/vendor/niivue/niivue.umd.js');
    if (!window.niivue || !window.niivue.Niivue) throw new Error('NiiVue runtime is unavailable.');
    niivueCanvas.hidden = false;
    niivueInstance = new window.niivue.Niivue({
      backColor: [0.05, 0.06, 0.09, 1],
      sliceType: niivueSlice.multiplanar,
      isColorbar: true,
      isOrientCube: true,
      crosshairWidth: 1,
    });
    niivueInstance.attachTo('niivue-viewer');
    await niivueInstance.loadVolumes([{ url: scene.primary_url, opacity: 1.0 }]);
    niivueInstance.setSliceType(niivueSlice.multiplanar);
  }

  async function openTask(task, scene) {
    clearViewer();
    status.textContent = `Opening ${scene.viewer} scene`;
    taskId.textContent = task.task_id;
    taskTitle.textContent = scene.viewer === 'molstar' ? 'Mol* controlled scene' : 'NiiVue controlled scene';
    prompt.hidden = false;
    prompt.textContent = task.prompt;
    showOperations(scene);
    if (scene.viewer === 'molstar') await openMolstar(scene);
    else await openNiivue(scene);
    status.textContent = 'Local viewer ready';
  }

  function renderList(tasks, scenes) {
    const byScene = new Map(scenes.map(scene => [scene.scene_ref, scene]));
    taskList.replaceChildren(...tasks.map((task, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'task-button';
      button.innerHTML = `<span></span><small></small>`;
      button.querySelector('span').textContent = task.task_id;
      button.querySelector('small').textContent = byScene.get(task.anonymous_scene_ref).viewer;
      button.addEventListener('click', async () => {
        for (const item of taskList.querySelectorAll('button')) item.removeAttribute('aria-current');
        button.setAttribute('aria-current', 'true');
        try { await openTask(task, byScene.get(task.anonymous_scene_ref)); } catch (error) { showError(error); }
      });
      if (index === 0) queueMicrotask(() => button.click());
      return button;
    }));
  }

  Promise.all([fetchJson('/api/tasks'), fetchJson('/api/scene-registry')])
    .then(([tasks, registry]) => {
      if (!Array.isArray(tasks.tasks) || !Array.isArray(registry.scenes)) throw new Error('Local reviewer data contract is malformed.');
      renderList(tasks.tasks, registry.scenes);
    })
    .catch(showError);
})();
