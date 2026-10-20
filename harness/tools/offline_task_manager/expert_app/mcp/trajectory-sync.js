(function installScienceVisionTrajectorySync(global) {
  'use strict';

  if (global.ScienceVisionTrajectorySync) return;

  function traceNowMs() {
    try {
      return (global.performance && global.performance.now)
        ? global.performance.now() : Date.now();
    } catch (error) {
      return Date.now();
    }
  }

  function trace(event, data) {
    try { global.SVTrace?.log(event, data); } catch (error) { /* ignore */ }
  }

  function traceFlush(event, data) {
    trace(event, data);
    try { global.SVTrace?.flush?.(); } catch (error) { /* ignore */ }
  }

  const DB_NAME = 'sciencevision-expert-annotations';
  const DB_VERSION = 2;
  const DRAFT_STORE = 'trajectory-drafts';
  const OUTBOX_STORE = 'trajectory-outbox';
  const BLOB_STORE = 'trajectory-blobs';
  const DRAFT_INDEX = 'draft_seq';
  const MAX_SEQUENCE = Number.MAX_SAFE_INTEGER;

  function makeId(prefix) {
    const suffix = global.crypto?.randomUUID?.()
      || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}_${suffix.replaceAll('-', '')}`;
  }

  function requestResult(request, fallback) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error(fallback));
    });
  }

  function createAuthenticatedTransport(options = {}) {
    const documentRef = options.document || global.document;
    const fetchRef = options.fetch || global.fetch?.bind(global);
    const base = documentRef?.querySelector(
      'meta[name="sciencevision-online-base"]',
    )?.content || '';
    const csrf = documentRef?.querySelector(
      'meta[name="sciencevision-csrf-token"]',
    )?.content || '';
    if (!documentRef || typeof fetchRef !== 'function' || !base) return null;
    const drafts = new Map();

    function assignmentPath(assignmentId, suffix = '') {
      const normalizedBase = String(base).replace(/\/$/, '');
      return `${normalizedBase}/api/expert/assignments/${encodeURIComponent(assignmentId)}`
        + `/trajectory${suffix}`;
    }

    async function requestJSON(url, init = {}) {
      const response = await fetchRef(url, {credentials: 'same-origin', ...init});
      let value = {};
      try { value = await response.json(); } catch (_) { /* handled by status below */ }
      if (!response.ok) {
        const error = new Error(value.error || `Trajectory request failed: HTTP ${response.status}`);
        error.status = response.status;
        error.response = value;
        throw error;
      }
      return value;
    }

    async function load(task, hooks = {}) {
      const assignmentId = String(task?._bundle?.assignment_id || task?.assignment_id || '');
      if (!assignmentId) throw new Error('MCP-only trajectory requires assignment_id.');
      const attempt = Number(task?.attempt || task?._bundle?.attempt || 1);
      const url = `${assignmentPath(assignmentId)}?attempt=${encodeURIComponent(attempt)}`;
      let draft;
      let freshlyInitialized = false;
      try {
        draft = await requestJSON(url);
      } catch (error) {
        if (error.status !== 404) throw error;
        if (typeof hooks.getInitialState !== 'function') {
          throw new Error('Trajectory initialization requires the initial viewer state.');
        }
        const initialState = await hooks.getInitialState();
        draft = await requestJSON(assignmentPath(assignmentId, '/initialize'), {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
          body: JSON.stringify({attempt, initial_state: initialState}),
        });
        freshlyInitialized = true;
      }
      if (draft?.draft && typeof draft.draft === 'object') {
        draft = {
          ...draft.draft,
          operations: Array.isArray(draft.operations) ? draft.operations : [],
          keyframes: Array.isArray(draft.keyframes) ? draft.keyframes : [],
          audit_events: Array.isArray(draft.audit_events) ? draft.audit_events : [],
          redo_keyframe_ids: Array.isArray(draft.redo_keyframe_ids)
            ? draft.redo_keyframe_ids : [],
          effective_tools: Array.isArray(draft.effective_tools) ? draft.effective_tools : [],
        };
      }
      if (!draft?.draft_id) throw new Error('Trajectory server returned an invalid draft.');
      drafts.set(String(draft.draft_id), {assignment_id: assignmentId, attempt});
      if (freshlyInitialized && typeof hooks.onDraftInitialized === 'function') {
        // Frame 0 rides on this hook. Not awaited and never allowed to throw:
        // the draft is already authoritative, the snapshot is a side channel.
        try { hooks.onDraftInitialized(draft); } catch (error) {
          trace('initial_frame.hook.failed', {
            draft_id: draft.draft_id, detail: error?.message || String(error),
          });
        }
      }
      return draft;
    }

    async function sendOperations(payload) {
      const binding = drafts.get(String(payload?.draft_id || ''));
      if (!binding) throw new Error('Trajectory draft is not bound to an assignment.');
      return requestJSON(assignmentPath(binding.assignment_id, '/operations'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify(payload),
      });
    }

    async function history(kind, payload) {
      const binding = drafts.get(String(payload?.draft_id || ''));
      if (!binding) throw new Error('Trajectory draft is not bound to an assignment.');
      const suffixes = {
        undo: '/undo',
        redo: '/redo',
        'discard-pending': '/discard-pending',
        'delete-from': '/delete-from',
      };
      const suffix = suffixes[kind];
      if (!suffix) throw new Error('Trajectory history action is invalid.');
      return requestJSON(assignmentPath(binding.assignment_id, suffix), {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify(payload),
      });
    }

    async function sendKeyframeNote(payload) {
      const binding = drafts.get(String(payload?.draft_id || ''));
      if (!binding) throw new Error('Trajectory draft is not bound to an assignment.');
      return requestJSON(assignmentPath(binding.assignment_id, '/keyframe-note'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify(payload),
      });
    }

    // Frame 0: the picture at task open, captured in the same instant as
    // initial_state. Same multipart shape as keyframes, its own route, no
    // expected_revision -- the server treats it as write-once side data.
    async function sendInitialFrame(request) {
      const binding = drafts.get(String(request?.draft_id || ''));
      if (!binding) throw new Error('Trajectory draft is not bound to an assignment.');
      if (!(request.blob instanceof Blob) || request.blob.type !== 'image/png') {
        throw new Error('Initial frame must be a PNG blob.');
      }
      const shot = request.screenshot || {};
      const metadata = {
        draft_id: String(request.draft_id),
        screenshot: {
          sha256: shot.sha256, width: shot.width, height: shot.height,
          captured_at: shot.captured_at,
        },
      };
      const form = new FormData();
      form.append('metadata', new Blob([JSON.stringify(metadata)], {type: 'application/json'}));
      form.append('file', request.blob, `${metadata.draft_id}-initial.png`);
      return requestJSON(assignmentPath(binding.assignment_id, '/initial-frame'), {
        method: 'POST',
        headers: {'X-CSRF-Token': csrf},
        body: form,
      });
    }

    async function sendKeyframe(request) {
      const payload = request?.payload || {};
      const binding = drafts.get(String(request?.draft_id || ''));
      if (!binding) throw new Error('Trajectory draft is not bound to an assignment.');
      if (!(request.blob instanceof Blob) || request.blob.type !== 'image/png') {
        throw new Error('Queued trajectory screenshot must be a PNG blob.');
      }
      const screenshot = payload.screenshot || {};
      const screenshotMeta = {
        width: screenshot.width,
        height: screenshot.height,
        captured_at: screenshot.captured_at,
      };
      if (typeof screenshot.sha256 === 'string' && screenshot.sha256) {
        screenshotMeta.sha256 = screenshot.sha256;
      }
      const metadata = {
        draft_id: String(request.draft_id),
        expected_revision: Number(request.expected_revision),
        keyframe_id: payload.keyframe_id,
        operation_id: payload.operation_id,
        operation_seq: payload.operation_seq,
        expert_note: payload.expert_note,
        screenshot: screenshotMeta,
        idempotency_key: payload.idempotency_key,
      };
      const form = new FormData();
      form.append(
        'metadata',
        new Blob([JSON.stringify(metadata)], {type: 'application/json'}),
      );
      form.append('file', request.blob, `${payload.keyframe_id}.png`);
      const sendStarted = traceNowMs();
      traceFlush('outbox.keyframe.http.start', {
        draft_id: String(request.draft_id || ''),
        assignment_id: binding.assignment_id,
        keyframe_id: payload.keyframe_id || null,
        expected_revision: Number(request.expected_revision),
        blob_bytes: Number(request.blob.size || 0),
        transport: 'multipart',
        sha256: screenshot.sha256 || null,
      });
      try {
        const response = await requestJSON(assignmentPath(binding.assignment_id, '/keyframes'), {
          method: 'POST',
          headers: {'X-CSRF-Token': csrf},
          body: form,
        });
        trace('outbox.keyframe.http.done', {
          draft_id: String(request.draft_id || ''),
          keyframe_id: payload.keyframe_id || null,
          revision: response?.revision ?? null,
          transport: 'multipart',
          blob_bytes: Number(request.blob.size || 0),
          ms: Math.round(traceNowMs() - sendStarted),
        });
        return response;
      } catch (error) {
        traceFlush('outbox.keyframe.http.error', {
          draft_id: String(request.draft_id || ''),
          keyframe_id: payload.keyframe_id || null,
          error: String((error && error.message) || error),
          status: Number(error?.status) || null,
          transport: 'multipart',
          ms: Math.round(traceNowMs() - sendStarted),
        });
        throw error;
      }
    }

    async function reset(task) {
      const assignmentId = String(task?._bundle?.assignment_id || task?.assignment_id || '');
      if (!assignmentId) throw new Error('MCP-only trajectory requires assignment_id.');
      const attempt = Number(task?.attempt || task?._bundle?.attempt || 1);
      return requestJSON(assignmentPath(assignmentId, '/reset'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({attempt}),
      });
    }

    return Object.freeze({
      load, sendOperations, sendKeyframe, sendInitialFrame, sendKeyframeNote, history, reset,
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(
        transaction.error || new Error('Trajectory storage transaction failed.'),
      );
      transaction.onabort = () => reject(
        transaction.error || new Error('Trajectory storage transaction was aborted.'),
      );
    });
  }

  function createOutbox(options = {}) {
    const indexedDBRef = options.indexedDB || global.indexedDB;
    const KeyRange = options.IDBKeyRange || global.IDBKeyRange;
    if (!indexedDBRef || !KeyRange) throw new Error('IndexedDB trajectory storage is unavailable.');
    let opening = null;
    let activeDatabase = null;
    let flushTail = Promise.resolve();
    const knownRevisions = new Map();
    const liveBlobs = new Map();

    function database() {
      if (opening) return opening;
      opening = new Promise((resolve, reject) => {
        const request = indexedDBRef.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(DRAFT_STORE)) {
            db.createObjectStore(DRAFT_STORE, {keyPath: 'storage_key'});
          }
          if (!db.objectStoreNames.contains(OUTBOX_STORE)) {
            const store = db.createObjectStore(OUTBOX_STORE, {keyPath: 'outbox_id'});
            store.createIndex(DRAFT_INDEX, ['draft_id', 'outbox_seq'], {unique: true});
          } else {
            const store = request.transaction.objectStore(OUTBOX_STORE);
            if (!store.indexNames.contains(DRAFT_INDEX)) {
              store.createIndex(DRAFT_INDEX, ['draft_id', 'outbox_seq'], {unique: true});
            }
          }
          if (!db.objectStoreNames.contains(BLOB_STORE)) {
            db.createObjectStore(BLOB_STORE, {keyPath: 'blob_id'});
          }
        };
        request.onsuccess = () => {
          activeDatabase = request.result;
          activeDatabase.onversionchange = () => activeDatabase.close();
          resolve(activeDatabase);
        };
        request.onerror = () => reject(
          request.error || new Error('Unable to open trajectory storage.'),
        );
        request.onblocked = () => reject(new Error('Trajectory storage upgrade is blocked.'));
      });
      return opening;
    }

    async function enqueue(draftId, kind, payload, blob = null) {
      if (!String(draftId || '')) throw new Error('Outbox record requires draft_id.');
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Outbox record payload must be an object.');
      }
      const db = await database();
      const transaction = db.transaction(
        [DRAFT_STORE, OUTBOX_STORE, BLOB_STORE], 'readwrite',
      );
      const done = transactionDone(transaction);
      const drafts = transaction.objectStore(DRAFT_STORE);
      const outbox = transaction.objectStore(OUTBOX_STORE);
      const blobs = transaction.objectStore(BLOB_STORE);
      const storageKey = `trajectory-outbox:${draftId}`;
      const cursor = await requestResult(
        drafts.get(storageKey), 'Unable to read the trajectory outbox cursor.',
      );
      const outboxSequence = Number(cursor?.next_outbox_seq || 1);
      const outboxId = makeId('outbox');
      const blobId = blob ? makeId('blob') : null;
      const record = {
        outbox_id: outboxId,
        draft_id: String(draftId),
        outbox_seq: outboxSequence,
        kind,
        payload,
        blob_id: blobId,
        batch_id: null,
        expected_revision: null,
        created_at: new Date().toISOString(),
      };
      drafts.put({
        storage_key: storageKey,
        draft_id: String(draftId),
        next_outbox_seq: outboxSequence + 1,
      });
      if (blob) {
        blobs.put({
          blob_id: blobId,
          draft_id: String(draftId),
          data: blob,
          sha256: String(payload.screenshot?.sha256 || ''),
          byte_size: Number(payload.screenshot?.byte_size ?? blob.size),
          created_at: record.created_at,
        });
      }
      outbox.put(record);
      await done;
      if (blob && blobId) liveBlobs.set(blobId, blob);
      trace('outbox.enqueue', {
        kind,
        draft_id: String(draftId),
        outbox_id: outboxId,
        outbox_seq: outboxSequence,
        blob_bytes: blob ? Number(blob.size || 0) : 0,
        keyframe_id: payload.keyframe_id || null,
      });
      return record;
    }

    async function enqueueKeyframeNote(draftId, payload) {
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Outbox record payload must be an object.');
      }
      const keyframeId = String(payload.keyframe_id || '');
      if (!keyframeId) throw new Error('Keyframe note requires keyframe_id.');
      const pending = await listPending(draftId);
      const queued = pending.find(row => (
        row.kind === 'keyframe_note'
        && !Number.isSafeInteger(row.expected_revision)
        && String(row.payload?.keyframe_id || '') === keyframeId
      ));
      if (queued) {
        const db = await database();
        const transaction = db.transaction(OUTBOX_STORE, 'readwrite');
        const done = transactionDone(transaction);
        const store = transaction.objectStore(OUTBOX_STORE);
        const stored = await requestResult(
          store.get(queued.outbox_id), 'Unable to read the queued keyframe note.',
        );
        if (stored && !Number.isSafeInteger(stored.expected_revision)) {
          stored.payload = {
            ...stored.payload,
            expert_note: payload.expert_note,
          };
          store.put(stored);
          await done;
          return stored;
        }
        await done;
      }
      return enqueue(draftId, 'keyframe_note', payload);
    }

    async function listPending(draftId) {
      const db = await database();
      const transaction = db.transaction(OUTBOX_STORE, 'readonly');
      const done = transactionDone(transaction);
      const range = KeyRange.bound([String(draftId), 0], [String(draftId), MAX_SEQUENCE]);
      const rows = await requestResult(
        transaction.objectStore(OUTBOX_STORE).index(DRAFT_INDEX).getAll(range),
        'Unable to read trajectory outbox records.',
      );
      await done;
      return rows;
    }

    async function getBlob(blobId) {
      const db = await database();
      const transaction = db.transaction(BLOB_STORE, 'readonly');
      const done = transactionDone(transaction);
      const value = await requestResult(
        transaction.objectStore(BLOB_STORE).get(blobId),
        'Unable to read trajectory screenshot.',
      );
      await done;
      return value || null;
    }

    async function markAcknowledged(outboxIds) {
      const ids = Array.isArray(outboxIds) ? outboxIds : [outboxIds];
      const db = await database();
      const transaction = db.transaction([OUTBOX_STORE, BLOB_STORE], 'readwrite');
      const done = transactionDone(transaction);
      const outbox = transaction.objectStore(OUTBOX_STORE);
      const blobs = transaction.objectStore(BLOB_STORE);
      for (const outboxId of ids) {
        const record = await requestResult(
          outbox.get(outboxId), 'Unable to read acknowledged outbox record.',
        );
        if (record?.blob_id) {
          blobs.delete(record.blob_id);
          liveBlobs.delete(record.blob_id);
        }
        outbox.delete(outboxId);
      }
      await done;
    }

    async function clearDraft(draftId) {
      const db = await database();
      const transaction = db.transaction(
        [DRAFT_STORE, OUTBOX_STORE, BLOB_STORE], 'readwrite',
      );
      const done = transactionDone(transaction);
      const outbox = transaction.objectStore(OUTBOX_STORE);
      const blobs = transaction.objectStore(BLOB_STORE);
      const range = KeyRange.bound([String(draftId), 0], [String(draftId), MAX_SEQUENCE]);
      const rows = await requestResult(
        outbox.index(DRAFT_INDEX).getAll(range), 'Unable to clear trajectory outbox records.',
      );
      for (const row of rows) {
        if (row.blob_id) {
          blobs.delete(row.blob_id);
          liveBlobs.delete(row.blob_id);
        }
        outbox.delete(row.outbox_id);
      }
      transaction.objectStore(DRAFT_STORE).delete(`trajectory-outbox:${draftId}`);
      await done;
    }

    async function claimOperationBatch(pending, expectedRevision) {
      const first = pending[0];
      if (first.batch_id) {
        return pending.filter((record, index) => index === 0
          || (['operation', 'audit'].includes(record.kind)
            && record.batch_id === first.batch_id));
      }
      const candidates = [];
      for (const record of pending) {
        if (!['operation', 'audit'].includes(record.kind) || record.batch_id) break;
        candidates.push(record);
      }
      const batchId = makeId('batch');
      const db = await database();
      const transaction = db.transaction(OUTBOX_STORE, 'readwrite');
      const done = transactionDone(transaction);
      const store = transaction.objectStore(OUTBOX_STORE);
      const claimed = candidates.map(record => ({
        ...record,
        batch_id: batchId,
        expected_revision: expectedRevision,
      }));
      for (const record of claimed) store.put(record);
      await done;
      return claimed;
    }

    async function claimKeyframe(record, expectedRevision) {
      if (Number.isSafeInteger(record.expected_revision)) return record;
      const db = await database();
      const transaction = db.transaction(OUTBOX_STORE, 'readwrite');
      const done = transactionDone(transaction);
      const store = transaction.objectStore(OUTBOX_STORE);
      const stored = await requestResult(
        store.get(record.outbox_id), 'Unable to read the queued keyframe.',
      );
      if (!stored) {
        await done;
        throw new Error('Queued trajectory keyframe is unavailable.');
      }
      const claimed = Number.isSafeInteger(stored.expected_revision)
        ? stored : {...stored, expected_revision: expectedRevision};
      if (claimed !== stored) store.put(claimed);
      await done;
      return claimed;
    }

    async function flushNow(draftId, context = {}) {
      const flushStarted = traceNowMs();
      let revision = Math.max(
        Number(context.expected_revision || 0),
        Number(knownRevisions.get(String(draftId)) || 0),
      );
      const rememberRevision = value => {
        if (Number.isSafeInteger(value)) {
          revision = Math.max(revision, value);
          knownRevisions.set(String(draftId), revision);
        }
      };
      rememberRevision(revision);
      trace('outbox.flush.start', {
        draft_id: String(draftId),
        expected_revision: revision,
      });
      let lastAcknowledgment = {};
      try {
        while (true) {
          // `context.kinds` narrows the flush to some record kinds and leaves the
          // rest queued. Recovery uses it to send the audit trail on its own,
          // while the operations and keyframes behind it stay unsent.
          const queued = await listPending(draftId);
          const pending = Array.isArray(context.kinds) && context.kinds.length
            ? queued.filter(row => context.kinds.includes(row.kind))
            : queued;
          if (!pending.length) {
            const result = {...lastAcknowledgment, revision, pending: 0};
            trace('outbox.flush.done', {
              draft_id: String(draftId),
              revision,
              pending: 0,
              ms: Math.round(traceNowMs() - flushStarted),
            });
            return result;
          }
          trace('outbox.flush.item', {
            draft_id: String(draftId),
            kind: pending[0].kind,
            outbox_id: pending[0].outbox_id,
            pending_count: pending.length,
            keyframe_id: pending[0].payload?.keyframe_id || null,
          });
          if (pending[0].kind === 'keyframe_note') {
            if (typeof options.sendKeyframeNote !== 'function') {
              throw new Error('Trajectory keyframe note transport is unavailable.');
            }
            let record = pending[0];
            if (!Number.isSafeInteger(record.expected_revision)) {
              const db = await database();
              const transaction = db.transaction(OUTBOX_STORE, 'readwrite');
              const done = transactionDone(transaction);
              const store = transaction.objectStore(OUTBOX_STORE);
              const stored = await requestResult(
                store.get(record.outbox_id), 'Unable to read the queued keyframe note.',
              );
              if (!stored) {
                await done;
                throw new Error('Queued trajectory keyframe note is unavailable.');
              }
              record = Number.isSafeInteger(stored.expected_revision)
                ? stored : {...stored, expected_revision: revision};
              if (record !== stored) store.put(record);
              await done;
            }
            const request = {
              draft_id: draftId,
              expected_revision: Number(record.expected_revision),
              keyframe_id: record.payload.keyframe_id,
              expert_note: record.payload.expert_note,
              idempotency_key: record.payload.idempotency_key,
            };
            const response = await options.sendKeyframeNote(request);
            const expectedRevision = Number(record.expected_revision);
            if (response?.acknowledged !== true
              || response?.keyframe_id !== record.payload?.keyframe_id
              || !Number.isSafeInteger(response?.revision)) {
              throw new Error('Trajectory keyframe note server ACK is incomplete.');
            }
            if (response?.noop === true) {
              if (response.revision !== expectedRevision) {
                throw new Error('Trajectory keyframe note server ACK is incomplete.');
              }
            } else if (response.revision !== expectedRevision + 1) {
              throw new Error('Trajectory keyframe note server ACK is incomplete.');
            }
            lastAcknowledgment = response && typeof response === 'object' ? response : {};
            rememberRevision(response?.revision);
            await markAcknowledged(record.outbox_id);
            continue;
          }
          if (pending[0].kind === 'keyframe') {
            if (typeof options.sendKeyframe !== 'function') {
              throw new Error('Trajectory keyframe transport is unavailable.');
            }
            let record = await claimKeyframe(pending[0], revision);
            const live = liveBlobs.get(record.blob_id);
            const png = live instanceof Blob ? live : (await getBlob(record.blob_id))?.data;
            if (!(png instanceof Blob)) {
              throw new Error('Queued trajectory screenshot is unavailable.');
            }
            traceFlush('outbox.keyframe.send', {
              draft_id: String(draftId),
              outbox_id: record.outbox_id,
              keyframe_id: record.payload?.keyframe_id || null,
              expected_revision: Number(record.expected_revision),
              blob_bytes: Number(png.size || 0),
            });
            const response = await options.sendKeyframe({
              draft_id: draftId,
              expected_revision: Number(record.expected_revision),
              payload: record.payload,
              blob: png,
              outbox_id: record.outbox_id,
            });
            const expectedRevision = Number(record.expected_revision);
            if (response?.acknowledged !== true
              || !Number.isSafeInteger(expectedRevision)
              || !Number.isSafeInteger(response?.revision)
              || response?.revision !== expectedRevision + 1
              || response?.keyframe_id !== record.payload?.keyframe_id
              || !['staged', 'archived'].includes(response?.storage_status)) {
              throw new Error('Trajectory keyframe server ACK is incomplete.');
            }
            lastAcknowledgment = response && typeof response === 'object' ? response : {};
            rememberRevision(response?.revision);
            await markAcknowledged(record.outbox_id);
            continue;
          }
          const batch = await claimOperationBatch(pending, revision);
          if (typeof options.sendOperations !== 'function') {
            throw new Error('Trajectory operation transport is unavailable.');
          }
          const request = {
            draft_id: draftId,
            expected_revision: Number(batch[0].expected_revision),
            operations: batch.filter(row => row.kind === 'operation').map(row => row.payload),
            audit_events: batch.filter(row => row.kind === 'audit').map(row => row.payload),
          };
          const response = await options.sendOperations(request);
          lastAcknowledgment = response && typeof response === 'object' ? response : {};
          rememberRevision(response?.revision);
          await markAcknowledged(batch.map(row => row.outbox_id));
        }
      } catch (error) {
        traceFlush('outbox.flush.error', {
          draft_id: String(draftId),
          error: String((error && error.message) || error),
          status: Number(error?.status) || null,
          ms: Math.round(traceNowMs() - flushStarted),
        });
        throw error;
      }
    }

    function flush(draftId, context = {}) {
      const run = () => flushNow(draftId, context);
      const result = flushTail.then(run, run);
      flushTail = result.then(() => undefined, () => undefined);
      return result;
    }

    function close() {
      activeDatabase?.close();
      activeDatabase = null;
      opening = null;
      liveBlobs.clear();
    }

    return Object.freeze({
      enqueueOperation: (draftId, payload) => enqueue(draftId, 'operation', payload),
      enqueueAudit: (draftId, payload) => enqueue(draftId, 'audit', payload),
      enqueueKeyframe: (draftId, payload, blob) => enqueue(draftId, 'keyframe', payload, blob),
      enqueueKeyframeNote,
      listPending,
      getBlob,
      markAcknowledged,
      clearDraft,
      resume: listPending,
      flush,
      close,
    });
  }

  Object.defineProperty(global, 'ScienceVisionTrajectorySync', {
    configurable: false,
    enumerable: true,
    writable: false,
    value: Object.freeze({
      version: 1,
      databaseVersion: DB_VERSION,
      createOutbox,
      createAuthenticatedTransport,
    }),
  });
}(window));
