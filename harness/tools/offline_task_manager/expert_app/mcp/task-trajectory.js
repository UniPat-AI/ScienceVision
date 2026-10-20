(function installScienceVisionTaskTrajectory(global) {
  'use strict';

  if (global.ScienceVisionTaskTrajectory) return;

  const SCHEMA_VERSION = 'sciencevision.expert_trajectory.v1';
  const DB_NAME = 'sciencevision-expert-annotations';
  const STORE_NAME = 'trajectory-drafts';
  const instances = new WeakMap();

  // This runtime is vendored into a Chinese-first workspace, so its copy resolves
  // through the shared language layer when the host loads one and falls back to
  // the original English wording when it does not (offline packages, tests).
  function t(key, fallback, params) {
    const layer = global.SVI18n;
    return layer ? layer.t(key, fallback, params) : fallback;
  }

  // Wording thrown here, sent by the backend, or raised by the browser arrives
  // as English text rather than as a key. The layer keys the catalog by that
  // text, so it is translated where it is drawn — from the original, so a
  // later language switch can redraw it in the language then chosen.
  function diagnostic(message) {
    const layer = global.SVI18n;
    return layer ? layer.translateThrown(message) : message;
  }

  function jsonClone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function nowISO() {
    return new Date().toISOString();
  }

  function makeId(prefix) {
    const suffix = global.crypto?.randomUUID?.()
      || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}-${suffix}`;
  }

  function stableValue(value) {
    if (Array.isArray(value)) return value.map(stableValue);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.keys(value).sort()
        .map(key => [key, stableValue(value[key])]));
    }
    return value;
  }

  function fingerprint(value) {
    const text = JSON.stringify(stableValue(value));
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
  }

  function taskInput(task) {
    const input = jsonClone(task || {});
    if (input.bundle === undefined && input._bundle !== undefined) {
      input.bundle = input._bundle;
    }
    delete input._bundle;
    return stableValue(input);
  }

  function taskContext(task) {
    return taskInput(task);
  }

  function taskKey(task) {
    const explicit = task?._bundle?.qualified_task_id || task?.bundle?.qualified_task_id;
    if (explicit) return String(explicit);
    const taskId = String(task?.task_id || '');
    if (!taskId) return '';
    return `derived:${taskId}:${fingerprint(taskContext(task))}`;
  }

  function markDraftChanged(draft, changes = {}, options = {}) {
    const updatedAt = options.updatedAt || nowISO();
    const rotateSubmission = draft.status === 'submitted';
    const submissionId = rotateSubmission
      ? (options.submissionId || makeId('submission'))
      : draft.submission_id;
    return {
      ...draft,
      ...changes,
      submission_id: submissionId,
      idempotency_key: rotateSubmission
        ? (options.idempotencyKey || `sciencevision-trajectory:${submissionId}`)
        : draft.idempotency_key,
      status: 'draft',
      submitted_at: null,
      submission_acknowledgment: null,
      submission_acknowledged_at: null,
      updated_at: updatedAt,
    };
  }

  function createDraft(task, options = {}) {
    const createdAt = options.createdAt || nowISO();
    const qualifiedTaskId = taskKey(task);
    if (!qualifiedTaskId) throw new Error('Trajectory task requires a task_id.');
    const contextFingerprint = fingerprint(taskContext(task));
    const sessionId = options.sessionId || makeId('session');
    const submissionId = options.submissionId || makeId('submission');
    return {
      schema_version: SCHEMA_VERSION,
      storage_key: `task:${qualifiedTaskId}:context:${contextFingerprint}`,
      task_id: String(task.task_id || qualifiedTaskId),
      qualified_task_id: qualifiedTaskId,
      context_fingerprint: contextFingerprint,
      session_id: sessionId,
      submission_id: submissionId,
      idempotency_key: options.idempotencyKey
        || `sciencevision-trajectory:${submissionId}`,
      status: 'draft',
      phase: 'trajectory',
      created_at: createdAt,
      updated_at: createdAt,
      segment_started_at: createdAt,
      last_cut_seq: Number(options.initialCutSeq || 0),
      task_input: taskInput(task),
      pending_actions: [],
      trajectory: [],
      final_result: '',
      submitted_at: null,
      submission_acknowledgment: null,
      submission_acknowledged_at: null,
    };
  }

  function isCompatibleDraft(saved, fresh) {
    return saved?.schema_version === SCHEMA_VERSION
      && saved.storage_key === fresh.storage_key
      && saved.qualified_task_id === fresh.qualified_task_id
      && saved.context_fingerprint === fresh.context_fingerprint
      && JSON.stringify(stableValue(saved.task_input || {}))
        === JSON.stringify(stableValue(fresh.task_input || {}));
  }

  function hydrateDraft(saved, fresh) {
    if (!isCompatibleDraft(saved, fresh)) return fresh;
    return {
      ...saved,
      submission_id: saved.submission_id || fresh.submission_id,
      idempotency_key: saved.idempotency_key || fresh.idempotency_key,
      submission_acknowledgment: saved.submission_acknowledgment || null,
      submission_acknowledged_at: saved.submission_acknowledged_at || null,
      pending_actions: Array.isArray(saved.pending_actions) ? saved.pending_actions : [],
      trajectory: Array.isArray(saved.trajectory) ? saved.trajectory : [],
      phase: 'trajectory',
    };
  }

  function receiptViewerIdentity(identity) {
    if (!identity || typeof identity !== 'object') return null;
    const generation = Number(identity.generation);
    const qualifiedTaskId = String(identity.qualified_task_id || '');
    const viewerInstanceId = String(identity.viewer_instance_id || '');
    const navigationToken = String(identity.navigation_token || '');
    const expectedURL = String(identity.expected_url || '');
    if (!Number.isSafeInteger(generation) || generation < 0
        || !qualifiedTaskId || !viewerInstanceId) return null;
    return {
      generation,
      qualified_task_id: qualifiedTaskId,
      viewer_instance_id: viewerInstanceId,
      navigation_token: navigationToken,
      expected_url: expectedURL,
    };
  }

  function viewerIdentityMatches(left, right) {
    const normalizedLeft = receiptViewerIdentity(left);
    const normalizedRight = receiptViewerIdentity(right);
    return Boolean(normalizedLeft && normalizedRight)
      && normalizedLeft.generation === normalizedRight.generation
      && normalizedLeft.qualified_task_id === normalizedRight.qualified_task_id
      && normalizedLeft.viewer_instance_id === normalizedRight.viewer_instance_id
      && normalizedLeft.navigation_token === normalizedRight.navigation_token
      && normalizedLeft.expected_url === normalizedRight.expected_url;
  }

  function draftAllowedTools(draft) {
    return new Set(draft?.task_input?.tool_policy?.allowed_tools || []);
  }

  function normalizePreview(receipt, options = {}) {
    if (!receipt || typeof receipt !== 'object') throw new Error('Preview receipt is invalid.');
    const completionSeq = Number(receipt.completion_seq);
    if (!Number.isSafeInteger(completionSeq) || completionSeq <= 0) {
      throw new Error('Preview receipt requires a positive completion_seq.');
    }
    const functionName = String(receipt.function || '');
    const viewerIdentity = receiptViewerIdentity(receipt.viewer_identity);
    let status = 'unknown';
    if (receipt.ok === false) status = 'failed';
    else if (receipt.ok === true && functionName && viewerIdentity
        && receipt.state_before && typeof receipt.state_before === 'object'
        && receipt.state_after && typeof receipt.state_after === 'object') status = 'succeeded';
    return {
      action_id: options.actionId || makeId('action'),
      action_type: functionName || 'unknown',
      function_name: functionName,
      function_input: jsonClone(receipt.arguments || {}),
      result: jsonClone(receipt.result),
      state_before: jsonClone(receipt.state_before),
      state_after: jsonClone(receipt.state_after),
      policy_compliant: receipt.policy_compliant === true,
      exploration_only: receipt.exploration_only === true,
      policy_source: String(receipt.policy_source || ''),
      status,
      error: status === 'succeeded' ? null : String(
        receipt.error || (status === 'unknown' ? 'Preview receipt was incomplete.' : 'Preview failed.'),
      ),
      viewer_identity: viewerIdentity,
      start_time: receipt.started_at || options.recordedAt || nowISO(),
      end_time: receipt.ended_at || options.recordedAt || nowISO(),
      completion_seq: completionSeq,
    };
  }

  function recordPreview(draft, receipt, options = {}) {
    if (draft.phase !== 'trajectory') return draft;
    const action = normalizePreview(receipt, options);
    const updatedAt = options.recordedAt || action.end_time;
    return markDraftChanged(draft, {
      pending_actions: [...draft.pending_actions, action],
    }, { updatedAt });
  }

  function isFormalAction(action, draft, viewerIdentity) {
    return action?.status === 'succeeded'
      && draftAllowedTools(draft).has(action.function_name)
      && viewerIdentityMatches(action.viewer_identity, viewerIdentity)
      && action.policy_compliant === true
      && action.exploration_only !== true
      && action.policy_source === 'task_allowed_tools';
  }

  function intervalActions(draft, endSeq = Number.POSITIVE_INFINITY) {
    const startSeq = Number(draft?.last_cut_seq || 0);
    return (draft?.pending_actions || []).filter(action => action.completion_seq > startSeq
      && action.completion_seq <= endSeq);
  }

  function latestInvalidSequence(actions, startSeq = 0, endSeq = Number.POSITIVE_INFINITY) {
    let barrier = Number(startSeq || 0);
    for (const action of actions) {
      if (action.completion_seq <= startSeq || action.completion_seq > endSeq) continue;
      if (action.status !== 'succeeded') barrier = Math.max(barrier, action.completion_seq);
    }
    return barrier;
  }

  function formalActions(actions, draft, viewerIdentity, endSeq = Number.POSITIVE_INFINITY) {
    const barrier = latestInvalidSequence(actions, draft?.last_cut_seq || 0, endSeq);
    return actions.filter(action => action.completion_seq > barrier
      && action.completion_seq <= endSeq
      && isFormalAction(action, draft, viewerIdentity));
  }

  function validateCommitSelection(request, viewerIdentity, currentAllowedTools, intervalEndSeq) {
    const startSeq = Number(request?.last_cut_seq || 0);
    const endSeq = Number(intervalEndSeq);
    if (!Number.isSafeInteger(startSeq) || startSeq < 0
        || !Number.isSafeInteger(endSeq) || endSeq < startSeq) {
      throw new Error('Save step sequence boundary is invalid.');
    }
    const actions = Array.isArray(request?.interval_actions) ? request.interval_actions : [];
    const bySequence = new Map();
    let barrier = startSeq;
    for (const action of actions) {
      const sequence = Number(action?.completion_seq);
      if (!Number.isSafeInteger(sequence) || sequence <= startSeq || sequence > endSeq) continue;
      if (bySequence.has(sequence)) barrier = Math.max(barrier, sequence);
      bySequence.set(sequence, action);
      if (action.status !== 'succeeded') barrier = Math.max(barrier, sequence);
    }
    for (let sequence = startSeq + 1; sequence <= endSeq; sequence += 1) {
      if (!bySequence.has(sequence)) barrier = sequence;
    }
    const selectedId = String(request?.selected_action_id || '');
    const matches = actions.filter(action => action?.action_id === selectedId
      && action.completion_seq > startSeq && action.completion_seq <= endSeq);
    if (!selectedId || matches.length !== 1) {
      throw new Error('Select exactly one preview action for this step.');
    }
    const selected = matches[0];
    const allowedTools = new Set(currentAllowedTools || []);
    if (selected.status !== 'succeeded' || selected.completion_seq <= barrier) {
      throw new Error('The selected preview was invalidated by a failed or unknown viewer event.');
    }
    if (!allowedTools.has(selected.function_name)
        || selected.policy_compliant !== true
        || selected.exploration_only === true
        || selected.policy_source !== 'task_allowed_tools') {
      throw new Error('The selected preview is not allowed by the current task policy.');
    }
    if (!viewerIdentityMatches(selected.viewer_identity, viewerIdentity)) {
      throw new Error('The selected preview belongs to a different viewer instance.');
    }
    return selected;
  }

  function validateRetrySelection(action, viewerIdentity, currentAllowedTools) {
    const allowedTools = new Set(currentAllowedTools || []);
    if (!action || action.status !== 'succeeded'
        || !allowedTools.has(action.function_name)
        || action.policy_compliant !== true
        || action.exploration_only === true
        || action.policy_source !== 'task_allowed_tools') {
      throw new Error('The retained retry action is no longer allowed by the current task.');
    }
    if (!viewerIdentityMatches(action.viewer_identity, viewerIdentity)) {
      throw new Error('The retained retry action belongs to a different viewer instance.');
    }
    return jsonClone(action);
  }

  function stepFromAction(action, candidateActions, resultState = action.state_after) {
    if (resultState == null || typeof resultState !== 'object') {
      throw new Error('A valid viewer result_state is required to save a step.');
    }
    return {
      function_name: action.function_name,
      function_input: jsonClone(action.function_input),
      result: jsonClone(action.result),
      result_state: jsonClone(resultState),
      selected_action_id: action.action_id,
      candidate_actions: jsonClone(candidateActions),
    };
  }

  function saveStep(draft, screenshot, options = {}) {
    if (draft.phase !== 'trajectory') throw new Error('Start trajectory annotation before saving a step.');
    const cutSeq = Number(options.cutSeq);
    const startSeq = Number(draft.last_cut_seq || 0);
    if (!Number.isSafeInteger(cutSeq) || cutSeq < startSeq) {
      throw new Error('A valid monotonic cut_seq is required to save a step.');
    }
    const boundarySeq = Number(options.boundarySeq || cutSeq);
    if (!Number.isSafeInteger(boundarySeq) || boundarySeq < cutSeq) {
      throw new Error('A valid capture boundary sequence is required to save a step.');
    }
    const candidates = intervalActions(draft, cutSeq);
    const eligible = formalActions(candidates, draft, options.viewerIdentity, cutSeq);
    if (!eligible.length) {
      throw new Error('Run at least one task-compliant Preview before saving a step.');
    }
    if (!screenshot?.blob) throw new Error('A PNG screenshot is required to save a step.');
    const selected = options.selectedActionId
      ? eligible.find(action => action.action_id === options.selectedActionId)
      : eligible[eligible.length - 1];
    if (!selected) throw new Error('Selected action is not task-compliant in this screenshot interval.');
    const capturedAt = screenshot.captured_at || nowISO();
    const committedAction = {
      ...selected,
      result: options.executedResult === undefined ? selected.result : jsonClone(options.executedResult),
      state_before: options.executedStateBefore === undefined
        ? selected.state_before : jsonClone(options.executedStateBefore),
      state_after: jsonClone(options.resultState),
    };
    const actionFields = stepFromAction(committedAction, candidates, options.resultState);
    const step = {
      step_id: options.stepId || makeId('step'),
      step: draft.trajectory.length + 1,
      ...actionFields,
      description: String(options.description || ''),
      screenshot: {
        blob: screenshot.blob,
        mime_type: screenshot.mime_type || screenshot.blob.type || 'image/png',
        width: Number(screenshot.width || 0),
        height: Number(screenshot.height || 0),
        captured_at: capturedAt,
      },
      action_interval: {
        start_time: draft.segment_started_at,
        end_time: capturedAt,
        start_seq_exclusive: startSeq,
        end_seq_inclusive: cutSeq,
        capture_seq: boundarySeq,
      },
      cut_seq: boundarySeq,
      execution_receipt: options.executionReceipt ? jsonClone(options.executionReceipt) : null,
    };
    return markDraftChanged(draft, {
      segment_started_at: capturedAt,
      last_cut_seq: boundarySeq,
      pending_actions: draft.pending_actions.filter(action => action.completion_seq > boundarySeq),
      trajectory: [...draft.trajectory, step],
    }, { updatedAt: capturedAt });
  }

  function deleteStep(draft, stepId, options = {}) {
    const trajectory = draft.trajectory
      .filter(step => step.step_id !== stepId)
      .map((step, index) => ({ ...step, step: index + 1 }));
    if (trajectory.length === draft.trajectory.length) throw new Error('Trajectory step was not found.');
    return markDraftChanged(draft, {
      trajectory,
    }, { updatedAt: options.updatedAt });
  }

  async function strictViewerState(api) {
    const state = await api.callTool('viewer_get_state', {});
    if (state == null || typeof state !== 'object') {
      throw new Error('viewer_get_state returned no usable state.');
    }
    return state;
  }

  async function replaySavedActions(api, actions) {
    await api.callTool('viewer_reset', {});
    for (const action of actions || []) {
      await api.callTool(
        action.function_name || action.function,
        jsonClone(action.function_input || action.input || {}),
      );
    }
  }

  async function executeCommittedCapture(api, screenshotAPI, savedActions, selectedAction, onStage) {
    if (!api || !screenshotAPI || typeof screenshotAPI.captureScreenshot !== 'function') {
      throw new Error('Viewer screenshot capture is unavailable.');
    }
    if (typeof screenshotAPI.whenReady === 'function') await screenshotAPI.whenReady();
    onStage?.('replay');
    await replaySavedActions(api, savedActions || []);
    onStage?.('executing');
    const result = await api.callTool(
      selectedAction.function_name,
      jsonClone(selectedAction.function_input || {}),
    );
    const stateAfter = await strictViewerState(api);
    onStage?.('capturing');
    const blob = await screenshotAPI.captureScreenshot();
    if (!blob || blob.type !== 'image/png') {
      throw new Error('Ground-truth screenshot capture must return a PNG blob.');
    }
    return {
      result: jsonClone(result),
      state_before: null,
      state_after: jsonClone(stateAfter),
      blob,
    };
  }

  function updateStep(draft, stepId, changes = {}, options = {}) {
    let found = false;
    let changed = false;
    const trajectory = draft.trajectory.map(step => {
      if (step.step_id !== stepId) return step;
      found = true;
      let updated = { ...step };
      if (Object.prototype.hasOwnProperty.call(changes, 'description')) {
        const description = String(changes.description || '');
        changed ||= description !== step.description;
        updated.description = description;
      }
      if (changes.selected_action_id && changes.selected_action_id !== step.selected_action_id) {
        throw new Error('A saved ground-truth action is immutable because its screenshot is already bound.');
      }
      return updated;
    });
    if (!found) throw new Error('Trajectory step was not found.');
    if (!changed) return draft;
    return markDraftChanged(draft, {
      trajectory,
    }, { updatedAt: options.updatedAt });
  }

  function blobToDataURL(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error || new Error('Unable to encode screenshot.'));
      reader.readAsDataURL(blob);
    });
  }

  async function exportPayload(draft, options = {}) {
    const trajectory = [];
    for (const step of draft.trajectory) {
      trajectory.push({
        step_id: step.step_id,
        step: step.step,
        function: step.function_name,
        input: jsonClone(step.function_input),
        result: jsonClone(step.result),
        result_state: jsonClone(step.result_state),
        description: step.description,
        action_interval: jsonClone(step.action_interval),
        cut_seq: step.cut_seq,
        candidate_actions: jsonClone(step.candidate_actions),
        execution_receipt: jsonClone(step.execution_receipt || null),
        screenshot: {
          mime_type: step.screenshot.mime_type,
          width: step.screenshot.width,
          height: step.screenshot.height,
          captured_at: step.screenshot.captured_at,
          data_url: await blobToDataURL(step.screenshot.blob),
        },
      });
    }
    const submittedAt = options.submittedAt || null;
    return {
      schema_version: SCHEMA_VERSION,
      task_id: draft.task_id,
      qualified_task_id: draft.qualified_task_id,
      context_fingerprint: draft.context_fingerprint,
      session_id: draft.session_id,
      submission_id: draft.submission_id,
      idempotency_key: draft.idempotency_key,
      status: submittedAt ? 'submitted' : draft.status,
      created_at: draft.created_at,
      updated_at: options.updatedAt || draft.updated_at,
      submitted_at: submittedAt,
      task_input: jsonClone(draft.task_input),
      trajectory,
      final_result: draft.final_result,
    };
  }

  async function submitDraft(draft, transport, options = {}) {
    if (typeof transport !== 'function') {
      throw new Error('Submission transport is not configured.');
    }
    const submittedAt = options.submittedAt || nowISO();
    const requestPayload = await exportPayload(draft, {
      updatedAt: submittedAt,
    });
    requestPayload.submission_attempted_at = submittedAt;
    const identity = {
      ...(options.identity || {}),
      submission_id: draft.submission_id,
      idempotency_key: draft.idempotency_key,
    };
    const acknowledgment = await transport({ payload: requestPayload, identity });
    if (acknowledgment?.acknowledged !== true) {
      throw new Error('Submission was not acknowledged by the backend.');
    }
    const payload = {
      ...requestPayload,
      status: 'submitted',
      submitted_at: submittedAt,
    };
    return {
      payload,
      acknowledgment,
      draft: {
        ...draft,
        status: 'submitted',
        submitted_at: submittedAt,
        submission_acknowledgment: jsonClone(acknowledgment),
        submission_acknowledged_at: submittedAt,
        updated_at: submittedAt,
      },
    };
  }

  function createIndexedDBStore(indexedDBRef = global.indexedDB) {
    if (!indexedDBRef) return null;
    let opening = null;
    function database() {
      if (opening) return opening;
      opening = new Promise((resolve, reject) => {
        const request = indexedDBRef.open(DB_NAME, 1);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains(STORE_NAME)) {
            request.result.createObjectStore(STORE_NAME, { keyPath: 'storage_key' });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Unable to open annotation storage.'));
      });
      return opening;
    }
    async function request(mode, operation) {
      const db = await database();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, mode);
        let result;
        let requestResult;
        let settled = false;
        const fail = error => {
          if (settled) return;
          settled = true;
          reject(error || new Error('Annotation storage transaction failed.'));
        };
        tx.oncomplete = () => {
          if (settled) return;
          settled = true;
          resolve(requestResult);
        };
        tx.onerror = () => fail(tx.error || result?.error);
        tx.onabort = () => fail(tx.error || new Error('Annotation storage transaction aborted.'));
        try {
          result = operation(tx.objectStore(STORE_NAME));
          result.onsuccess = () => { requestResult = result.result; };
          result.onerror = () => fail(result.error || new Error('Annotation storage request failed.'));
        } catch (error) {
          try { tx.abort(); } catch (_) { /* transaction may already be inactive */ }
          fail(error);
        }
      });
    }
    return Object.freeze({
      get: key => request('readonly', store => store.get(key)),
      put: value => request('readwrite', store => store.put(value)),
      delete: key => request('readwrite', store => store.delete(key)),
    });
  }

  function sameOriginSubmitEndpoint(content, documentRef) {
    if (!String(content || '').trim()) return null;
    let endpoint;
    try {
      endpoint = new URL(String(content).trim(), documentRef.baseURI || global.location?.href);
    } catch (_) {
      throw new Error('Trajectory submission endpoint is invalid.');
    }
    const pageOrigin = new URL(documentRef.baseURI || global.location?.href).origin;
    if (endpoint.origin !== pageOrigin || !['http:', 'https:'].includes(endpoint.protocol)) {
      throw new Error('Trajectory submission endpoint must be same-origin.');
    }
    return endpoint.href;
  }

  function configuredSubmitEndpoint(documentRef) {
    const content = documentRef?.querySelector?.(
      'meta[name="sciencevision-trajectory-submit-endpoint"]',
    )?.content?.trim();
    return sameOriginSubmitEndpoint(content, documentRef);
  }

  function createAuthenticatedSubmitTransport(options = {}) {
    const documentRef = options.document || global.document;
    const fetchRef = options.fetch || global.fetch?.bind(global);
    const endpoint = options.endpoint
      ? sameOriginSubmitEndpoint(options.endpoint, documentRef)
      : configuredSubmitEndpoint(documentRef);
    if (!endpoint) return null;
    if (typeof fetchRef !== 'function') throw new Error('Fetch is unavailable for trajectory submission.');
    const csrfToken = options.csrfToken ?? documentRef?.querySelector?.(
      'meta[name="sciencevision-csrf-token"]',
    )?.content?.trim();
    return async ({ payload, identity }) => {
      const idempotencyKey = String(
        identity?.idempotency_key || payload?.idempotency_key || '',
      );
      if (!idempotencyKey) throw new Error('Trajectory submission requires an idempotency key.');
      const headers = {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      };
      if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
      const response = await fetchRef(endpoint, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: JSON.stringify({ payload, identity }),
      });
      if (!response?.ok) {
        throw new Error(`Trajectory submission failed with HTTP ${response?.status || 'unknown'}.`);
      }
      let acknowledgment;
      try {
        acknowledgment = await response.json();
      } catch (_) {
        throw new Error('Trajectory submission returned invalid JSON.');
      }
      if (acknowledgment?.acknowledged !== true) {
        throw new Error('Submission was not acknowledged by the backend.');
      }
      if (identity?.submission_id && acknowledgment.submission_id != null
          && acknowledgment.submission_id !== identity?.submission_id) {
        throw new Error('Submission acknowledgment does not match this submission ID.');
      }
      return acknowledgment;
    };
  }

  // Reviewers asked for the reasoning behind the operations that moved a
  // judgement, not a log of every mouse move. Saving a step here is already
  // deliberate -- the expert replays, executes, and captures on purpose -- so
  // every saved step owes a reason. Returns the first step that still lacks
  // one, or null when the trajectory is fully explained.
  function unexplainedStep(draft) {
    const steps = draft?.trajectory || [];
    return steps.find(step => !String(step?.description || '').trim()) || null;
  }

  function appendText(documentRef, parent, tag, className, value) {
    const node = documentRef.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    parent.appendChild(node);
    return node;
  }

  function buildUI(documentRef, root) {
    root.replaceChildren();
    const phase = documentRef.createElement('div');
    phase.className = 'sv-trajectory-phase';
    const review = documentRef.createElement('button');
    review.type = 'button';
    review.dataset.phase = 'review';
    review.textContent = t('trajectory.phase.review', 'Review');
    const annotate = documentRef.createElement('button');
    annotate.type = 'button';
    annotate.dataset.phase = 'trajectory';
    annotate.textContent = t('trajectory.phase.trajectory', 'Trajectory');
    phase.append(review, annotate);

    const captureRow = documentRef.createElement('div');
    captureRow.className = 'sv-trajectory-capture';
    const pending = appendText(
      documentRef, captureRow, 'span', 'sv-trajectory-pending',
      t('trajectory.pending.none', 'No previews'),
    );
    const pendingAction = documentRef.createElement('select');
    pendingAction.className = 'sv-trajectory-pending-action';
    pendingAction.setAttribute(
      'aria-label', t('trajectory.pendingAction.label', 'Ground-truth preview action'),
    );
    pendingAction.disabled = true;
    const capture = documentRef.createElement('button');
    capture.type = 'button';
    capture.textContent = t('trajectory.capture.save', 'Save step + screenshot');
    capture.disabled = true;
    captureRow.append(pendingAction, capture);

    const steps = documentRef.createElement('div');
    steps.className = 'sv-trajectory-steps';
    const finalLabel = appendText(
      documentRef, root, 'label', 'sv-trajectory-final-label',
      t('trajectory.final.label', 'Final result'),
    );
    const finalResult = documentRef.createElement('textarea');
    finalResult.className = 'sv-trajectory-final';
    finalResult.rows = 3;
    finalLabel.appendChild(finalResult);

    const actions = documentRef.createElement('div');
    actions.className = 'sv-trajectory-actions';
    const exportButton = documentRef.createElement('button');
    exportButton.type = 'button';
    exportButton.textContent = t('trajectory.action.export', 'Export draft');
    const submit = documentRef.createElement('button');
    submit.type = 'button';
    submit.textContent = t('trajectory.action.submit', 'Submit trajectory');
    const reset = documentRef.createElement('button');
    reset.type = 'button';
    reset.className = 'sv-trajectory-reset';
    reset.textContent = t('trajectory.action.reset', 'Reset all');
    actions.append(exportButton, submit, reset);
    const status = appendText(
      documentRef, root, 'div', 'sv-trajectory-status',
      t('trajectory.status.noTask', 'No task selected'),
    );
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const stepHeading = appendText(
      documentRef, root, 'div', 'sv-trajectory-section-heading',
      t('trajectory.steps.heading', 'Saved Ground Truth steps'),
    );
    const delivery = appendText(
      documentRef, root, 'div', 'sv-trajectory-delivery',
      t('trajectory.delivery.initial', 'Export available / Submit not configured'),
    );
    root.prepend(captureRow, stepHeading, steps, finalLabel, delivery, actions);
    return {
      phase, review, annotate, pending, pendingAction, capture, steps, finalResult,
      exportButton, submit, reset, status, delivery, finalLabel, stepHeading,
    };
  }

  function create(options = {}) {
    const documentRef = options.document || global.document;
    const mcpControls = options.mcpControls;
    const root = options.root || mcpControls?.getTrajectoryRoot?.();
    if (!documentRef || !mcpControls || !root) {
      throw new Error('Trajectory controls require MCP controls and a trajectory root.');
    }
    if (instances.has(root)) return instances.get(root);
    const store = options.store === undefined ? createIndexedDBStore() : options.store;
    const configuredTransport = createAuthenticatedSubmitTransport({ document: documentRef });
    const submitTransport = configuredTransport
      || options.submitTransport
      || global.ScienceVisionTrajectorySubmitTransport
      || null;
    const ui = buildUI(documentRef, root);
    const mcpRoot = root.closest('.sv-mcp-controls');
    const state = {
      generation: 0,
      task: null,
      draft: null,
      busy: false,
      failClosed: false,
      objectURLs: [],
      retryCommit: null,
    };
    let persistTail = Promise.resolve();

    function replayActions() {
      if (state.draft?.phase !== 'trajectory') return null;
      return state.draft.trajectory.map(step => ({
        function_name: step.function_name,
        function_input: jsonClone(step.function_input),
      }));
    }

    mcpControls.setReplayProvider?.(replayActions);

    let statusRender = null;

    // Backend and thrown wording arrives as English text. It is remembered as
    // given and translated on the way to the element, so the reader sees it in
    // their language and a switch afterwards redraws it in the other one.
    function setStatus(message, kind = '') {
      statusRender = () => {
        ui.status.textContent = diagnostic(message);
        ui.status.dataset.state = kind;
      };
      statusRender();
    }

    // Copy this component composes itself is remembered as a recipe rather than
    // as text, so a language switch can redraw whatever the last operation
    // reported. `params` may be a thunk when it interpolates translated text.
    function setStatusText(key, fallback, params = null, kind = '') {
      statusRender = () => {
        const values = typeof params === 'function' ? params() : params;
        // `detail` is the one parameter that carries a diagnostic rather than
        // copy — the wording of whatever failed. Translate it here too, so a
        // sentence that quotes a failure does not end in English.
        const resolved = typeof values?.detail === 'string'
          ? { ...values, detail: diagnostic(values.detail) }
          : values;
        ui.status.textContent = t(key, fallback, resolved);
        ui.status.dataset.state = kind;
      };
      statusRender();
    }

    function clearObjectURLs() {
      for (const url of state.objectURLs) URL.revokeObjectURL(url);
      state.objectURLs = [];
    }

    async function persistDraft(snapshot) {
      if (!store || !snapshot) return;
      const operation = persistTail.then(() => store.put(snapshot));
      persistTail = operation.catch(() => undefined);
      await operation;
    }

    async function persist() {
      await persistDraft(state.draft);
    }

    function sameViewerIdentity(left, right) {
      return Boolean(left && right)
        && left.generation === right.generation
        && left.qualified_task_id === right.qualified_task_id
        && left.viewer_instance_id === right.viewer_instance_id
        && String(left.navigation_token || '') === String(right.navigation_token || '')
        && String(left.expected_url || '') === String(right.expected_url || '')
        && left.api === right.api;
    }

    function operationContext() {
      return state.draft ? {
        generation: state.generation,
        storage_key: state.draft.storage_key,
        viewer_identity: mcpControls.getIdentity?.() || null,
      } : null;
    }

    function contextIsCurrent(context) {
      return Boolean(context && state.draft)
        && context.generation === state.generation
        && context.storage_key === state.draft.storage_key
        && (!context.viewer_identity || sameViewerIdentity(
          context.viewer_identity, mcpControls.getIdentity?.(),
        ));
    }

    function closeOnRollbackFailure(primaryError, rollbackErrors) {
      state.failClosed = true;
      state.busy = true;
      mcpControls.setInteractionFrozen?.(true);
      render();
      const detail = rollbackErrors.map(error => error?.message || String(error)).join('; ');
      setStatusText(
        'trajectory.status.lockedRollback',
        'Annotation locked after rollback failure: {detail}',
        {
          detail: `${primaryError?.message || primaryError}`
            + (detail ? ` (${detail})` : ''),
        },
        'error',
      );
    }

    async function rollbackMutation(previousDraft, context, primaryError) {
      const rollbackErrors = [];
      let restoredDraft = previousDraft;
      try {
        const recovery = await mcpControls.recover(
          previousDraft.trajectory || [], context.viewer_identity,
        );
        const recoveredAt = nowISO();
        restoredDraft = {
          ...previousDraft,
          pending_actions: [],
          last_cut_seq: recovery.completion_seq,
          segment_started_at: recoveredAt,
          updated_at: recoveredAt,
        };
      } catch (error) {
        rollbackErrors.push(error);
      }
      try {
        await persistDraft(restoredDraft);
      } catch (error) {
        rollbackErrors.push(error);
      }
      if (rollbackErrors.length) {
        closeOnRollbackFailure(primaryError, rollbackErrors);
        return false;
      }
      state.draft = restoredDraft;
      return true;
    }

    async function recordUnknownBoundary(previousDraft, context, error) {
      const completionSeq = Number(error?.completion_seq);
      if (!Number.isSafeInteger(completionSeq) || completionSeq <= previousDraft.last_cut_seq) return;
      const candidate = recordPreview(previousDraft, {
        ok: null,
        function: '',
        arguments: {},
        error: error?.message || String(error),
        policy_compliant: false,
        exploration_only: false,
        policy_source: 'unknown_operation',
        viewer_identity: receiptViewerIdentity(context.viewer_identity),
        started_at: nowISO(),
        ended_at: nowISO(),
        completion_seq: completionSeq,
      });
      await persistDraft(candidate);
      if (contextIsCurrent(context)) state.draft = candidate;
    }

    function render() {
      clearObjectURLs();
      const draft = state.draft;
      const active = 'trajectory';
      ui.review.setAttribute('aria-pressed', String(active === 'review'));
      ui.annotate.setAttribute('aria-pressed', String(active === 'trajectory'));
      const viewerIdentity = mcpControls.getIdentity?.() || null;
      const eligibleActions = draft
        ? formalActions(intervalActions(draft), draft, viewerIdentity)
        : [];
      const previousSelection = ui.pendingAction.value;
      ui.pendingAction.replaceChildren();
      for (const action of eligibleActions) {
        const option = documentRef.createElement('option');
        option.value = action.action_id;
        option.textContent = `${action.function_name} ${JSON.stringify(action.function_input)}`;
        ui.pendingAction.appendChild(option);
      }
      if (eligibleActions.some(action => action.action_id === previousSelection)) {
        ui.pendingAction.value = previousSelection;
      } else if (state.retryCommit?.selectedAction?.action_id
          && eligibleActions.some(
            action => action.action_id === state.retryCommit.selectedAction.action_id,
          )) {
        ui.pendingAction.value = state.retryCommit.selectedAction.action_id;
      } else if (eligibleActions.length) {
        ui.pendingAction.value = eligibleActions[eligibleActions.length - 1].action_id;
      }
      const pendingCount = draft?.pending_actions.length || 0;
      ui.pending.textContent = state.retryCommit
        ? t('trajectory.pending.uncommitted',
          'Save was not committed. The selected action and screenshot are retained; '
          + 'retry without repeating your work.')
        : pendingCount
          ? t(pendingCount === 1 ? 'trajectory.pending.one' : 'trajectory.pending.many',
            pendingCount === 1
              ? '{count} preview since last screenshot'
              : '{count} previews since last screenshot',
            { count: pendingCount })
          : t('trajectory.pending.prompt',
            'Preview a Ground Truth action, then save it with a screenshot.');
      ui.pendingAction.disabled = state.busy || state.failClosed
        || Boolean(state.retryCommit) || active !== 'trajectory' || eligibleActions.length === 0;
      ui.capture.disabled = state.busy || state.failClosed
        || active !== 'trajectory' || (!state.retryCommit && eligibleActions.length === 0);
      ui.capture.textContent = state.retryCommit
        ? (state.retryCommit.capture
          ? t('trajectory.capture.retrySave', 'Retry save')
          : t('trajectory.capture.retryCapture', 'Retry capture + save'))
        : t('trajectory.capture.save', 'Save step + screenshot');
      ui.finalResult.disabled = !draft || state.busy || state.failClosed
        || Boolean(state.retryCommit);
      ui.exportButton.disabled = !draft || state.busy || Boolean(state.retryCommit);
      ui.submit.disabled = !draft || state.busy || state.failClosed
        || draft.trajectory.length === 0 || draft.status === 'submitted'
        || typeof submitTransport !== 'function' || Boolean(state.retryCommit);
      ui.submit.dataset.configured = String(typeof submitTransport === 'function');
      ui.submit.textContent = typeof submitTransport === 'function'
        ? t('trajectory.action.submit', 'Submit trajectory')
        : t('trajectory.action.submitUnconfigured', 'Submit unconfigured');
      ui.submit.title = typeof submitTransport === 'function'
        ? t('trajectory.submit.title', 'Submit the trajectory to the configured backend')
        : t('trajectory.submit.titleUnconfigured',
          'Submission unconfigured: host must inject the trajectory endpoint meta tag.');
      ui.reset.disabled = !draft || state.busy || state.failClosed;
      const finalState = draft?.final_result?.trim()
        ? t('trajectory.delivery.finalReady', 'final ready')
        : t('trajectory.delivery.finalMissing', 'final missing');
      const submitState = typeof submitTransport === 'function'
        ? (draft?.status === 'submitted'
          ? t('trajectory.delivery.submitted', 'submitted')
          : t('trajectory.delivery.submitReady', 'submit ready'))
        : t('trajectory.delivery.submitUnavailable', 'submit unavailable');
      ui.delivery.textContent = t(
        'trajectory.delivery.summary', '{saved} saved / {final} / export ready / {submit}',
        { saved: draft?.trajectory.length || 0, final: finalState, submit: submitState },
      );
      ui.finalResult.value = draft?.final_result || '';
      ui.steps.replaceChildren();
      if (!draft?.trajectory.length) {
        appendText(documentRef, ui.steps, 'div', 'sv-trajectory-empty',
          t('trajectory.steps.empty', 'No saved steps.'));
      } else {
        for (const step of draft.trajectory) {
          const card = documentRef.createElement('article');
          card.className = 'sv-trajectory-step';
          card.dataset.stepId = step.step_id;
          const head = documentRef.createElement('div');
          head.className = 'sv-trajectory-step-head';
          appendText(documentRef, head, 'strong', null,
            t('trajectory.step.title', 'Step {step}', { step: step.step }));
          appendText(documentRef, head, 'code', null, step.function_name);
          const remove = documentRef.createElement('button');
          remove.type = 'button';
          remove.dataset.deleteStep = step.step_id;
          const removeLabel = t(
            'trajectory.step.deleteLabel', 'Delete step {step}', { step: step.step },
          );
          remove.setAttribute('aria-label', removeLabel);
          remove.title = removeLabel;
          remove.textContent = t('trajectory.step.delete', 'Delete');
          remove.disabled = state.busy || state.failClosed || Boolean(state.retryCommit);
          head.appendChild(remove);
          const image = documentRef.createElement('img');
          image.alt = t(
            'trajectory.step.screenshotAlt', 'Step {step} viewer screenshot',
            { step: step.step },
          );
          const objectURL = URL.createObjectURL(step.screenshot.blob);
          state.objectURLs.push(objectURL);
          image.src = objectURL;

          const actionLabel = appendText(documentRef, card, 'label', null,
            t('trajectory.step.action', 'Ground-truth action'));
          const select = documentRef.createElement('select');
          select.dataset.stepAction = step.step_id;
          for (const action of step.candidate_actions.filter(
            candidateAction => candidateAction.action_id === step.selected_action_id,
          )) {
            const option = documentRef.createElement('option');
            option.value = action.action_id;
            option.textContent = `${action.function_name} ${JSON.stringify(action.function_input)}`;
            select.appendChild(option);
          }
          select.value = step.selected_action_id;
          select.disabled = true;
          select.title = t(
            'trajectory.step.actionBound', 'The action is bound to this screenshot.',
          );
          actionLabel.appendChild(select);
          const input = appendText(documentRef, card, 'pre', 'sv-trajectory-input', JSON.stringify(step.function_input, null, 2));
          input.setAttribute('aria-label', t(
            'trajectory.step.inputLabel', 'Step {step} function input', { step: step.step },
          ));
          const explained = Boolean(String(step.description || '').trim());
          card.dataset.explained = String(explained);
          const descriptionLabel = appendText(documentRef, card, 'label', null,
            explained
              ? t('trajectory.step.note', 'Step note / COT')
              : t('trajectory.step.noteRequired', 'Step note / COT (required)'));
          const description = documentRef.createElement('textarea');
          description.rows = 2;
          description.dataset.stepDescription = step.step_id;
          description.value = step.description;
          description.disabled = state.busy || state.failClosed || Boolean(state.retryCommit);
          descriptionLabel.appendChild(description);
          card.prepend(head, image);
          card.append(actionLabel, input, descriptionLabel);
          ui.steps.appendChild(card);
        }
      }
      if (draft) {
        const count = draft.trajectory.length;
        if (draft.status === 'submitted') {
          setStatusText(
            count === 1 ? 'trajectory.status.submitted.one' : 'trajectory.status.submitted.many',
            count === 1 ? 'Submitted {count} step' : 'Submitted {count} steps',
            { count }, 'success',
          );
        } else {
          setStatusText(
            count === 1 ? 'trajectory.status.saved.one' : 'trajectory.status.saved.many',
            count === 1 ? 'Draft saved / {count} step' : 'Draft saved / {count} steps',
            { count }, 'ready',
          );
        }
      }
    }

    async function navigate(task) {
      const generation = ++state.generation;
      state.task = task || null;
      state.draft = null;
      state.failClosed = false;
      state.retryCommit = null;
      state.busy = Boolean(task);
      mcpControls.setInteractionFrozen?.(Boolean(task));
      render();
      if (!task) {
        state.busy = false;
        setStatusText('trajectory.status.noTask', 'No task selected');
        return;
      }
      const fresh = createDraft(task);
      let candidate = fresh;
      let storageFailure = null;
      let recoveryFailure = null;
      try {
        if (!store) throw new Error('IndexedDB draft storage is unavailable.');
        const saved = store ? await store.get(fresh.storage_key) : null;
        if (generation !== state.generation) return;
        candidate = hydrateDraft(saved, fresh);
      } catch (error) {
        if (generation !== state.generation) return;
        storageFailure = error;
      }
      state.draft = candidate;
      render();
      setStatusText('trajectory.status.recovering',
        'Recovering viewer from saved trajectory...', null, 'running');
      try {
        const recovery = await mcpControls.recover(candidate.trajectory || []);
        if (generation !== state.generation || state.draft?.storage_key !== fresh.storage_key) return;
        const recoveredAt = nowISO();
        state.draft = {
          ...candidate,
          pending_actions: [],
          last_cut_seq: recovery.completion_seq,
          segment_started_at: recoveredAt,
          updated_at: recoveredAt,
        };
        await persist();
        render();
      } catch (error) {
        if (generation !== state.generation) return;
        recoveryFailure = error;
      } finally {
        if (generation === state.generation) {
          state.failClosed = Boolean(storageFailure || recoveryFailure);
          state.busy = state.failClosed;
          mcpControls.setInteractionFrozen?.(state.failClosed);
          render();
          if (storageFailure) {
            setStatusText('trajectory.status.storageUnavailable',
              'Draft storage unavailable: {detail}',
              { detail: storageFailure.message }, 'error');
          } else if (recoveryFailure) {
            setStatusText('trajectory.status.recoveryFailed',
              'Viewer recovery failed: {detail}',
              { detail: recoveryFailure.message }, 'error');
          }
        }
      }
    }

    function clear() {
      state.generation += 1;
      state.task = null;
      state.draft = null;
      state.busy = false;
      state.failClosed = false;
      state.retryCommit = null;
      mcpControls.setInteractionFrozen?.(false);
      render();
      setStatusText('trajectory.status.noTask', 'No task selected');
    }

    function invalidate() {
      state.generation += 1;
      state.task = null;
      state.draft = null;
      state.busy = true;
      state.failClosed = false;
      state.retryCommit = null;
      mcpControls.setInteractionFrozen?.(true);
      render();
      setStatusText('trajectory.status.waitingViewer',
        'Waiting for the new viewer document...', null, 'running');
    }

    async function setPhase(phase) {
      if (!state.draft || state.busy) return;
      const nextPhase = phase === 'trajectory' ? 'trajectory' : 'review';
      if (nextPhase === state.draft.phase) return;
      if (nextPhase === 'review') {
        const candidate = { ...state.draft, phase: 'review', updated_at: nowISO() };
        state.busy = true;
        render();
        let failure = null;
        try {
          await persistDraft(candidate);
          state.draft = candidate;
        } catch (error) {
          failure = error.message || String(error);
        } finally {
          state.busy = false;
          render();
          if (failure) setStatus(failure, 'error');
        }
        return;
      }
      const context = operationContext();
      const previousDraft = state.draft;
      state.busy = true;
      mcpControls.setInteractionFrozen?.(true);
      render();
      let failure = null;
      let viewerRecovered = false;
      try {
        const recovery = await mcpControls.recover(state.draft.trajectory, context.viewer_identity);
        viewerRecovered = true;
        if (!contextIsCurrent(context)) return;
        const candidate = {
          ...previousDraft,
          phase: 'trajectory',
          pending_actions: [],
          last_cut_seq: recovery.completion_seq,
          segment_started_at: nowISO(),
          updated_at: nowISO(),
        };
        await persistDraft(candidate);
        if (!contextIsCurrent(context)) return;
        state.draft = candidate;
        state.retryCommit = null;
      } catch (error) {
        if (contextIsCurrent(context)) {
          if (viewerRecovered) {
            const rolledBack = await rollbackMutation(previousDraft, context, error);
            if (!rolledBack) return;
          }
          failure = error.message || String(error);
        }
      } finally {
        if (contextIsCurrent(context) && !state.failClosed) {
          state.busy = false;
          mcpControls.setInteractionFrozen?.(Boolean(state.retryCommit));
          render();
          if (failure) setStatus(failure, 'error');
        }
      }
    }

    async function previewRecorded(event) {
      if (!state.draft) return;
      const receiptIdentity = event.detail?.viewer_identity;
      const currentIdentity = mcpControls.getIdentity?.();
      if (receiptIdentity && !viewerIdentityMatches(receiptIdentity, currentIdentity)) return;
      const previousDraft = state.draft;
      try {
        const updated = recordPreview(state.draft, event.detail);
        if (updated === state.draft) return;
        state.draft = updated;
        await persist();
        render();
      } catch (error) {
        state.draft = previousDraft;
        state.failClosed = true;
        state.busy = true;
        mcpControls.setInteractionFrozen?.(true);
        render();
        setStatusText('trajectory.status.locked', 'Annotation locked: {detail}',
          { detail: error.message || String(error) }, 'error');
      }
    }

    async function screenshotInfo(blob, capturedAt = nowISO()) {
      let width = 0;
      let height = 0;
      if (typeof global.createImageBitmap === 'function') {
        const bitmap = await global.createImageBitmap(blob);
        width = bitmap.width;
        height = bitmap.height;
        bitmap.close();
      }
      return { blob, mime_type: blob.type || 'image/png', width, height, captured_at: capturedAt };
    }

    async function captureStep() {
      if (!state.draft || state.busy) return;
      const context = operationContext();
      const previousDraft = state.draft;
      const eligibleActions = formalActions(
        intervalActions(previousDraft), previousDraft, context.viewer_identity,
      );
      const selectedActionId = state.retryCommit?.selectedAction?.action_id
        || ui.pendingAction.value;
      const selectedAction = state.retryCommit?.selectedAction
        || eligibleActions.find(action => action.action_id === selectedActionId);
      if (!selectedAction) {
        setStatusText('trajectory.status.selectAction',
          'Preview and select a Ground Truth action before saving.', null, 'error');
        return;
      }
      const retained = state.retryCommit || { selectedAction: jsonClone(selectedAction) };
      state.retryCommit = retained;
      state.busy = true;
      mcpControls.setInteractionFrozen?.(true);
      render();
      setStatusText('trajectory.status.saveStage', 'Save / {label}...',
        () => ({ label: t('trajectory.stage.replay', 'replaying saved GT steps') }),
        'running');
      let failure = null;
      try {
        let capture = retained.capture;
        if (!capture) {
          capture = await mcpControls.commitStep({
            saved_actions: previousDraft.trajectory,
            interval_actions: intervalActions(previousDraft),
            last_cut_seq: previousDraft.last_cut_seq,
            selected_action_id: selectedActionId,
            retry_action: retained.attempted ? retained.selectedAction : null,
            onStage(stage) {
              const labels = {
                replay: () => t('trajectory.stage.replay', 'replaying saved GT steps'),
                executing: () => t('trajectory.stage.executing', 'executing {name}',
                  { name: selectedAction.function_name }),
                capturing: () => t('trajectory.stage.capturing', 'capturing screenshot'),
              };
              setStatusText('trajectory.status.saveStage', 'Save / {label}...',
                () => ({ label: labels[stage] ? labels[stage]() : stage }), 'running');
            },
          }, context.viewer_identity);
          retained.attempted = true;
          retained.capture = capture;
        }
        if (!contextIsCurrent(context)) return;
        const screenshot = retained.screenshot
          || await screenshotInfo(capture.blob, capture.captured_at);
        retained.screenshot = screenshot;
        if (!contextIsCurrent(context)) return;
        const candidate = retained.candidate || saveStep(previousDraft, screenshot, {
            cutSeq: capture.preview_cut_seq,
            boundarySeq: capture.completion_seq,
            selectedActionId,
            viewerIdentity: context.viewer_identity,
            resultState: capture.result_state ?? capture.state_after,
            executedResult: capture.result,
            executedStateBefore: capture.state_before,
            executionReceipt: capture.execution_receipt,
          });
        retained.candidate = candidate;
        setStatusText('trajectory.status.saveStage', 'Save / {label}...',
          () => ({
            label: t('trajectory.stage.committing', 'committing screenshot and draft'),
          }), 'running');
        await persistDraft(candidate);
        if (!contextIsCurrent(context)) return;
        state.draft = candidate;
        state.retryCommit = null;
      } catch (error) {
        if (contextIsCurrent(context)) {
          if (error?.rollback_failed) {
            closeOnRollbackFailure(error, error.rollback_errors || []);
            return;
          }
          retained.attempted = true;
          state.retryCommit = retained;
          // Held as a recipe, not as text: the finally block redraws it, and a
          // language switch afterwards has to redraw it again.
          failure = retained.capture
            ? {
              key: 'trajectory.status.saveFailedWithCapture',
              fallback: 'Save was not committed: {detail}. Retry; the selected action and '
                + 'screenshot are retained.',
              detail: error.message || String(error),
            }
            : {
              key: 'trajectory.status.saveFailed',
              fallback: 'Save was not committed: {detail}. Retry; the selected action is '
                + 'retained.',
              detail: error.message || String(error),
            };
        }
      } finally {
        if (contextIsCurrent(context) && !state.failClosed) {
          state.busy = false;
          mcpControls.setInteractionFrozen?.(Boolean(state.retryCommit));
          render();
          if (failure) {
            setStatusText(failure.key, failure.fallback, { detail: failure.detail }, 'error');
          } else {
            setStatusText('trajectory.status.stepSaved', 'Step {step} saved with screenshot',
              { step: state.draft.trajectory.length }, 'success');
          }
        }
      }
    }

    async function mutateStep(stepId, changes) {
      if (!state.draft || state.busy) return;
      try {
        const candidate = updateStep(state.draft, stepId, changes);
        await persistDraft(candidate);
        state.draft = candidate;
        render();
      } catch (error) {
        setStatus(error.message || String(error), 'error');
      }
    }

    async function removeStep(stepId) {
      if (!state.draft || state.busy) return;
      const context = operationContext();
      const previousDraft = state.draft;
      const candidate = deleteStep(previousDraft, stepId);
      state.busy = true;
      mcpControls.setInteractionFrozen?.(true);
      render();
      let failure = null;
      let viewerRebuilt = false;
      try {
        const rebuilt = await mcpControls.rebuildTrajectory(
          candidate.trajectory, context.viewer_identity, previousDraft.trajectory,
        );
        viewerRebuilt = true;
        if (!contextIsCurrent(context)) return;
        const trajectory = [];
        let previousSequence = rebuilt.start_seq;
        for (let index = 0; index < candidate.trajectory.length; index += 1) {
          const step = candidate.trajectory[index];
          const result = rebuilt.steps[index];
          const screenshot = await screenshotInfo(result.blob, result.ended_at);
          if (!contextIsCurrent(context)) return;
          const action = normalizePreview({
            ok: true,
            function: step.function_name,
            arguments: step.function_input,
            result: result.result,
            state_before: result.state_before,
            state_after: result.result_state,
            policy_compliant: true,
            exploration_only: false,
            policy_source: 'task_allowed_tools',
            viewer_identity: receiptViewerIdentity(context.viewer_identity),
            started_at: result.started_at,
            ended_at: result.ended_at,
            completion_seq: result.completion_seq,
          });
          trajectory.push({
            ...step,
            step: index + 1,
            selected_action_id: action.action_id,
            candidate_actions: [action],
            result: jsonClone(result.result),
            result_state: jsonClone(result.result_state),
            screenshot,
            action_interval: {
              start_time: result.started_at,
              end_time: result.ended_at,
              start_seq_exclusive: previousSequence,
              end_seq_inclusive: result.completion_seq,
              capture_seq: result.completion_seq,
            },
            cut_seq: result.completion_seq,
            execution_receipt: jsonClone(action),
          });
          previousSequence = result.completion_seq;
        }
        const rebuiltAt = nowISO();
        const rebuiltDraft = {
          ...candidate,
          trajectory,
          pending_actions: [],
          last_cut_seq: rebuilt.completion_seq,
          segment_started_at: rebuiltAt,
          updated_at: rebuiltAt,
        };
        await persistDraft(rebuiltDraft);
        if (!contextIsCurrent(context)) return;
        state.draft = rebuiltDraft;
      } catch (error) {
        if (contextIsCurrent(context)) {
          if (error?.rollback_failed) {
            closeOnRollbackFailure(error, error.rollback_errors || []);
            return;
          }
          if (viewerRebuilt) {
            const rolledBack = await rollbackMutation(previousDraft, context, error);
            if (!rolledBack) return;
          }
          failure = error.message || String(error);
        }
      } finally {
        if (contextIsCurrent(context) && !state.failClosed) {
          state.busy = false;
          mcpControls.setInteractionFrozen?.(Boolean(state.retryCommit));
          render();
          if (failure) setStatus(failure, 'error');
        }
      }
    }

    function downloadPayload(payload, suffix) {
      const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = documentRef.createElement('a');
      link.href = url;
      link.download = `${payload.qualified_task_id.replace(/[^A-Za-z0-9_.-]/g, '_')}.${suffix}.json`;
      documentRef.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }

    async function exportDraft() {
      if (!state.draft || state.busy) return;
      const context = operationContext();
      const draft = state.draft;
      state.busy = true;
      render();
      let failure = null;
      try {
        const payload = await exportPayload(draft);
        if (!contextIsCurrent(context)) return;
        downloadPayload(payload, 'trajectory-draft');
      } catch (error) {
        failure = error.message || String(error);
      } finally {
        if (contextIsCurrent(context)) {
          state.busy = false;
          render();
          if (failure) setStatus(failure, 'error');
          else setStatusText('trajectory.status.exported', 'Draft exported', null, 'success');
        }
      }
    }

    async function submitTrajectory() {
      if (!state.draft || state.busy) return;
      if (!state.draft.trajectory.length) {
        setStatusText('trajectory.status.needStep',
          'Save at least one step before submitting.', null, 'error');
        return;
      }
      const unexplained = unexplainedStep(state.draft);
      if (unexplained) {
        setStatusText('trajectory.status.needNote',
          'Step {step} still needs the reason it mattered.',
          { step: unexplained.step }, 'error');
        // Step ids are generated, but a selector built from data is a habit
        // worth not forming: scan for the field instead.
        for (const field of ui.steps.querySelectorAll('[data-step-description]')) {
          if (field.dataset.stepDescription === unexplained.step_id) {
            field.focus();
            break;
          }
        }
        return;
      }
      if (!state.draft.final_result.trim()) {
        setStatusText('trajectory.status.needFinal',
          'Final result is required before submitting.', null, 'error');
        ui.finalResult.focus();
        return;
      }
      if (typeof submitTransport !== 'function') {
        setStatusText('trajectory.status.noTransport',
          'Submission transport is not configured.', null, 'error');
        return;
      }
      const context = operationContext();
      const draft = state.draft;
      state.busy = true;
      render();
      let failure = null;
      try {
        const submittedAt = nowISO();
        const submission = await submitDraft(draft, submitTransport, {
          submittedAt,
          identity: {
            storage_key: context.storage_key,
            qualified_task_id: draft.qualified_task_id,
            context_fingerprint: draft.context_fingerprint,
          },
        });
        if (!contextIsCurrent(context)) return;
        await persistDraft(submission.draft);
        if (!contextIsCurrent(context)) return;
        state.draft = submission.draft;
        const EventType = documentRef.defaultView?.CustomEvent || global.CustomEvent;
        if (EventType) root.dispatchEvent(new EventType(
          'sciencevision:trajectory-submit', {
            detail: {
              payload: submission.payload,
              acknowledgment: submission.acknowledgment,
            },
          },
        ));
      } catch (error) {
        if (contextIsCurrent(context)) failure = error.message || String(error);
      } finally {
        if (contextIsCurrent(context)) {
          state.busy = false;
          render();
          if (failure) setStatus(failure, 'error');
          else {
            setStatusText('trajectory.status.submitAcknowledged',
              'Trajectory submission acknowledged', null, 'success');
          }
        }
      }
    }

    async function resetAll() {
      if (!state.draft || state.busy) return;
      const question = t('trajectory.confirm.reset',
        'Clear every saved step, pending preview, and reset the viewer?');
      if (!global.confirm(question)) return;
      const context = operationContext();
      const task = state.task;
      const previousDraft = state.draft;
      state.busy = true;
      mcpControls.setInteractionFrozen?.(true);
      render();
      let failure = null;
      let viewerReset = false;
      try {
        const reset = await mcpControls.resetViewer(
          context.viewer_identity, previousDraft.trajectory,
        );
        viewerReset = true;
        if (!contextIsCurrent(context)) return;
        const candidate = createDraft(task, {
          phase: 'trajectory',
          initialCutSeq: reset.completion_seq,
        });
        await persistDraft(candidate);
        if (!contextIsCurrent(context)) return;
        state.draft = candidate;
        state.retryCommit = null;
      } catch (error) {
        if (contextIsCurrent(context)) {
          if (error?.rollback_failed) {
            closeOnRollbackFailure(error, error.rollback_errors || []);
            return;
          }
          if (viewerReset) {
            const rolledBack = await rollbackMutation(previousDraft, context, error);
            if (!rolledBack) return;
          }
          failure = error.message || String(error);
        }
      } finally {
        if (contextIsCurrent(context) && !state.failClosed) {
          state.busy = false;
          mcpControls.setInteractionFrozen?.(Boolean(state.retryCommit));
          render();
          if (failure) setStatus(failure, 'error');
        }
      }
    }

    async function handleStepsChange(event) {
      const description = event.target.closest('[data-step-description]');
      if (description) {
        await mutateStep(description.dataset.stepDescription, { description: description.value });
        return;
      }
      const action = event.target.closest('[data-step-action]');
      if (action) await mutateStep(action.dataset.stepAction, { selected_action_id: action.value });
    }

    async function handleStepsClick(event) {
      const remove = event.target.closest('[data-delete-step]');
      if (remove) await removeStep(remove.dataset.deleteStep);
    }

    ui.phase.addEventListener('click', event => {
      const button = event.target.closest('button[data-phase]');
      if (button) setPhase(button.dataset.phase);
    });
    ui.capture.addEventListener('click', captureStep);
    ui.steps.addEventListener('change', handleStepsChange);
    ui.steps.addEventListener('click', handleStepsClick);
    ui.finalResult.addEventListener('input', async () => {
      if (!state.draft) return;
      if (state.draft.final_result === ui.finalResult.value) return;
      state.draft = markDraftChanged(state.draft, {
        final_result: ui.finalResult.value,
      });
      await persist();
      const saved = state.draft.trajectory.length;
      setStatusText(
        saved === 1 ? 'trajectory.status.saved.one' : 'trajectory.status.saved.many',
        saved === 1 ? 'Draft saved / {count} step' : 'Draft saved / {count} steps',
        { count: saved }, 'ready',
      );
    });
    ui.exportButton.addEventListener('click', exportDraft);
    ui.submit.addEventListener('click', submitTrajectory);
    ui.reset.addEventListener('click', resetAll);
    // Static chrome is built once, so a language switch has to rewrite it here.
    // Everything else on the panel is derived by render().
    function applyStaticText() {
      ui.review.textContent = t('trajectory.phase.review', 'Review');
      ui.annotate.textContent = t('trajectory.phase.trajectory', 'Trajectory');
      ui.pendingAction.setAttribute(
        'aria-label', t('trajectory.pendingAction.label', 'Ground-truth preview action'),
      );
      ui.exportButton.textContent = t('trajectory.action.export', 'Export draft');
      ui.reset.textContent = t('trajectory.action.reset', 'Reset all');
      ui.stepHeading.textContent = t('trajectory.steps.heading', 'Saved Ground Truth steps');
      // The label owns the textarea, so only its own text node may change.
      const finalText = ui.finalLabel.firstChild;
      if (finalText) finalText.nodeValue = t('trajectory.final.label', 'Final result');
    }

    function applyLanguage() {
      applyStaticText();
      const remembered = statusRender;
      const reportedText = ui.status.textContent;
      const reportedState = ui.status.dataset.state || '';
      render();
      // render() rewrites the status line, so put back what the last operation
      // reported: this component's own copy in the new language, backend and
      // thrown wording verbatim.
      if (remembered) {
        statusRender = remembered;
        remembered();
      } else {
        setStatus(reportedText, reportedState);
      }
    }

    mcpRoot?.addEventListener('sciencevision:mcp-preview', previewRecorded);
    render();

    global.SVI18n?.onChange(() => {
      // The layer offers no unsubscribe hook, so a replaced panel opts out here.
      if (instances.get(root) !== controller) return;
      applyLanguage();
    });

    const controller = Object.freeze({
      navigate,
      clear,
      invalidate,
      resetAll,
      exportPayload: () => exportPayload(state.draft),
    });
    instances.set(root, controller);
    return controller;
  }

  let defaultController = null;
  function mountDefault(options = {}) {
    if (defaultController) return defaultController;
    const mcpControls = options.mcpControls
      || global.ScienceVisionTaskMCPControls?.mountDefault?.();
    const root = options.root || mcpControls?.getTrajectoryRoot?.();
    if (!mcpControls || !root) return null;
    defaultController = create({
      ...options,
      mcpControls,
      root,
      submitTransport: options.submitTransport || global.ScienceVisionTrajectorySubmitTransport,
    });
    return defaultController;
  }

  Object.defineProperty(global, 'ScienceVisionTaskTrajectory', {
    configurable: false,
    enumerable: true,
    value: Object.freeze({
      version: 1,
      schemaVersion: SCHEMA_VERSION,
      create,
      mountDefault,
      createDraft,
      normalizePreview,
      recordPreview,
      saveStep,
      deleteStep,
      updateStep,
      exportPayload,
      submitDraft,
      unexplainedStep,
      isFormalAction,
      formalActions,
      intervalActions,
      validateCommitSelection,
      validateRetrySelection,
      viewerIdentityMatches,
      receiptViewerIdentity,
      executeCommittedCapture,
      replaySavedActions,
      isCompatibleDraft,
      hydrateDraft,
      taskKey,
      taskInput,
      markDraftChanged,
      createIndexedDBStore,
      createAuthenticatedSubmitTransport,
      configuredSubmitEndpoint,
    }),
    writable: false,
  });
}(window));
