(function scienceVisionManager() {
  'use strict';

  const onlineBase = document.querySelector('meta[name="sciencevision-online-base"]')?.content || '';
  const i18n = window.SVI18n;
  const t = (key, fallback, params) => (i18n ? i18n.t(key, fallback, params) : fallback);
  const serverMessage = message => (i18n ? i18n.translateError(message) : message);
  // One select per assignment field the state actually carries, so an admin
  // can ask for "expert-b, still assigned, chemistry batch" instead of
  // guessing at a free-text query.
  const ASSIGNMENT_FILTERS = [
    {field: 'batch_id', id: 'assignment-filter-batch'},
    {field: 'expert_id', id: 'assignment-filter-expert'},
    {field: 'status', id: 'assignment-filter-status'},
    {field: 'domain', id: 'assignment-filter-domain'},
  ];
  const VIEW_TABS = ['setup', 'distribution', 'results'];
  let activeTab = 'setup';
  let assignmentFilters = {};
  // A spot check is a view over the filtered slice, never a stored decision.
  let assignmentSample = null;
  let onlineSession = null;
  let pendingPasswordReset = null;
  let oneTimePassword = '';
  let passwordResetInFlight = false;
  let state = {
    experts: [], batches: [], tasks: [], assignments: [], packages: [], bundles: [],
    active_bundles: [], results: [], review_bundles: [], accounts: [],
  };
  let credentialClearTimer = null;
  const byId = id => document.getElementById(id);

  const log = (value, error = false) => {
    const target = byId('operation-log');
    // The log holds operator data once it has run, so the placeholder key must
    // go or a language switch would overwrite the last result.
    target.removeAttribute('data-i18n');
    target.dataset.error = String(error);
    target.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  };

  const operatorResult = value => {
    if (value?.schema_version === 'sciencevision.viewer_runtime.v1') {
      return {
        runtime_id: value.runtime_id,
        file_count: value.file_count,
        byte_size: value.byte_size,
      };
    }
    if (value?.bundle_id && value?.task_file) {
      return {
        bundle_id: value.bundle_id,
        batch_id: value.batch_id,
        expert_id: value.expert_id,
        task_count: value.task_file.count,
      };
    }
    if (value?.package_id && value?.assignments) {
      return {
        package_id: value.package_id,
        batch_id: value.batch_id,
        expert_id: value.expert_id,
        task_count: value.assignments.length,
      };
    }
    return value;
  };

  const option = (value, text) => {
    const node = document.createElement('option');
    node.value = value;
    node.textContent = text;
    return node;
  };

  const cell = value => {
    const node = document.createElement('td');
    node.textContent = value == null ? '' : String(value);
    return node;
  };

  async function api(path, body) {
    const target = onlineBase ? `${onlineBase}/api/admin${path.slice(4)}` : path;
    const headers = { 'Content-Type': 'application/json' };
    if (onlineSession) headers['X-CSRF-Token'] = onlineSession.csrf;
    const response = await fetch(target, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) {
      const messages = value.errors || [value.error || t('manager.error.request', 'Request failed')];
      throw new Error(messages.map(serverMessage).join('\n'));
    }
    return value;
  }

  async function accountCredentialRequest(path, body) {
    const response = await fetch(`${onlineBase}/api/admin/accounts/${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': onlineSession.csrf,
      },
      body: JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) {
      throw new Error(serverMessage(value.error) || t('manager.error.accountUpdate', 'Account update failed'));
    }
    return value;
  }

  async function refresh() {
    const target = onlineBase ? `${onlineBase}/api/admin/overview` : '/api/overview';
    const requests = [fetch(target, { cache: 'no-store' })];
    if (onlineBase) {
      requests.push(fetch(`${onlineBase}/api/admin/review-bundles`, { cache: 'no-store' }));
      requests.push(fetch(`${onlineBase}/api/admin/accounts`, { cache: 'no-store' }));
    }
    const [response, reviewResponse, accountsResponse] = await Promise.all(requests);
    if (!response.ok) throw new Error(t('manager.error.stateLoad', 'Manager state could not be loaded'));
    if (reviewResponse && !reviewResponse.ok) {
      throw new Error(t('manager.error.reviewBundlesLoad', 'Review bundles could not be loaded'));
    }
    if (accountsResponse && !accountsResponse.ok) {
      throw new Error(t('manager.error.accountsLoad', 'Accounts could not be loaded'));
    }
    const reviewIndex = reviewResponse ? await reviewResponse.json() : { bundles: [] };
    const accountIndex = accountsResponse ? await accountsResponse.json() : { accounts: [] };
    state = {
      experts: [], batches: [], tasks: [], assignments: [], packages: [], bundles: [],
      active_bundles: [], results: [], review_bundles: [], accounts: [], ...await response.json(),
      review_bundles: Array.isArray(reviewIndex.bundles) ? reviewIndex.bundles : [],
      accounts: Array.isArray(accountIndex.accounts) ? accountIndex.accounts : [],
    };
    render();
  }

  function fillSelect(id, items) {
    const select = byId(id);
    const current = select.value;
    select.replaceChildren();
    items.forEach(item => select.append(option(item.value, item.text)));
    if (items.some(item => item.value === current)) select.value = current;
  }

  function fillTable(id, rows) {
    const body = byId(id);
    body.replaceChildren();
    rows.forEach(values => {
      const row = document.createElement('tr');
      values.forEach(value => row.append(cell(value)));
      body.append(row);
    });
  }

  function renderExperts() {
    const body = byId('experts-table');
    body.replaceChildren();
    state.experts.forEach(item => {
      const row = document.createElement('tr');
      row.append(cell(item.expert_id));
      row.append(cell(item.name));
      row.append(cell(item.domains.join(', ')));
      const status = item.online_account?.status;
      const statusLabels = {
        not_invited: t('manager.account.status.notInvited', 'Not invited'),
        password_change_required: t('manager.account.status.activationPending', 'Activation pending'),
        active: t('manager.account.status.active', 'Active'),
      };
      row.append(cell(statusLabels[status] || t('manager.account.status.offlineOnly', 'Offline only')));
      const actionCell = document.createElement('td');
      const action = document.createElement('button');
      action.type = 'button';
      action.className = 'account-action';
      action.dataset.expertId = item.expert_id;
      action.dataset.accountAction = status === 'not_invited' ? 'invite' : 'reset';
      action.textContent = status === 'not_invited'
        ? t('manager.account.invite', 'Invite')
        : t('manager.account.reissue', 'Reissue temporary password');
      action.disabled = !onlineBase || !onlineSession || !status;
      actionCell.append(action);
      row.append(actionCell);
      row.append(cell(item.updated_at));
      body.append(row);
    });
  }

  function resultStepsCell(item) {
    const node = document.createElement('td');
    const status = document.createElement('span');
    status.className = 'trajectory-status';
    status.textContent = item.trajectory_status == null ? '' : String(item.trajectory_status);
    node.append(status);
    const steps = Array.isArray(item.trajectory_steps) ? item.trajectory_steps : [];
    if (!steps.length) return node;
    // The count answers "did the expert record anything"; the reason each
    // operation mattered is what the reviewers asked to be able to read, so it
    // stays one click away instead of being crushed into the row.
    const details = document.createElement('details');
    details.className = 'result-steps';
    const summary = document.createElement('summary');
    summary.textContent = t('manager.resultsPanel.steps.summary', `${steps.length} key operations`, {
      count: steps.length,
    });
    const list = document.createElement('ol');
    steps.forEach(entry => {
      const line = document.createElement('li');
      const action = document.createElement('span');
      // The recorded action is stored data, not copy: an offline package may
      // send an action this build never listed, so it is shown verbatim.
      action.className = 'step-action';
      action.textContent = entry?.action == null ? '' : String(entry.action);
      const note = document.createElement('span');
      note.className = 'step-note';
      note.textContent = entry?.note == null ? '' : String(entry.note);
      line.append(action, note);
      list.append(line);
    });
    details.append(summary, list);
    node.append(details);
    return node;
  }

  function renderResults() {
    const body = byId('results-table');
    body.replaceChildren();
    state.results.forEach(item => {
      const row = document.createElement('tr');
      row.append(
        cell(item.task_id),
        cell(`${item.expert_name} (${item.expert_id})`),
        cell(item.validation_status),
        cell(item.validation_feedback),
        cell(item.answer),
        cell(item.reasoning),
        resultStepsCell(item),
      );
      body.append(row);
    });
  }

  function assignmentMatches(item) {
    return ASSIGNMENT_FILTERS.every(({field}) => {
      const chosen = assignmentFilters[field];
      return !chosen || String(item[field] == null ? '' : item[field]) === chosen;
    });
  }

  function filteredAssignments() {
    return state.assignments.filter(assignmentMatches);
  }

  function visibleAssignments() {
    const matching = filteredAssignments();
    if (!assignmentSample) return matching;
    return matching.filter(item => assignmentSample.has(item.assignment_id));
  }

  function sampledIds(items, size) {
    // Fisher-Yates on a local copy: a biased shuffle would quietly skew which
    // assignments ever get spot-checked.
    const pool = items.map(item => item.assignment_id);
    for (let index = pool.length - 1; index > 0; index -= 1) {
      const pick = Math.floor(Math.random() * (index + 1));
      [pool[index], pool[pick]] = [pool[pick], pool[index]];
    }
    return new Set(pool.slice(0, size));
  }

  function renderAssignmentFilters() {
    ASSIGNMENT_FILTERS.forEach(({field, id}) => {
      const select = byId(id);
      // Options come from the state, so a filter can only ask for something
      // that exists and a value that disappeared cannot hide every row.
      const values = [...new Set(
        state.assignments.map(item => String(item[field] == null ? '' : item[field])),
      )].sort();
      if (assignmentFilters[field] && !values.includes(assignmentFilters[field])) {
        assignmentFilters = {...assignmentFilters, [field]: ''};
      }
      select.replaceChildren(
        option('', t('manager.assignmentsPanel.filterAll', 'All')),
        // The recorded status, batch and domain are data, not copy: they are
        // shown exactly as the status column shows them.
        ...values.map(value => option(value, value)),
      );
      select.value = assignmentFilters[field] || '';
    });
  }

  function renderAssignmentNotice(shown, total) {
    const notice = byId('assignment-sample-status');
    if (assignmentSample) {
      notice.dataset.mode = 'sample';
      notice.hidden = false;
      notice.textContent = t(
        'manager.assignmentsPanel.sampleActive',
        'Spot-checking {shown} of {total} assignments. The sample only changes this view; '
        + 'review decisions are still written to the audit log.',
        {shown, total},
      );
      return;
    }
    if (shown !== total) {
      notice.dataset.mode = 'filter';
      notice.hidden = false;
      notice.textContent = t(
        'manager.assignmentsPanel.filterActive',
        'Showing {shown} of {total} assignments',
        {shown, total},
      );
      return;
    }
    delete notice.dataset.mode;
    notice.hidden = true;
    notice.textContent = '';
  }

  function drawAssignmentSample() {
    const matching = filteredAssignments();
    const requested = Math.floor(Number(byId('assignment-sample-size').value));
    const size = Number.isFinite(requested) && requested > 0 ? requested : 1;
    assignmentSample = matching.length ? sampledIds(matching, Math.min(size, matching.length)) : null;
    renderAssignments();
    if (matching.length) return;
    const notice = byId('assignment-sample-status');
    notice.dataset.mode = 'empty';
    notice.hidden = false;
    notice.textContent = t(
      'manager.assignmentsPanel.sampleEmpty',
      'No assignments match the current filters.',
    );
  }

  function clearAssignmentSample() {
    assignmentSample = null;
    renderAssignments();
  }

  function renderAssignments() {
    renderAssignmentFilters();
    const body = byId('assignments-table');
    const visible = visibleAssignments();
    body.replaceChildren();
    visible.forEach(item => {
      const row = document.createElement('tr');
      row.append(
        cell(item.batch_id),
        cell(item.task_id),
        cell(item.domain),
        cell(`${item.expert_name} (${item.expert_id})`),
        cell(item.status),
        cell(item.assignment_id),
      );
      const actions = document.createElement('td');
      actions.className = 'assignment-review-actions';
      if (onlineBase && item.status === 'pending_review') {
        const approve = document.createElement('button');
        approve.type = 'button';
        approve.textContent = t('manager.assignment.approve', 'Approve');
        approve.addEventListener('click', () => reviewAssignment(item, 'approved'));
        const returnButton = document.createElement('button');
        returnButton.type = 'button';
        returnButton.textContent = t('manager.assignment.return', 'Return');
        returnButton.dataset.action = 'return';
        returnButton.addEventListener('click', () => reviewAssignment(item, 'returned'));
        actions.append(approve, returnButton);
      } else {
        actions.textContent = '—';
      }
      row.append(actions);
      body.append(row);
    });
    byId('assignment-table-count').textContent = visible.length;
    renderAssignmentNotice(visible.length, state.assignments.length);
    // The free-text box hides rows in place, so it has to be re-applied to the
    // rows this render just created.
    const search = document.querySelector('[data-filter-target="assignments-table"]');
    if (search) applyTableFilter(search);
  }

  function clearCredentialResult() {
    if (credentialClearTimer) window.clearTimeout(credentialClearTimer);
    credentialClearTimer = null;
    byId('credential-result').hidden = true;
    byId('credential-username').textContent = '';
    byId('credential-password').textContent = '';
    const warning = byId('credential-warning');
    warning.setAttribute('data-i18n', 'manager.credential.warning');
    warning.textContent = t(
      'manager.credential.warning',
      'Share this temporary password securely. It will not be shown again.',
    );
  }

  function showCredentialResult(value) {
    byId('credential-username').textContent = value.account.username;
    byId('credential-password').textContent = value.password;
    if (value.durability === 'directory_sync_unconfirmed') {
      const warning = byId('credential-warning');
      warning.setAttribute('data-i18n', 'manager.credential.durabilityWarning');
      warning.textContent = t(
        'manager.credential.durabilityWarning',
        'The account was updated, but directory durability could not be confirmed. Record this credential and verify persistent storage before relying on it.',
      );
    }
    byId('credential-result').hidden = false;
    credentialClearTimer = window.setTimeout(clearCredentialResult, 5 * 60 * 1000);
  }

  async function reviewAssignment(item, targetStatus) {
    let reason;
    if (targetStatus === 'returned') {
      const supplied = window.prompt(
        t('manager.assignment.returnReasonPrompt', 'Reason for returning this assignment:'),
      );
      if (supplied === null) return;
      reason = supplied.trim();
      if (!reason) {
        log(t('manager.assignment.returnReasonRequired', 'A return reason is required.'), true);
        return;
      }
    } else if (!window.confirm(t(
      'manager.assignment.approveConfirm',
      'Approve assignment {assignment}?',
      {assignment: item.assignment_id},
    ))) {
      return;
    }
    await run(() => api('/api/assignments/review', {
      assignment_id: item.assignment_id,
      status: targetStatus,
      ...(reason ? {reason} : {}),
    }));
  }

  function applyTableFilter(input) {
    const query = input.value.trim().toLocaleLowerCase();
    const body = byId(input.dataset.filterTarget);
    Array.from(body.rows).forEach(row => {
      row.hidden = Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query);
    });
  }

  function render() {
    const counts = {
      experts: state.experts.length,
      batches: state.batches.length,
      tasks: state.tasks.length,
      assignments: state.assignments.length,
      packages: state.packages.length,
      activeBundles: state.active_bundles.length,
      results: state.results.length,
    };
    byId('expert-count').textContent = counts.experts;
    byId('batch-count').textContent = counts.batches;
    byId('task-count').textContent = counts.tasks;
    byId('assignment-count').textContent = counts.assignments;
    byId('package-count').textContent = counts.activeBundles;
    byId('result-count').textContent = counts.results;
    byId('assignment-coverage').textContent = t('manager.summary.coverage', '{percent}% coverage', {
      percent: counts.tasks ? Math.round((counts.assignments / counts.tasks) * 100) : 0,
    });
    byId('setup-nav-count').textContent = counts.experts;
    byId('distribution-nav-count').textContent = counts.assignments;
    byId('results-nav-count').textContent = counts.results;

    byId('expert-table-count').textContent = counts.experts;
    byId('batch-table-count').textContent = counts.batches;
    byId('package-table-count').textContent = counts.packages;
    byId('active-bundle-table-count').textContent = counts.activeBundles;
    byId('result-table-count').textContent = counts.results;

    renderExperts();
    fillTable('batches-table', state.batches.map(item => [
      item.batch_id,
      item.title,
      item.task_count,
      `${item.assignment_count}/${item.task_count}`,
      item.imported_at,
    ]));
    renderAssignments();
    fillTable('packages-table', state.packages.map(item => [
      item.batch_id,
      item.expert_id,
      Array.isArray(item.assignments) ? item.assignments.length : 0,
      item.package_id,
      item.created_at,
    ]));
    const expertNames = new Map(state.experts.map(item => [item.expert_id, item.name]));
    fillTable('active-bundles-table', state.bundles.filter(item => item.active).map(item => [
      `${expertNames.get(item.expert_id) || item.expert_id} (${item.expert_id})`,
      item.batch_id,
      item.bundle_id,
      item.task_count,
      item.admission_status,
      item.activated_at,
    ]));
    renderResults();

    const batches = state.batches.map(item => ({
      value: item.batch_id,
      text: `${item.batch_id} (${item.assignment_count}/${item.task_count})`,
    }));
    ['auto-batch', 'publish-batch', 'package-batch'].forEach(id => fillSelect(id, batches));
    const experts = state.experts.map(item => ({
      value: item.expert_id,
      text: `${item.name} (${item.expert_id}) · ${item.domains.join(', ')}`,
    }));
    ['explicit-expert', 'package-expert'].forEach(id => fillSelect(id, experts));
    fillSelect('explicit-task', state.tasks.filter(item => !item.assignment_id).map(item => ({
      value: `${item.batch_id}\t${item.task_id}`,
      text: `${item.batch_id} · ${item.domain} · ${item.task_id}`,
    })));
    document.querySelectorAll('[data-filter-target]').forEach(applyTableFilter);
    renderReviewBundles();
    renderAccounts();
  }

  function renderReviewBundles() {
    const section = byId('review-bundles-section');
    const list = byId('review-bundles-list');
    const bundles = Array.isArray(state.review_bundles) ? state.review_bundles : [];
    section.hidden = !onlineBase || bundles.length === 0;
    list.replaceChildren();
    bundles.forEach(bundle => {
      const article = document.createElement('article');
      article.className = 'review-bundle';
      const copy = document.createElement('div');
      const eyebrow = document.createElement('span');
      eyebrow.className = 'review-bundle-type';
      eyebrow.textContent = t('manager.reviewBundle.type', 'Checksummed static bundle');
      const title = document.createElement('h4');
      title.textContent = bundle.title;
      const meta = document.createElement('p');
      meta.textContent = t('manager.reviewBundle.meta', '{count} tasks · Not imported / read-only', {
        count: bundle.task_count,
      });
      const policy = document.createElement('small');
      policy.textContent = t(
        'manager.reviewBundle.policy',
        'Local drafts and export only. Central submit unavailable.',
      );
      copy.append(eyebrow, title, meta, policy);
      const open = document.createElement('button');
      open.type = 'button';
      open.textContent = t('manager.reviewBundle.open', 'Open viewer');
      open.addEventListener('click', () => openReviewBundle(bundle, open));
      const actions = document.createElement('div');
      actions.className = 'review-bundle-actions';
      actions.append(open);
      if (bundle.trajectory_replay === true) {
        const replay = document.createElement('button');
        replay.type = 'button';
        replay.textContent = t('manager.reviewBundle.replay', 'Replay trajectory');
        replay.addEventListener('click', () => replayReviewBundle(bundle, replay));
        actions.append(replay);
      }
      article.append(copy, actions);
      list.append(article);
    });
  }

  function renderAccounts() {
    const section = byId('accounts-section');
    const body = byId('accounts-table');
    const accounts = Array.isArray(state.accounts) ? state.accounts : [];
    section.hidden = !onlineBase;
    body.replaceChildren();
    accounts.forEach(account => {
      const row = document.createElement('tr');
      row.append(
        cell(account.display_name),
        cell(account.username),
        cell(account.role),
        cell(account.expert_id),
      );
      const action = document.createElement('td');
      if (account.role === 'expert') {
        const reset = document.createElement('button');
        reset.type = 'button';
        reset.className = 'account-reset';
        reset.textContent = t('manager.accounts.reset', 'Reset password');
        reset.addEventListener('click', () => beginPasswordReset(account));
        action.append(reset);
      } else {
        action.textContent = t('manager.accounts.notResettable', 'Not resettable here');
      }
      row.append(action);
      body.append(row);
    });
  }

  function beginPasswordReset(account) {
    const dialog = byId('password-reset-confirm-dialog');
    if (
      passwordResetInFlight
      || dialog.open
      || account?.role !== 'expert'
      || typeof account.expert_id !== 'string'
      || account.expert_id.length === 0
    ) return;
    pendingPasswordReset = {
      expertId: account.expert_id,
      displayName: account.display_name || account.username || account.expert_id,
    };
    byId('password-reset-target').textContent = `${pendingPasswordReset.displayName} (${pendingPasswordReset.expertId})`;
    byId('password-reset-error').hidden = true;
    byId('password-reset-error').textContent = '';
    dialog.showModal();
  }

  // Experts are invited, so the server always picks the temporary password and
  // forces the expert to replace it at first login. The request body therefore
  // carries the roster id and nothing else.
  async function resetExpertPasswordRequest(expertId) {
    const response = await fetch(`${onlineBase}/api/admin/accounts/reset`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': onlineSession.csrf,
      },
      body: JSON.stringify({ expert_id: expertId }),
      cache: 'no-store',
    });
    const value = await response.json();
    if (!response.ok) {
      throw new Error(serverMessage(value.error) || t('manager.reset.failed', 'Password reset failed'));
    }
    if (
      !value.account
      || typeof value.account.username !== 'string'
      || value.account.expert_id !== expertId
      || value.account.password_change_required !== true
      || typeof value.password !== 'string'
      || value.password.length === 0
      || (value.durability !== undefined && value.durability !== 'directory_sync_unconfirmed')
    ) {
      throw new Error(t('manager.reset.incomplete', 'Password reset response was incomplete'));
    }
    return value;
  }

  function clearPendingPasswordReset() {
    pendingPasswordReset = null;
    passwordResetInFlight = false;
    byId('password-reset-target').textContent = '';
    byId('password-reset-error').hidden = true;
    byId('password-reset-error').textContent = '';
    byId('password-reset-confirm').disabled = false;
    byId('password-reset-cancel').disabled = false;
  }

  function showOneTimePassword(account, password, durability) {
    oneTimePassword = password;
    const field = byId('password-reset-value');
    field.type = 'password';
    field.value = oneTimePassword;
    setRevealLabel(false);
    byId('password-reset-copy-status').textContent = '';
    byId('password-reset-account').textContent = account.display_name || account.username;
    const durabilityWarning = byId('password-reset-durability-warning');
    const directorySyncUnconfirmed = durability === 'directory_sync_unconfirmed';
    durabilityWarning.hidden = !directorySyncUnconfirmed;
    durabilityWarning.textContent = directorySyncUnconfirmed
      ? t('manager.reset.durabilityPending', 'Password is active, but durable storage confirmation is pending.')
      : '';
    byId('password-reset-secret-dialog').showModal();
  }

  function setRevealLabel(revealed) {
    const button = byId('password-reset-reveal');
    const key = revealed ? 'manager.reset.hide' : 'manager.reset.reveal';
    button.setAttribute('data-i18n', key);
    button.textContent = t(key, revealed ? 'Hide' : 'Reveal');
    button.setAttribute('aria-pressed', String(revealed));
  }

  function clearOneTimePassword() {
    oneTimePassword = '';
    const field = byId('password-reset-value');
    field.value = '';
    field.type = 'password';
    byId('password-reset-account').textContent = '';
    byId('password-reset-copy-status').textContent = '';
    byId('password-reset-durability-warning').hidden = true;
    byId('password-reset-durability-warning').textContent = '';
    setRevealLabel(false);
  }

  async function confirmPasswordReset() {
    const target = pendingPasswordReset;
    if (!target || passwordResetInFlight) return;
    const submit = byId('password-reset-confirm');
    const cancel = byId('password-reset-cancel');
    const error = byId('password-reset-error');
    passwordResetInFlight = true;
    submit.disabled = true;
    cancel.disabled = true;
    error.hidden = true;
    error.textContent = '';
    try {
      const result = await resetExpertPasswordRequest(target.expertId);
      byId('password-reset-confirm-dialog').close();
      showOneTimePassword(result.account, result.password, result.durability);
    } catch (requestError) {
      passwordResetInFlight = false;
      error.textContent = requestError.message || t('manager.reset.failed', 'Password reset failed');
      error.hidden = false;
      submit.disabled = false;
      cancel.disabled = false;
    }
  }

  async function copyOneTimePassword() {
    const status = byId('password-reset-copy-status');
    if (!oneTimePassword || !navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') {
      status.textContent = t('manager.reset.clipboardUnavailable', 'Clipboard access is unavailable.');
      return;
    }
    try {
      await navigator.clipboard.writeText(oneTimePassword);
      status.textContent = t('manager.reset.copied', 'Copied.');
    } catch (_error) {
      status.textContent = t('manager.reset.copyFailed', 'Copy failed.');
    }
  }

  async function openReviewBundle(bundle, button) {
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    button.disabled = true;
    try {
      const result = await api('/api/review-bundles/open', {
        review_id: bundle.review_id,
      });
      if (popup && !popup.closed) {
        popup.location.replace(result.viewer_url);
      } else {
        window.open(result.viewer_url, '_blank', 'noopener,noreferrer');
      }
    } catch (error) {
      if (popup && !popup.closed) popup.close();
      log(error.message || String(error), true);
    } finally {
      button.disabled = false;
    }
  }

  async function replayReviewBundle(bundle, button) {
    const popup = window.open('about:blank', '_blank');
    if (popup) popup.opener = null;
    button.disabled = true;
    try {
      const result = await api('/api/review-bundles/replay', {
        review_id: bundle.review_id,
      });
      if (popup && !popup.closed) {
        popup.location.replace(result.replay_url);
      } else {
        window.open(result.replay_url, '_blank', 'noopener,noreferrer');
      }
    } catch (error) {
      if (popup && !popup.closed) popup.close();
      log(error.message || String(error), true);
    } finally {
      button.disabled = false;
    }
  }

  function activateTab(tab) {
    if (!VIEW_TABS.includes(tab)) return;
    activeTab = tab;
    document.querySelectorAll('[data-tab]').forEach(item => {
      item.dataset.active = String(item.dataset.tab === tab);
    });
    document.querySelectorAll('[data-view]').forEach(view => {
      view.hidden = view.dataset.view !== tab;
    });
    // The heading pair stays a translated placeholder, so it keeps its key and
    // only the key changes when the operator moves between views.
    const title = byId('view-title');
    const description = byId('view-description');
    title.setAttribute('data-i18n', `manager.view.${tab}.title`);
    description.setAttribute('data-i18n', `manager.view.${tab}.description`);
    title.textContent = t(`manager.view.${tab}.title`);
    description.textContent = t(`manager.view.${tab}.description`);
    if (tab !== 'setup') clearCredentialResult();
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  }

  async function run(action, statusId = '') {
    const status = statusId ? byId(statusId) : null;
    const submit = status?.closest('form')?.querySelector('button[type="submit"]') || null;
    if (status) {
      // The status line reports run outcomes, so its placeholder key must go.
      status.removeAttribute('data-i18n');
      status.dataset.state = 'working';
      status.textContent = t(
        'manager.publish.validating',
        'Validating every expert bundle before activation...',
      );
    }
    if (submit) submit.disabled = true;
    let result;
    try {
      result = await action();
      log(operatorResult(result));
      if (status) {
        status.dataset.state = 'success';
        status.textContent = t(
          'manager.publish.success',
          '{count} expert bundle(s) activated atomically.',
          {count: result.published},
        );
      }
    } catch (error) {
      log(error.message || String(error), true);
      if (status) {
        status.dataset.state = 'error';
        status.textContent = t(
          'manager.publish.failed',
          'Publish failed. Active bundles were not changed. {error}',
          {error: error.message || error},
        );
      }
      if (submit) submit.disabled = false;
      return null;
    }
    try {
      await refresh();
    } catch (error) {
      log({
        result: operatorResult(result),
        refresh_error: error.message || String(error),
      }, true);
      if (status) {
        status.textContent += ` ${t(
          'manager.publish.refreshFailed',
          'The publish succeeded, but status refresh failed; use Refresh.',
        )}`;
      }
    } finally {
      if (submit) submit.disabled = false;
    }
    return result;
  }

  document.querySelectorAll('[data-tab]').forEach(button => {
    button.addEventListener('click', () => activateTab(button.dataset.tab));
  });
  document.querySelectorAll('[data-summary-tab]').forEach(button => {
    button.addEventListener('click', () => activateTab(button.dataset.summaryTab));
  });
  document.querySelectorAll('[data-filter-target]').forEach(input => {
    input.addEventListener('input', () => applyTableFilter(input));
  });
  ASSIGNMENT_FILTERS.forEach(({field, id}) => {
    byId(id).addEventListener('change', event => {
      assignmentFilters = {...assignmentFilters, [field]: event.target.value};
      // A sample drawn from a wider slice would leave rows that no longer
      // belong to what the admin asked for, so narrowing drops it.
      assignmentSample = null;
      renderAssignments();
    });
  });
  byId('assignment-sample').addEventListener('click', drawAssignmentSample);
  byId('assignment-sample-clear').addEventListener('click', clearAssignmentSample);
  byId('refresh').addEventListener('click', async () => {
    try {
      await refresh();
      log(t('manager.status.refreshed', 'Manager state refreshed.'));
    } catch (error) {
      log(error.message || String(error), true);
    }
  });
  byId('credential-dismiss').addEventListener('click', clearCredentialResult);
  window.addEventListener('pagehide', clearCredentialResult);
  byId('experts-table').addEventListener('click', async event => {
    const button = event.target.closest('[data-account-action]');
    if (!button || !onlineBase || !onlineSession) return;
    const action = button.dataset.accountAction;
    if (
      action === 'reset'
      && !window.confirm(t(
        'manager.account.reissueConfirm',
        'Reissue this expert password? Existing sessions will be revoked.',
      ))
    ) return;
    clearCredentialResult();
    const status = byId('credential-status');
    status.textContent = action === 'invite'
      ? t('manager.account.inviting', 'Creating invitation...')
      : t('manager.account.reissuing', 'Reissuing password...');
    button.disabled = true;
    try {
      const body = {expert_id: button.dataset.expertId};
      const value = await accountCredentialRequest(action, body);
      showCredentialResult(value);
      status.textContent = t(
        'manager.account.created',
        'Temporary password created. It is shown only in the secure panel above.',
      );
      await refresh();
    } catch (error) {
      status.textContent = error.message || String(error);
      button.disabled = false;
    }
  });
  byId('logout').addEventListener('click', async () => {
    if (!onlineBase || !onlineSession) return;
    await fetch(`${onlineBase}/api/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': onlineSession.csrf },
      body: '{}',
    });
    location.replace(`${onlineBase}/login/`);
  });
  byId('experts-file').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (file) byId('experts-csv').value = await file.text();
  });
  byId('experts-form').addEventListener('submit', event => {
    event.preventDefault();
    run(() => api('/api/experts/import', { csv: byId('experts-csv').value }));
  });
  byId('batch-form').addEventListener('submit', event => {
    event.preventDefault();
    run(() => api('/api/batches/import', {
      batch_id: byId('batch-id').value,
      title: byId('batch-title').value,
      tasks_jsonl: byId('tasks-jsonl').value,
      asset_root: byId('asset-root').value,
      admission_receipt: byId('admission-receipt').value,
      admission_purpose: byId('admission-purpose').value,
      viewer_root: byId('viewer-root').value,
    }));
  });
  byId('auto-form').addEventListener('submit', event => {
    event.preventDefault();
    run(() => api('/api/assignments/auto', { batch_id: byId('auto-batch').value }));
  });
  byId('explicit-form').addEventListener('submit', event => {
    event.preventDefault();
    const [batch_id, task_id] = byId('explicit-task').value.split('\t');
    run(() => api('/api/assignments/explicit', {
      batch_id,
      task_id,
      expert_id: byId('explicit-expert').value,
    }));
  });
  byId('publish-form').addEventListener('submit', event => {
    event.preventDefault();
    run(
      () => api('/api/bundles/publish', {batch_id: byId('publish-batch').value}),
      'publish-status',
    );
  });
  byId('package-form').addEventListener('submit', event => {
    event.preventDefault();
    run(() => api('/api/packages/build', {
      batch_id: byId('package-batch').value,
      expert_id: byId('package-expert').value,
      output: byId('package-output').value,
      mode: byId('package-mode').value,
    }));
  });
  byId('runtime-form').addEventListener('submit', event => {
    event.preventDefault();
    run(() => api('/api/runtime/build', {
      viewer_root: byId('runtime-viewer-root').value,
      output: byId('runtime-output').value,
    }));
  });
  byId('legacy-form').addEventListener('submit', event => {
    event.preventDefault();
    run(() => api('/api/bundles/import-legacy', {
      package_root: byId('legacy-package-root').value,
      activate: byId('legacy-activate').checked,
      allow_historical_missing_checksums: byId('legacy-missing-checksums').checked,
    }));
  });
  byId('results-form').addEventListener('submit', event => {
    event.preventDefault();
    const file = byId('results-file').files[0];
    if (!file) return;
    run(async () => api('/api/results/import', { submission: JSON.parse(await file.text()) }));
  });
  byId('password-reset-confirm-form').addEventListener('submit', event => {
    event.preventDefault();
    confirmPasswordReset();
  });
  byId('password-reset-cancel').addEventListener('click', () => {
    if (!passwordResetInFlight) byId('password-reset-confirm-dialog').close();
  });
  byId('password-reset-confirm-dialog').addEventListener('close', clearPendingPasswordReset);
  byId('password-reset-confirm-dialog').addEventListener('cancel', event => {
    if (passwordResetInFlight) event.preventDefault();
  });
  byId('password-reset-reveal').addEventListener('click', () => {
    const field = byId('password-reset-value');
    const revealed = field.type === 'password';
    field.type = revealed ? 'text' : 'password';
    setRevealLabel(revealed);
  });
  byId('password-reset-copy').addEventListener('click', copyOneTimePassword);
  byId('password-reset-secret-dialog').addEventListener('close', clearOneTimePassword);

  (async () => {
    if (onlineBase) {
      const response = await fetch(`${onlineBase}/api/session`, { cache: 'no-store' });
      if (!response.ok) {
        location.replace(`${onlineBase}/login/`);
        return;
      }
      onlineSession = await response.json();
      if (onlineSession.role !== 'admin') {
        location.replace(`${onlineBase}/expert/`);
        return;
      }
      const adminName = byId('admin-name');
      // The signed-in operator's name is data, not translated copy.
      adminName.removeAttribute('data-i18n');
      adminName.textContent = onlineSession.display_name || onlineSession.username;
      byId('logout').hidden = false;
      document.querySelectorAll('.export-panel a').forEach(link => {
        const format = new URL(link.href).searchParams.get('format');
        link.href = `${onlineBase}/api/admin/results/export?format=${format}`;
      });
    }
    await refresh();
  })().catch(error => log(error.message, true));

  i18n?.onChange(() => {
    render();
    activateTab(activeTab);
  });
}());
