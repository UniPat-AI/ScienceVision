(function installScienceVisionTrajectoryModel(global) {
  'use strict';

  if (global.ScienceVisionTrajectoryModel) return;

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function deepFreeze(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
    for (const child of Object.values(value)) deepFreeze(child);
    return Object.freeze(value);
  }

  function makeId(prefix) {
    const suffix = global.crypto?.randomUUID?.()
      || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}_${suffix.replaceAll('-', '')}`;
  }

  function requireObject(value, message) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(message);
    return value;
  }

  function requireSequence(value) {
    const sequence = Number(value);
    if (!Number.isSafeInteger(sequence) || sequence < 1) {
      throw new Error('MCP receipt requires a positive operation_seq.');
    }
    return sequence;
  }

  function requireCursor(cursor) {
    requireObject(cursor, 'Trajectory cursor is required.');
    if (!String(cursor.draft_id || '')) throw new Error('Trajectory cursor requires draft_id.');
    return cursor;
  }

  function operationFromReceipt(receipt, cursor) {
    requireObject(receipt, 'Successful MCP receipt is invalid.');
    requireCursor(cursor);
    if (receipt.ok !== true || !String(receipt.function || '')
        || !receipt.state_before || !receipt.state_after) {
      throw new Error('Successful MCP receipt requires before and after state.');
    }
    requireObject(receipt.state_before, 'Successful MCP receipt requires before and after state.');
    requireObject(receipt.state_after, 'Successful MCP receipt requires before and after state.');
    const sequence = requireSequence(receipt.operation_seq);
    const operation = {
      operation_id: makeId('op'),
      operation_seq: sequence,
      parent_operation_id: cursor.parent_operation_id || null,
      tool_name: String(receipt.function),
      tool_version: '1',
      input: clone(receipt.arguments || {}),
      replay_input: clone(receipt.result?.replay_input || receipt.arguments || {}),
      state_before: clone(receipt.state_before),
      state_after: clone(receipt.state_after),
      result: clone(receipt.result || {}),
      status: 'succeeded',
      idempotency_key: `operation:${cursor.draft_id}:${sequence}`,
      started_at: String(receipt.started_at || ''),
      completed_at: String(receipt.ended_at || ''),
    };
    return deepFreeze(operation);
  }

  function failedAuditFromReceipt(receipt, cursor) {
    requireObject(receipt, 'Failed MCP receipt is invalid.');
    requireCursor(cursor);
    if (receipt.ok !== false || !String(receipt.function || '')) {
      throw new Error('Failed MCP receipt requires a function name.');
    }
    const sequence = requireSequence(receipt.operation_seq);
    return deepFreeze({
      event_type: 'operation.failed',
      operation_seq: sequence,
      idempotency_key: `operation-failed:${cursor.draft_id}:${sequence}`,
      payload: {
        tool_name: String(receipt.function),
        input: clone(receipt.arguments || {}),
        error: String(receipt.error || 'MCP function failed.'),
      },
      occurred_at: String(receipt.ended_at || receipt.started_at || ''),
    });
  }

  function submissionBlockers(state) {
    const source = state && typeof state === 'object' ? state : {};
    const keyframes = Array.isArray(source.active_keyframes) ? source.active_keyframes : [];
    const blockers = [];
    if (!keyframes.length) blockers.push('at_least_one_keyframe');
    if (Number(source.outbox_count || 0) > 0) blockers.push('outbox_pending');
    if (source.keyframe_uploading) blockers.push('keyframe_uploading');
    if (source.sync_error) blockers.push('sync_error');
    if (source.active_head_operation_id !== source.active_keyframe_operation_id) {
      blockers.push('unrecorded_operations');
    }
    return blockers;
  }

  function activeKeyframesFrom(state) {
    const source = state && typeof state === 'object' ? state : {};
    const redoIds = new Set(Array.isArray(source.redo_keyframe_ids) ? source.redo_keyframe_ids : []);
    return (Array.isArray(source.keyframes) ? source.keyframes : []).filter(
      kf => (kf?.status == null || kf.status === 'active') && !redoIds.has(kf?.keyframe_id),
    );
  }

  function activeStepsFromTarget(state, targetKeyframeId) {
    const active = activeKeyframesFrom(state);
    const index = active.findIndex(
      kf => String(kf?.keyframe_id || '') === String(targetKeyframeId || ''),
    );
    return index < 0 ? 0 : active.length - index;
  }

  function previousActiveKeyframe(state, targetKeyframeId) {
    const active = activeKeyframesFrom(state);
    const index = active.findIndex(
      kf => String(kf?.keyframe_id || '') === String(targetKeyframeId || ''),
    );
    return index > 0 ? active[index - 1] : null;
  }

  Object.defineProperty(global, 'ScienceVisionTrajectoryModel', {
    configurable: false,
    enumerable: true,
    writable: false,
    value: Object.freeze({
      version: 1,
      operationFromReceipt,
      failedAuditFromReceipt,
      submissionBlockers,
      activeStepsFromTarget,
      previousActiveKeyframe,
    }),
  });
}(window));
