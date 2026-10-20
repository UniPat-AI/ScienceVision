(function scienceVisionExpertApp() {
  'use strict';

  const onlineBase = document.querySelector('meta[name="sciencevision-online-base"]')?.content || '';
  const i18n = window.SVI18n;
  const t = (key, fallback, params) => (i18n ? i18n.t(key, fallback, params) : fallback);
  const serverMessage = message => (i18n ? i18n.translateError(message) : message);
  let onlineSession = null;
  let selectionRequest = 0;
  // Mirrors _validate_trajectory in tools/offline_task_manager/store.py: a step the
  // manager cannot import is worse than a step the expert never recorded.
  const STEP_LIMITS = {count: 200, at: 128, action: 64, note: 500};
  const state = {
    manifest: null, binding: null, tasks: [], selected: null, drafts: {}, filter: 'all',
    viewerFocus: false, rail: '',
  };
  const els = Object.fromEntries([
    'package-meta', 'progress', 'export-results', 'save-draft', 'task-filter', 'task-list', 'task-domain',
    'task-id', 'task-title', 'task-instruction', 'response-form', 'validation-status',
    'validation-feedback', 'answer', 'reasoning', 'attempt', 'save-status', 'sv-task-frame',
    'error-banner', 'viewer-focus-toggle', 'workspace', 'logout',
    'step-list', 'step-action', 'step-note', 'step-add', 'step-status',
  ].map(id => [id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), document.getElementById(id)]));

  // Parent-page half of the browser MCP layer. task-mcp-controls.js self-mounts
  // on load, so this only resolves the already-built controller; the trajectory
  // drawer mounts into the slot that controller exposes.
  const mcpControls = window.ScienceVisionTaskMCPControls?.mountDefault?.() || null;
  const trajectory = window.ScienceVisionTaskTrajectory?.mountDefault?.({ mcpControls })
    || null;

  function canonical(value) {
    if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
    if (value && typeof value === 'object') {
      return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
    }
    return JSON.stringify(value);
  }

  async function sha256(value) {
    const bytes = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }

  function bundleId() {
    return state.manifest.bundle_id
      || state.manifest.package_id
      || state.manifest.legacy_package?.package_id;
  }
  function manifestHash() {
    return state.manifest.bundle_manifest_sha256
      || state.manifest.manifest_sha256
      || state.manifest.legacy_package?.package_manifest_sha256;
  }
  function isV2Bundle() {
    return state.manifest.schema_version === 'sciencevision.expert_task_bundle.v2';
  }
  function legacyPackageId() {
    return state.manifest.package_id || state.manifest.legacy_package?.package_id;
  }
  function storageKey() { return `sciencevision:drafts:${bundleId()}`; }
  function defaultDraft() {
    return {
      validation_status: 'uncertain', validation_feedback: '', answer: '', reasoning: '',
      attempt: 1, steps: [], saved_at: new Date(0).toISOString(),
    };
  }
  function trimmedText(value, limit) {
    return typeof value === 'string' ? value.trim().slice(0, limit) : '';
  }
  function normalizedStep(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const at = trimmedText(value.at, STEP_LIMITS.at);
    const action = trimmedText(value.action, STEP_LIMITS.action);
    const note = trimmedText(value.note, STEP_LIMITS.note);
    // A step without its reason is exactly what the reviewers did not ask for,
    // so it is dropped here rather than rejected later by the manager.
    return at && action && note ? {at, action, note} : null;
  }
  function normalizedSteps(value) {
    if (!Array.isArray(value)) return [];
    return value.map(normalizedStep).filter(Boolean).slice(0, STEP_LIMITS.count)
      .map((step, position) => ({index: position + 1, ...step}));
  }
  function normalizedDraft(value) {
    const fallback = defaultDraft();
    if (!value || typeof value !== 'object' || Array.isArray(value)) return fallback;
    const status = ['valid', 'invalid', 'uncertain'].includes(value.validation_status)
      ? value.validation_status : fallback.validation_status;
    const attempt = Number.isInteger(value.attempt) && value.attempt >= 1 ? value.attempt : 1;
    return {
      validation_status: status,
      validation_feedback: typeof value.validation_feedback === 'string' ? value.validation_feedback : '',
      answer: typeof value.answer === 'string' ? value.answer : '',
      reasoning: typeof value.reasoning === 'string' ? value.reasoning : '',
      attempt,
      steps: normalizedSteps(value.steps),
      saved_at: typeof value.saved_at === 'string' && value.saved_at
        ? value.saved_at : fallback.saved_at,
    };
  }
  function loadDrafts(serverDrafts) {
    let storedDrafts = {};
    try {
      const stored = JSON.parse(localStorage.getItem(storageKey()) || '{}');
      if ((stored.manifest_sha256 || stored.package_manifest_sha256) === manifestHash()
          && stored.drafts && typeof stored.drafts === 'object' && !Array.isArray(stored.drafts)) {
        storedDrafts = stored.drafts;
      }
    } catch (error) {
      showError(t('expert.error.draftLoad',
        `Saved drafts could not be loaded: ${error.message}`, {message: error.message}));
    }
    const remote = serverDrafts && typeof serverDrafts === 'object' && !Array.isArray(serverDrafts)
      ? serverDrafts : {};
    state.drafts = {};
    state.tasks.forEach(row => {
      state.drafts[row.assignment_id] = normalizedDraft(
        remote[row.assignment_id] || storedDrafts[row.assignment_id],
      );
    });
  }
  function persistDrafts() {
    localStorage.setItem(storageKey(), JSON.stringify({
      schema_version: 'sciencevision.expert_drafts.v1',
      manifest_sha256: manifestHash(),
      package_manifest_sha256: manifestHash(),
      drafts: state.drafts,
    }));
  }
  function isComplete(row) {
    const draft = state.drafts[row.assignment_id];
    return Boolean(draft && draft.answer.trim() && draft.reasoning.trim()
      && draft.validation_feedback.trim());
  }
  function taskTitle(row) {
    return row.task.title || row.task.question || row.task.prompt || row.task.task_id || row.task_id;
  }

  function taskAsset(row) {
    return row.task.asset
      || (Array.isArray(row.task.assets) ? row.task.assets[0] : null)
      || null;
  }

  function joinedURL(base, sourcePath) {
    const prefix = String(base || '/').replace(/\/?$/, '/');
    return `${prefix}${String(sourcePath).replace(/^\/+/g, '')}`;
  }

  function assetURL(sourcePath) {
    return joinedURL(state.binding?.content_base || '/', sourcePath);
  }

  function viewerKind(row) {
    const asset = taskAsset(row);
    if (!asset) return '';
    if (asset.viewer) return String(asset.viewer);
    return asset.format === 'svg' ? 'svg' : 'gltf';
  }

  function setViewerFocus(enabled) {
    state.viewerFocus = Boolean(enabled && window.innerWidth > 700);
    els.workspace.dataset.viewerFocus = String(state.viewerFocus);
    els.viewerFocusToggle.setAttribute('aria-pressed', String(state.viewerFocus));
    els.viewerFocusToggle.textContent = state.viewerFocus
      ? t('expert.showTasks', 'Show tasks & response') : t('expert.focusViewer', 'Focus viewer');
    els.viewerFocusToggle.title = state.viewerFocus
      ? t('expert.showTasksTitle', 'Show assigned tasks and response fields')
      : t('expert.focusViewerTitle', 'Give the 3D viewer the full workspace');
  }

  function viewerURL(row) {
    const task = row.task;
    const asset = taskAsset(row) || {};
    if (!asset.path && !asset.repo_path) return '';
    const sourcePath = assetURL(asset.path || `/${asset.repo_path}`);
    if (asset.viewer === 'svg' || (asset.format === 'svg' && !asset.viewer)) return sourcePath;
    const viewer = asset.viewer || 'gltf';
    const params = new URLSearchParams();
    if (viewer === 'cornerstone') params.set('series', sourcePath);
    else if (viewer === 'photonics') params.set('path', sourcePath);
    else params.set('file', sourcePath);
    if (viewer === 'molstar' && asset.format) params.set('type', asset.format);
    if (viewer === 'molstar') params.set('embed', 'expert');
    if (viewer === 'molstar' && Array.isArray(task.assets) && task.assets.length > 1) {
      const files = task.assets.filter(item => item && (item.path || item.repo_path)).map(item => ({
        file: assetURL(item.path || `/${item.repo_path}`),
        type: item.format || asset.format || 'mmcif',
      }));
      if (files.length > 1) params.set('files', JSON.stringify(files));
    }
    if (viewer === 'niivue' && (asset.mask_path || asset.mask_repo_path)) {
      params.set('overlay', assetURL(asset.mask_path || `/${asset.mask_repo_path}`));
    }
    const maskId = asset.mask_id ?? task.target_selection?.mask_id;
    if (viewer === 'niivue' && maskId != null) params.set('mask_id', String(maskId));
    if (asset.view_hint?.cam) params.set('cam', asset.view_hint.cam);
    const runtimeId = state.binding?.runtime_manifest?.runtime_id;
    if (runtimeId) params.set('runtime', runtimeId);
    const viewerBase = state.binding?.viewer_base || '/viewer/';
    return `${joinedURL(viewerBase, `${encodeURIComponent(viewer)}.html`)}?${params.toString()}`;
  }

  function railSignature(rows) {
    return canonical([state.filter, state.selected, i18n?.language() || '',
      rows.map(row => [row.assignment_id, taskTitle(row), row.task.domain || '', isComplete(row)])]);
  }

  function renderRail(rows) {
    els.taskList.replaceChildren();
    rows.forEach(row => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'task-item';
      button.dataset.selected = String(row.assignment_id === state.selected);
      const dot = document.createElement('span');
      dot.className = 'task-state';
      dot.dataset.complete = String(isComplete(row));
      const copy = document.createElement('span');
      copy.className = 'task-copy';
      const strong = document.createElement('strong');
      strong.textContent = taskTitle(row);
      const small = document.createElement('small');
      const domain = row.task.domain || t('expert.unclassified', 'unclassified');
      small.textContent = `${domain} · ${row.task_id}`;
      copy.append(strong, small);
      button.append(dot, copy);
      button.addEventListener('click', () => {
        selectTask(row.assignment_id).catch(error => showError(error.message || String(error)));
      });
      els.taskList.append(button);
    });
  }

  function renderList() {
    const rows = state.tasks.filter(row => state.filter === 'all'
      || (state.filter === 'complete') === isComplete(row));
    // Every keystroke saves a draft and lands here. Rebuilding the rail each
    // time destroys the buttons between a click's mousedown and mouseup, so the
    // first click after typing was swallowed; only rebuild on a real change.
    const signature = railSignature(rows);
    if (signature !== state.rail) {
      state.rail = signature;
      renderRail(rows);
    }
    const complete = state.tasks.filter(isComplete).length;
    els.progress.textContent = t('expert.progress', `${complete} / ${state.tasks.length} complete`, {
      complete, total: state.tasks.length,
    });
  }

  async function openOnlineAssignment(assignmentId) {
    const response = await fetch(`${onlineBase}/api/expert/assignments/open`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': onlineSession.csrf},
      body: JSON.stringify({assignment_id: assignmentId}),
      cache: 'no-store',
    });
    let value = {};
    try {
      value = await response.json();
    } catch (_error) {
      throw new Error(t('expert.error.taskOpenHttp',
        `Task could not be opened (HTTP ${response.status})`, {status: response.status}));
    }
    if (!response.ok) {
      throw new Error(serverMessage(value.error)
        || t('expert.error.taskOpen', 'Task could not be opened'));
    }
    if (value.assignment_id !== assignmentId || value.status !== 'in_progress') {
      throw new Error(t('expert.error.taskOpenInvalid', 'Task open response was invalid'));
    }
    return value;
  }

  // Bundle context travels with the task so trajectory drafts key off the
  // assignment instead of a fingerprint of the task body.
  function mcpTask(row) {
    const packageId = row.package_id || bundleId() || '';
    return {
      ...row.task,
      _bundle: {
        qualified_task_id: packageId ? `${packageId}:${row.task_id}` : row.task_id,
        assignment_id: row.assignment_id,
        package_id: packageId || null,
        expert_id: row.expert_id || null,
      },
    };
  }

  // The MCP controls own frame.src so their navigation token survives the load.
  // Assign it directly only when that parent-page runtime is unavailable.
  function showViewer(row, viewer) {
    const task = mcpTask(row);
    if (mcpControls) {
      if (viewer) mcpControls.navigate(task, viewer);
      else mcpControls.clear();
    } else if (viewer) els.svTaskFrame.src = viewer;
    else els.svTaskFrame.removeAttribute('src');
    if (!trajectory) return;
    const navigation = viewer ? trajectory.navigate(task) : trajectory.clear();
    Promise.resolve(navigation).catch(error => showError(
      t('expert.error.trajectoryUnavailable', `Trajectory capture unavailable: ${error.message || String(error)}`, {
        message: error.message || String(error),
      }),
    ));
  }

  async function selectTask(assignmentId) {
    const requestId = ++selectionRequest;
    const row = state.tasks.find(item => item.assignment_id === assignmentId);
    if (!row) return false;
    if (onlineBase) {
      try {
        await openOnlineAssignment(assignmentId);
      } catch (error) {
        if (requestId === selectionRequest) {
          showError(t('expert.error.taskSelect',
            `Task selection failed: ${error.message || error}`,
            {message: error.message || error}));
        }
        return false;
      }
      if (requestId !== selectionRequest) return false;
    }
    state.selected = assignmentId;
    const draft = state.drafts[assignmentId];
    els.taskDomain.textContent = row.task.domain || '';
    els.taskId.textContent = row.task_id;
    // The heading starts as the "select a task" placeholder; once a task is
    // open its title is data and must survive a language switch.
    els.taskTitle.removeAttribute('data-i18n');
    els.taskTitle.textContent = taskTitle(row);
    els.taskInstruction.textContent = row.task.instruction || row.task.description || row.task.prompt || row.task.question || '';
    els.validationStatus.value = draft.validation_status;
    els.validationFeedback.value = draft.validation_feedback;
    els.answer.value = draft.answer;
    els.reasoning.value = draft.reasoning;
    els.attempt.value = String(draft.attempt);
    els.stepNote.value = '';
    els.stepStatus.textContent = '';
    renderSteps();
    els.responseForm.hidden = false;
    const viewer = viewerURL(row);
    els.viewerFocusToggle.hidden = !viewer;
    setViewerFocus(viewerKind(row) === 'molstar' && window.innerWidth >= 1000);
    showViewer(row, viewer);
    location.hash = encodeURIComponent(assignmentId);
    renderList();
    els.errorBanner.hidden = true;
    els.errorBanner.textContent = '';
    return true;
  }

  function saveCurrent() {
    if (!state.selected) return true;
    const attempt = Number.parseInt(els.attempt.value, 10);
    if (!Number.isInteger(attempt) || attempt < 1) {
      els.saveStatus.textContent = t('expert.status.attemptInvalid', 'Attempt must be 1 or greater');
      return false;
    }
    state.drafts[state.selected] = {
      validation_status: els.validationStatus.value,
      validation_feedback: els.validationFeedback.value,
      answer: els.answer.value,
      reasoning: els.reasoning.value,
      attempt,
      steps: state.drafts[state.selected].steps,
      saved_at: new Date().toISOString(),
    };
    persistWithStatus();
    renderList();
    return true;
  }

  let serverDraftTimer = null;
  const SERVER_DRAFT_IDLE_MS = 3000;

  async function persistServerDraft(assignmentId) {
    if (!onlineBase || !onlineSession || !assignmentId) return;
    const draft = state.drafts[assignmentId];
    if (!draft) return;
    const response = await fetch(`${onlineBase}/api/expert/drafts`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': onlineSession.csrf},
      body: JSON.stringify({
        assignment_id: assignmentId,
        validation_status: draft.validation_status,
        validation_feedback: draft.validation_feedback,
        answer: draft.answer,
        reasoning: draft.reasoning,
        attempt: draft.attempt,
        steps: draft.steps,
      }),
    });
    let value = {};
    try {
      value = await response.json();
    } catch (_error) {
      value = {};
    }
    if (!response.ok) {
      throw new Error(
        serverMessage(value.error)
        || t('expert.error.draftSave', `Draft save failed: ${response.status}`, {
          message: String(response.status),
        }),
      );
    }
    if (value.draft && typeof value.draft.saved_at === 'string') {
      state.drafts[assignmentId] = normalizedDraft({
        ...state.drafts[assignmentId],
        saved_at: value.draft.saved_at,
      });
      persistDrafts();
    }
    els.saveStatus.textContent = t('expert.status.savedServer', 'Saved on the server');
  }

  function scheduleServerDraft() {
    if (!onlineBase) return;
    clearTimeout(serverDraftTimer);
    serverDraftTimer = setTimeout(() => {
      if (state.selected) {
        persistServerDraft(state.selected).catch(error => showError(error.message || String(error)));
      }
    }, SERVER_DRAFT_IDLE_MS);
  }

  async function saveDraftNow() {
    if (!saveCurrent()) return;
    if (!state.selected) return;
    if (onlineBase) {
      clearTimeout(serverDraftTimer);
      try {
        await persistServerDraft(state.selected);
      } catch (error) {
        showError(error.message || String(error));
      }
      return;
    }
    els.saveStatus.textContent = t('expert.status.savedLocally', 'Saved locally');
  }

  function persistWithStatus() {
    try {
      persistDrafts();
      els.saveStatus.textContent = t('expert.status.savedLocally', 'Saved locally');
      scheduleServerDraft();
      return true;
    } catch (error) {
      els.saveStatus.textContent = t('expert.status.localSaveFailed', 'Local save failed');
      showError(t('expert.error.draftSave', `Draft save failed: ${error.message}`, {
        message: error.message,
      }));
      return false;
    }
  }

  function actionLabel(action) {
    const option = Array.from(els.stepAction.options).find(item => item.value === action);
    return option ? option.textContent : action;
  }

  function renderSteps() {
    const draft = state.selected ? state.drafts[state.selected] : null;
    els.stepList.textContent = '';
    (draft?.steps || []).forEach(step => {
      const item = document.createElement('li');
      item.className = 'step-item';
      const action = document.createElement('span');
      action.className = 'step-action';
      action.textContent = actionLabel(step.action);
      const note = document.createElement('span');
      note.className = 'step-note';
      note.textContent = step.note;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'step-remove';
      remove.dataset.position = String(step.index);
      remove.textContent = t('expert.steps.remove', 'Remove');
      remove.title = t('expert.steps.removeTitle', 'Remove this step');
      item.append(action, note, remove);
      els.stepList.append(item);
    });
  }

  function commitSteps(steps, status) {
    state.drafts[state.selected] = {
      ...state.drafts[state.selected],
      steps: normalizedSteps(steps),
      saved_at: new Date().toISOString(),
    };
    els.stepStatus.textContent = status;
    persistWithStatus();
    renderSteps();
  }

  function addStep() {
    if (!state.selected) return;
    const note = trimmedText(els.stepNote.value, STEP_LIMITS.note);
    if (!note) {
      els.stepStatus.textContent = t('expert.steps.noteRequired', 'Say why this operation mattered');
      els.stepNote.focus();
      return;
    }
    const recorded = state.drafts[state.selected].steps;
    if (recorded.length >= STEP_LIMITS.count) {
      els.stepStatus.textContent = t('expert.steps.tooMany', 'Keep at most {limit} key operations', {
        limit: STEP_LIMITS.count,
      });
      return;
    }
    const step = {
      index: recorded.length + 1,
      at: new Date().toISOString(),
      action: trimmedText(els.stepAction.value, STEP_LIMITS.action) || 'other',
      note,
    };
    commitSteps([...recorded, step], t('expert.steps.recorded', 'Key operation recorded'));
    els.stepNote.value = '';
  }

  function removeStep(position) {
    if (!state.selected || !Number.isInteger(position)) return;
    const kept = state.drafts[state.selected].steps.filter(step => step.index !== position);
    commitSteps(kept, t('expert.steps.removed', 'Key operation removed'));
  }

  async function exportResults() {
    if (!saveCurrent()) return;
    const row = state.tasks.find(item => item.assignment_id === state.selected);
    if (!row || !isComplete(row)) {
      setViewerFocus(false);
      showError(t(
        'expert.error.incompleteCurrentTask',
        'This task still needs validation feedback, an answer, and reasoning.',
      ));
      return;
    }
    const v2 = isV2Bundle();
    const containerId = v2 ? state.manifest.bundle_id : legacyPackageId();
    const draft = state.drafts[row.assignment_id];
    const results = [{
      schema_version: v2
        ? 'sciencevision.expert_bundle_result.v2'
        : 'sciencevision.expert_result.v1',
      ...(v2 ? {bundle_id: containerId} : {package_id: containerId}),
      batch_id: state.manifest.batch_id,
      expert_id: state.manifest.expert_id,
      assignment_id: row.assignment_id,
      task_id: row.task_id,
      task_revision_sha256: row.task_revision_sha256,
      attempt: draft.attempt,
      task_validation: {
        status: draft.validation_status,
        feedback: draft.validation_feedback,
      },
      answer: draft.answer,
      reasoning: draft.reasoning,
      trajectory: {
        schema_version: 'sciencevision.trajectory.v1',
        status: draft.steps.length ? 'collected' : 'empty',
        steps: draft.steps,
      },
      evidence: [],
      saved_at: draft.saved_at,
    }];
    const contentHash = await sha256(canonical({container_id: containerId, results}));
    const body = v2 ? {
      schema_version: 'sciencevision.expert_bundle_submission.v2',
      contract_version: 'sciencevision.expert_exchange.v2',
      submission_id: `sub_${contentHash.slice(0, 24)}`,
      bundle_id: containerId,
      bundle_manifest_sha256: state.manifest.bundle_manifest_sha256,
      batch_id: state.manifest.batch_id,
      expert_id: state.manifest.expert_id,
      results,
    } : {
      schema_version: 'sciencevision.expert_submission.v1',
      contract_version: 'sciencevision.offline_exchange.v1',
      submission_id: `sub_${contentHash.slice(0, 24)}`,
      package_id: containerId,
      package_manifest_sha256: state.manifest.manifest_sha256
        || state.manifest.legacy_package.package_manifest_sha256,
      batch_id: state.manifest.batch_id,
      expert_id: state.manifest.expert_id,
      results,
    };
    const envelope = { ...body, payload_sha256: await sha256(canonical(body)) };
    if (onlineBase) {
      const response = await fetch(`${onlineBase}/api/expert/submit`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': onlineSession.csrf},
        body: JSON.stringify({submission: envelope}),
      });
      const value = await response.json();
      if (!response.ok) {
        const fallback = t('expert.error.submissionFailed', 'Submission failed');
        // Store rejections are contract diagnostics with no table entry yet;
        // translateError returns them unchanged until one is added.
        const reported = value.errors || [value.error || fallback];
        throw new Error(reported.map(serverMessage).join('\n'));
      }
      els.saveStatus.textContent = value.submission_status === 'unchanged'
        ? t('expert.status.alreadySubmitted', 'Already submitted')
        : t('expert.status.submitted', `Submitted ${value.counts.accepted} result(s)`, {
          count: value.counts.accepted,
        });
      return;
    }
    const blob = new Blob([`${JSON.stringify(envelope, null, 2)}\n`], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${bundleId()}-${body.submission_id}.results.json`;
    document.body.append(link);
    link.click();
    link.remove();
    requestAnimationFrame(() => URL.revokeObjectURL(url));
  }

  function showError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
  }

  async function loadLocalManifest() {
    for (const path of ['/bundle_manifest.json', '/package_manifest.json']) {
      const response = await fetch(path, {cache: 'no-store'});
      if (response.ok) return response.json();
      if (response.status !== 404) {
        throw new Error(t('expert.error.manifestLoad', `Manifest load failed: HTTP ${response.status}`, {
          status: response.status,
        }));
      }
    }
    throw new Error(t('expert.error.bundleManifest', 'Expert bundle manifest could not be loaded'));
  }

  async function load() {
    try {
      let serverDrafts = {};
      if (onlineBase) {
        const sessionResponse = await fetch(`${onlineBase}/api/session`, {cache: 'no-store'});
        if (!sessionResponse.ok) { location.replace(`${onlineBase}/login/`); return; }
        onlineSession = await sessionResponse.json();
        els.logout.hidden = false;
        els.exportResults.setAttribute('data-i18n', 'expert.submitResults');
        els.exportResults.textContent = t('expert.submitResults', 'Submit this task');
        const bootstrapResponse = await fetch(
          `${onlineBase}/api/expert/bootstrap`, {cache: 'no-store'},
        );
        if (!bootstrapResponse.ok) {
          throw new Error(t('expert.error.bootstrap', 'Active task bundle could not be loaded'));
        }
        const bootstrap = await bootstrapResponse.json();
        state.manifest = bootstrap.manifest;
        state.binding = bootstrap.binding;
        serverDrafts = bootstrap.drafts && typeof bootstrap.drafts === 'object' ? bootstrap.drafts : {};
      } else {
        state.manifest = await loadLocalManifest();
        state.binding = {
          tasks_url: state.manifest.task_file
            ? joinedURL('/', state.manifest.task_file.path)
            : state.manifest.tasks_path,
          content_base: '/',
          viewer_base: state.manifest.viewer_path || '/viewer/',
        };
      }
      const tasksResponse = await fetch(state.binding.tasks_url, { cache: 'no-store' });
      if (!tasksResponse.ok) {
        throw new Error(t('expert.error.tasksLoad', 'Assigned tasks could not be loaded'));
      }
      const text = await tasksResponse.text();
      state.tasks = text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
      const expectedCount = state.manifest.task_file?.count ?? state.manifest.task_count;
      if (state.tasks.length !== expectedCount) {
        throw new Error(t('expert.error.taskCountMismatch', 'Task count mismatch'));
      }
      loadDrafts(serverDrafts);
      els.packageMeta.removeAttribute('data-i18n');
      els.packageMeta.textContent = `${state.manifest.expert.name} · ${state.manifest.batch_id} · ${bundleId()}`;
      els.exportResults.disabled = false;
      if (els.saveDraft) els.saveDraft.disabled = false;
      renderList();
      const requested = decodeURIComponent(location.hash.slice(1));
      await selectTask(state.tasks.some(row => row.assignment_id === requested)
        ? requested : state.tasks[0]?.assignment_id);
    } catch (error) {
      showError(error.message || String(error));
    }
  }

  ['input', 'change'].forEach(eventName => els.responseForm.addEventListener(eventName, saveCurrent));
  els.responseForm.addEventListener('submit', event => { event.preventDefault(); addStep(); });
  els.stepAdd.addEventListener('click', addStep);
  els.stepNote.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); addStep(); }
  });
  els.stepList.addEventListener('click', event => {
    const button = event.target.closest('.step-remove');
    if (button) removeStep(Number.parseInt(button.dataset.position, 10));
  });
  els.taskFilter.addEventListener('change', () => { state.filter = els.taskFilter.value; renderList(); });
  els.viewerFocusToggle.addEventListener('click', () => setViewerFocus(!state.viewerFocus));
  els.exportResults.addEventListener('click', () => exportResults().catch(error => showError(error.message)));
  if (els.saveDraft) {
    els.saveDraft.addEventListener('click', () => saveDraftNow());
  }
  els.logout.addEventListener('click', async () => {
    if (!onlineBase || !onlineSession) return;
    await fetch(`${onlineBase}/api/logout`, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': onlineSession.csrf}, body: '{}',
    });
    location.replace(`${onlineBase}/login/`);
  });
  window.addEventListener('resize', () => {
    if (window.innerWidth <= 700 && state.viewerFocus) setViewerFocus(false);
  });
  // The viewer draws its own panel in English and carries no language layer of
  // its own, so the workspace glosses that wording from here once the frame has
  // loaded. One watcher at a time: the previous task's nodes are gone.
  let stopViewerGloss = null;
  els.svTaskFrame.addEventListener('load', () => {
    if (stopViewerGloss) stopViewerGloss();
    stopViewerGloss = i18n?.observeFrame ? i18n.observeFrame(els.svTaskFrame) : null;
  });
  window.addEventListener('beforeunload', saveCurrent);
  i18n?.onChange(() => {
    setViewerFocus(state.viewerFocus);
    renderSteps();
    renderList();
  });
  load();
}());
