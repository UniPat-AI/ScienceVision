(function installScienceVisionTaskMCPControls(global) {
  'use strict';

  if (global.ScienceVisionTaskMCPControls) return;

  const instances = new WeakMap();
  const DEFAULT_UTILITY_TOOLS = Object.freeze(['viewer_get_state', 'viewer_reset']);
  const NAVIGATION_TOKEN_KEY = '__sv_mcp_nav';

  // This runtime is vendored into a Chinese-first workspace, so its copy resolves
  // through the shared language layer when the host loads one and falls back to
  // the original English wording when it does not (offline packages, tests).
  function t(key, fallback, params) {
    const layer = global.SVI18n;
    return layer ? layer.t(key, fallback, params) : fallback;
  }

  function navigationURL(url, baseURI, token) {
    const parsed = new URL(url, baseURI);
    parsed.searchParams.set(NAVIGATION_TOKEN_KEY, token);
    return parsed.href;
  }

  function navigationToken(url, baseURI) {
    if (!url) return null;
    let parsed;
    try {
      parsed = new URL(url, baseURI);
    } catch (_) {
      return null;
    }
    return parsed.searchParams.get(NAVIGATION_TOKEN_KEY);
  }

  function isExpectedNavigation(actualURL, expectedURL, expectedToken, baseURI) {
    if (!actualURL || !expectedURL || !expectedToken) return false;
    let actual;
    try {
      actual = new URL(actualURL, baseURI).href;
    } catch (_) {
      return false;
    }
    return actual === expectedURL
      && navigationToken(actual, baseURI) === expectedToken;
  }

  function errorMessage(error) {
    return error && typeof error.message === 'string'
      ? error.message
      : String(error || 'Unknown viewer function error.');
  }

  // Vocabulary the viewer declares for itself over the iframe boundary: function
  // names, argument names, dropdown choices and the sentence for each. Keyed by
  // that English text, so a viewer this workspace has no wording for still reads
  // as it declared itself.
  function viewerText(kind, value) {
    const layer = global.SVI18n;
    return layer ? layer.translateViewer(kind, value) : value;
  }

  // The wording above is English, because it is also what the raw receipt
  // records. On the way to the status line it goes through the catalog, which
  // keys the Chinese wording by that same English text.
  function diagnostic(message) {
    const layer = global.SVI18n;
    return layer ? layer.translateThrown(message) : message;
  }

  function applyInteractionLock(elements, frozen) {
    const locked = Boolean(frozen);
    for (const element of elements || []) {
      if (!element) continue;
      element.inert = locked;
      element.toggleAttribute?.('data-sv-trajectory-locked', locked);
    }
  }

  function appendTextElement(documentRef, parent, tagName, className, text) {
    const element = documentRef.createElement(tagName);
    if (className) element.className = className;
    element.textContent = text;
    parent.appendChild(element);
    return element;
  }

  function buildControls(documentRef, root) {
    root.replaceChildren();
    root.classList.add('sv-mcp-controls');
    root.dataset.svMcpControls = 'true';

    const summary = documentRef.createElement('summary');
    const summaryLabel = appendTextElement(
      documentRef, summary, 'span', 'sv-mcp-summary-label', t('mcp.panel.title', 'Record GT'),
    );
    const status = appendTextElement(
      documentRef, summary, 'span', 'sv-mcp-status', t('mcp.status.noViewer', 'No viewer selected'),
    );
    status.dataset.state = 'loading';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');

    const body = documentRef.createElement('div');
    body.className = 'sv-mcp-body sv-mcp-panel';
    body.dataset.panel = 'functions';
    const command = documentRef.createElement('div');
    command.className = 'sv-mcp-command';
    const functionField = documentRef.createElement('div');
    functionField.className = 'sv-mcp-field';
    const functionLabel = appendTextElement(
      documentRef, functionField, 'label', null, t('mcp.panel.function', 'Function'),
    );
    const tool = documentRef.createElement('select');
    const toolId = `${root.id || 'sv-mcp-controls'}-tool`;
    tool.id = toolId;
    tool.disabled = true;
    functionLabel.htmlFor = toolId;
    functionField.appendChild(tool);
    const explorationLabel = documentRef.createElement('label');
    explorationLabel.className = 'sv-mcp-exploration-label';
    const exploration = documentRef.createElement('input');
    exploration.type = 'checkbox';
    exploration.disabled = true;
    const explorationText = appendTextElement(
      documentRef, explorationLabel, 'span', null,
      t('mcp.panel.exploration', 'Show non-GT exploration tools'),
    );
    explorationLabel.prepend(exploration);
    const description = documentRef.createElement('div');
    description.className = 'sv-mcp-tool-description';
    const availability = documentRef.createElement('div');
    availability.className = 'sv-mcp-availability';
    availability.setAttribute('role', 'status');
    const run = documentRef.createElement('button');
    run.className = 'sv-mcp-run';
    run.type = 'button';
    run.disabled = true;
    run.textContent = t('mcp.panel.run', 'Run preview');
    run.title = t(
      'mcp.panel.runHint',
      'Reset the viewer, replay the saved GT steps, then execute the selected'
      + ' function. Nothing is saved until you press Save step.',
    );
    // Execution feedback sits next to the button, not only in the panel header:
    // the header status is hidden whenever the panel is collapsed, which made a
    // click look like it had no effect at all.
    const feedback = documentRef.createElement('div');
    feedback.className = 'sv-mcp-feedback';
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    feedback.dataset.state = 'idle';
    feedback.textContent = t('mcp.feedback.idle', 'Pick a function, then press Run preview.');
    command.append(availability, functionField, explorationLabel, run, feedback, description);

    const params = documentRef.createElement('div');
    params.className = 'sv-mcp-params';
    const result = documentRef.createElement('pre');
    result.className = 'sv-mcp-result';
    result.setAttribute('aria-label', t('mcp.panel.resultLabel', 'Viewer function result'));
    result.textContent = t('mcp.result.empty', 'No result.');
    result.dataset.placeholder = 'true';
    const resultDetails = documentRef.createElement('details');
    resultDetails.className = 'sv-mcp-result-details';
    const resultSummary = documentRef.createElement('summary');
    resultSummary.textContent = t('mcp.panel.rawReceipt', 'Raw receipt');
    resultDetails.append(resultSummary, result);
    body.append(command, params, resultDetails);

    const tabs = documentRef.createElement('div');
    tabs.className = 'sv-mcp-tabs';
    tabs.setAttribute('role', 'tablist');
    const functionTab = documentRef.createElement('button');
    functionTab.type = 'button';
    functionTab.dataset.panelTarget = 'functions';
    functionTab.setAttribute('role', 'tab');
    functionTab.setAttribute('aria-selected', 'true');
    functionTab.textContent = t('mcp.panel.tab.functions', 'Functions');
    const trajectoryTab = documentRef.createElement('button');
    trajectoryTab.type = 'button';
    trajectoryTab.dataset.panelTarget = 'trajectory';
    trajectoryTab.setAttribute('role', 'tab');
    trajectoryTab.setAttribute('aria-selected', 'false');
    trajectoryTab.textContent = t('mcp.panel.tab.trajectory', 'Trajectory');
    tabs.append(functionTab, trajectoryTab);

    const trajectoryRoot = documentRef.createElement('div');
    trajectoryRoot.id = `${root.id || 'sv-mcp-controls'}-trajectory`;
    trajectoryRoot.className = 'sv-trajectory-root sv-mcp-panel';
    trajectoryRoot.dataset.panel = 'trajectory';
    trajectoryRoot.hidden = false;
    const content = documentRef.createElement('div');
    content.className = 'sv-mcp-content';
    content.append(body, trajectoryRoot);
    root.append(summary, content);
    return {
      status, tool, exploration, description, availability, run, feedback, params, result,
      tabs, functionTab, trajectoryTab, trajectoryRoot, body, content, resultDetails,
      summaryLabel, functionLabel, explorationText, resultSummary,
    };
  }

  function createOption(documentRef, label, value) {
    const option = documentRef.createElement('option');
    option.textContent = label;
    option.value = value;
    return option;
  }

  function initialValue(name, schema) {
    if (Object.prototype.hasOwnProperty.call(schema, 'default')) return schema.default;
    if (schema.type === 'boolean') return false;
    if (schema.type === 'number' || schema.type === 'integer') {
      if (name === 'factor' || name === 'width') return 1;
      return typeof schema.minimum === 'number' ? schema.minimum : 0;
    }
    if (schema.type === 'string') return Array.isArray(schema.enum) ? schema.enum[0] : '';
    if (schema.type === 'array') {
      const count = schema.minItems === schema.maxItems && Number.isInteger(schema.minItems)
        ? schema.minItems : 0;
      return Array.from({ length: count }, () => 0);
    }
    if (schema.type === 'object') return {};
    return null;
  }

  function makeInput(documentRef, name, schema, value) {
    if (schema.type === 'boolean') {
      const input = documentRef.createElement('input');
      input.type = 'checkbox';
      input.checked = Boolean(value);
      return { kind: 'boolean', nodes: [input], element: input };
    }
    if (schema.type === 'string' && Array.isArray(schema.enum)) {
      const select = documentRef.createElement('select');
      for (const enumValue of schema.enum) {
        select.appendChild(createOption(
          documentRef, viewerText('enum', String(enumValue)), String(enumValue),
        ));
      }
      select.value = String(value);
      return { kind: 'string', nodes: [select], element: select };
    }
    if (schema.type === 'number' || schema.type === 'integer') {
      const input = documentRef.createElement('input');
      input.type = 'number';
      input.step = schema.type === 'integer' ? '1' : 'any';
      if (typeof schema.minimum === 'number') input.min = String(schema.minimum);
      if (typeof schema.maximum === 'number') input.max = String(schema.maximum);
      input.value = String(value);
      return { kind: schema.type, nodes: [input], element: input };
    }
    const fixedNumericArray = schema.type === 'array'
      && schema.minItems === schema.maxItems
      && Number.isInteger(schema.minItems)
      && ['number', 'integer'].includes(schema.items?.type);
    if (fixedNumericArray) {
      const wrap = documentRef.createElement('div');
      wrap.className = 'sv-mcp-array';
      wrap.style.gridTemplateColumns = `repeat(${schema.minItems}, minmax(0, 1fr))`;
      const inputs = [];
      for (let index = 0; index < schema.minItems; index += 1) {
        const input = documentRef.createElement('input');
        input.type = 'number';
        input.step = schema.items.type === 'integer' ? '1' : 'any';
        input.value = String(value[index] ?? 0);
        input.setAttribute('aria-label', t(
          'mcp.field.arrayItem', `${name} ${index + 1}`,
          { name: viewerText('arg', name), index: index + 1 },
        ));
        wrap.appendChild(input);
        inputs.push(input);
      }
      return { kind: 'fixed-array', nodes: inputs, element: wrap };
    }
    if (schema.type === 'object' || schema.type === 'array') {
      const textarea = documentRef.createElement('textarea');
      textarea.value = JSON.stringify(value, null, 2);
      textarea.spellcheck = false;
      return { kind: 'json', nodes: [textarea], element: textarea };
    }
    const input = documentRef.createElement('input');
    input.type = 'text';
    input.value = String(value ?? '');
    return { kind: 'string', nodes: [input], element: input };
  }

  function policyForTool(name, allowedTools, utilityTools) {
    if (utilityTools.has(name)) {
      return {
        policy_compliant: true,
        exploration_only: false,
        policy_source: 'viewer_utility',
      };
    }
    if (allowedTools.has(name)) {
      return {
        policy_compliant: true,
        exploration_only: false,
        policy_source: 'task_allowed_tools',
      };
    }
    return {
      policy_compliant: false,
      exploration_only: true,
      policy_source: 'free_exploration_extension',
    };
  }

  function filterToolsForPolicy(tools, allowedTools, utilityTools, includeExploration) {
    return tools.filter(tool => includeExploration
      || allowedTools.has(tool.name)
      || utilityTools.has(tool.name));
  }

  function countToolsForPolicy(tools, allowedTools, utilityTools) {
    const counts = { task: 0, utility: 0, exploration: 0, total: 0 };
    for (const tool of tools || []) {
      counts.total += 1;
      if (utilityTools.has(tool.name)) counts.utility += 1;
      else if (allowedTools.has(tool.name)) counts.task += 1;
      else counts.exploration += 1;
    }
    return counts;
  }

  function normalizeCommittedCapture(capture) {
    const resultState = capture?.result_state ?? capture?.state_after;
    if (resultState == null || typeof resultState !== 'object') {
      throw new Error('Committed viewer action returned no usable result state.');
    }
    return {
      ...capture,
      result_state: resultState,
      state_after: capture.state_after ?? resultState,
    };
  }

  function staleOperationError() {
    const error = new Error('Viewer changed while the operation was running.');
    error.code = 'STALE_VIEWER_OPERATION';
    return error;
  }

  function createOperationQueue(isCurrent = () => true) {
    let tail = Promise.resolve();
    let completionSeq = 0;

    function enqueue(identity, kind, operation, onComplete) {
      const run = async () => {
        if (!isCurrent(identity)) throw staleOperationError();
        let value;
        let error = null;
        try {
          const startSequence = completionSeq;
          value = await operation(Object.freeze({
            start_seq: startSequence,
            allocateSequence: () => ++completionSeq,
          }));
          if (!isCurrent(identity)) throw staleOperationError();
        } catch (operationError) {
          error = operationError;
        }
        const completion = {
          kind,
          identity,
          completion_seq: ++completionSeq,
          value,
          error,
        };
        if (onComplete && isCurrent(identity)) await onComplete(completion);
        if (error) {
          error.completion_seq = completion.completion_seq;
          throw error;
        }
        return completion;
      };
      const result = tail.then(run, run);
      tail = result.then(() => undefined, () => undefined);
      return result;
    }

    async function whenIdle(identity) {
      const checkpoint = tail;
      await checkpoint;
      if (!isCurrent(identity)) throw staleOperationError();
      return completionSeq;
    }

    return Object.freeze({
      enqueue,
      whenIdle,
      highWaterMark: () => completionSeq,
    });
  }

  async function replayViewerActions(api, actions) {
    await api.callTool('viewer_reset', {});
    const results = [];
    for (const action of actions || []) {
      const input = action.function_input || action.input || {};
      results.push(await api.callTool(
        action.function_name || action.function,
        JSON.parse(JSON.stringify(input)),
      ));
    }
    return results;
  }

  async function requireViewerState(api) {
    const result = await api.callTool('viewer_get_state', {});
    if (result == null || typeof result !== 'object') {
      throw new Error('viewer_get_state returned no usable state.');
    }
    return result;
  }

  function create(options = {}) {
    const documentRef = options.document || global.document;
    if (!documentRef) throw new Error('Viewer function controls require a document.');
    const frame = options.frame || documentRef.getElementById('sv-task-frame');
    const root = options.root || documentRef.getElementById('sv-mcp-controls');
    if (!frame || !root) throw new Error('Viewer function controls require an iframe and controls root.');
    if (instances.has(root)) return instances.get(root);

    const layout = options.layout || root.closest('.sv-task-frame-wrap');
    const utilityTools = new Set(options.utilityTools || DEFAULT_UTILITY_TOOLS);
    const ui = buildControls(documentRef, root);
    if (options.defaultOpen === false) root.removeAttribute('open');
    else root.setAttribute('open', '');

    const state = {
      generation: 0,
      expectedURL: null,
      expectedToken: null,
      api: null,
      task: null,
      tools: [],
      providerTools: [],
      allowedTools: new Set(),
      explorationEnabled: false,
      fields: [],
      running: false,
      frozen: false,
      replayProvider: null,
      ready: null,
      viewerInstanceId: '',
    };
    let interactionRefresh = typeof options.interactionRefresh === 'function'
      ? options.interactionRefresh : null;
    let lockObserver = null;

    function taskIdentity(task = state.task) {
      return String(task?._bundle?.qualified_task_id || task?.task_id || '');
    }

    function getIdentity() {
      return Object.freeze({
        generation: state.generation,
        qualified_task_id: taskIdentity(),
        viewer_instance_id: state.viewerInstanceId,
        api: state.api,
      });
    }

    function identityMatches(identity) {
      return Boolean(identity)
        && identity.generation === state.generation
        && identity.qualified_task_id === taskIdentity()
        && identity.viewer_instance_id === state.viewerInstanceId
        && identity.api === state.api
        && identity.api === currentAPI();
    }

    const operationQueue = createOperationQueue(identityMatches);

    function newReadySignal() {
      let resolve;
      const promise = new Promise(done => { resolve = done; });
      return { promise, resolve };
    }

    function syncOpenState() {
      if (layout) layout.classList.toggle('sv-mcp-open', root.open);
    }

    let feedbackRender = null;

    function setStatus(stateName, message) {
      ui.status.dataset.state = stateName;
      ui.status.textContent = message;
    }

    // Execution outcome, rendered beside the Run button. Kept separate from
    // setStatus so the header keeps showing readiness ("biology / 3 GT") while
    // this line answers "what did my click just do?".
    //
    // What the last call reported is remembered as a recipe rather than as
    // finished text, so a language switch redraws it instead of leaving the
    // outcome in the language it happened to be drawn in. `message` is the one
    // parameter that carries a diagnostic rather than copy, so it is translated
    // at render time like the trajectory drawer's `detail`.
    function setFeedback(stateName, key, fallback, params = null) {
      feedbackRender = () => {
        const resolved = typeof params?.message === 'string'
          ? { ...params, message: diagnostic(params.message) }
          : params;
        ui.feedback.dataset.state = stateName;
        ui.feedback.textContent = t(key, fallback, resolved);
      };
      feedbackRender();
    }

    function setRunBusy(busy, label) {
      ui.run.dataset.busy = String(Boolean(busy));
      ui.run.textContent = busy
        ? (label || t('mcp.panel.running', 'Running...'))
        : t('mcp.panel.run', 'Run preview');
    }

    function showPanel(name) {
      const next = name === 'trajectory' ? 'trajectory' : 'functions';
      ui.body.hidden = false;
      ui.trajectoryRoot.hidden = false;
      for (const tab of [ui.functionTab, ui.trajectoryTab]) {
        tab.setAttribute('aria-selected', String(tab.dataset.panelTarget === next));
      }
    }

    function currentFrameHref() {
      try {
        return frame.contentWindow?.location?.href || null;
      } catch (_) {
        return null;
      }
    }

    function navigationMatches(generation, expectedURL, expectedToken) {
      return generation === state.generation
        && expectedURL === state.expectedURL
        && expectedToken === state.expectedToken
        && Boolean(expectedURL)
        && frame.dataset.mcpNavigationToken === expectedToken
        && frame.dataset.mcpExpectedUrl === expectedURL
        && isExpectedNavigation(
          currentFrameHref(), expectedURL, expectedToken, documentRef.baseURI,
        );
    }

    function currentAPI() {
      try {
        const api = frame.contentWindow?.ScienceVisionViewerMCP;
        if (api?.version === 1 && typeof api.whenReady === 'function'
            && typeof api.listTools === 'function' && typeof api.callTool === 'function') {
          return api;
        }
      } catch (_) {
        return null;
      }
      return null;
    }

    function currentScreenshotAPI() {
      try {
        const api = frame.contentWindow?.ScienceVisionViewerScreenshot;
        if (api?.version === 1 && typeof api.whenReady === 'function'
            && typeof api.captureScreenshot === 'function') return api;
      } catch (_) {
        return null;
      }
      return null;
    }

    async function viewerState(api) {
      return requireViewerState(api);
    }

    function dispatchPreview(detail) {
      const EventType = documentRef.defaultView?.CustomEvent || global.CustomEvent;
      if (!EventType) return;
      root.dispatchEvent(new EventType('sciencevision:mcp-preview', { detail }));
    }

    function receiptIdentity(identity) {
      return {
        generation: identity.generation,
        qualified_task_id: identity.qualified_task_id,
        viewer_instance_id: identity.viewer_instance_id,
      };
    }

    function applyIdleText(task) {
      ui.tool.replaceChildren(createOption(
        documentRef,
        task
          ? t('mcp.tool.waiting', 'Waiting for viewer')
          : t('mcp.tool.none', 'No viewer selected'),
        '',
      ));
      ui.availability.textContent = task
        ? t('mcp.availability.waiting', 'Waiting for viewer tool compatibility...')
        : t('mcp.availability.noTask', 'Select a task to inspect Ground Truth actions.');
      ui.availability.dataset.state = 'loading';
      setStatus('loading', task
        ? t('mcp.status.loading', 'Loading {viewer} functions...', {
          viewer: task.asset?.viewer || 'viewer',
        })
        : t('mcp.status.noViewer', 'No viewer selected'));
      if (task) {
        setFeedback(
          'idle', 'mcp.feedback.waitingViewer',
          'Waiting for the viewer to report its functions...',
        );
      } else {
        setFeedback(
          'idle', 'mcp.feedback.noTask',
          'Select a task to inspect Ground Truth actions.',
        );
      }
    }

    function resetUI(task) {
      state.api = null;
      state.task = task || null;
      state.tools = [];
      state.providerTools = [];
      state.allowedTools = new Set(task?.tool_policy?.allowed_tools || []);
      state.explorationEnabled = false;
      state.fields = [];
      state.running = false;
      ui.tool.disabled = true;
      ui.exploration.checked = false;
      ui.exploration.disabled = true;
      ui.run.disabled = true;
      ui.description.textContent = '';
      ui.params.replaceChildren();
      ui.result.textContent = t('mcp.result.empty', 'No result.');
      ui.result.dataset.placeholder = 'true';
      ui.resultDetails.open = false;
      setRunBusy(false);
      root.dataset.gtUnavailable = 'true';
      applyIdleText(task);
    }

    function getPolicy(name) {
      return policyForTool(name, state.allowedTools, utilityTools);
    }

    function readyStatus() {
      const context = [state.task?.domain, state.task?.asset?.viewer]
        .filter(Boolean).join(' / ');
      const counts = countToolsForPolicy(state.providerTools, state.allowedTools, utilityTools);
      const count = t(
        'mcp.status.counts',
        '{gt} GT / {utility} utility / {exploration} exploration',
        { gt: counts.task, utility: counts.utility, exploration: counts.exploration },
      );
      setStatus(counts.task ? 'ready' : 'error', `${context || 'viewer'} / ${count}`);
      root.dataset.gtUnavailable = String(counts.task === 0);
      if (counts.task === 0) {
        ui.availability.dataset.state = 'blocked';
        ui.availability.textContent = t(
          'mcp.availability.blocked',
          'Blocked: this task allows no function exposed by the current viewer. Non-GT tools can be previewed, but no step can be saved.',
        );
      } else {
        ui.availability.dataset.state = 'ready';
        ui.availability.textContent = t(
          counts.task === 1 ? 'mcp.availability.ready.one' : 'mcp.availability.ready.many',
          counts.task === 1
            ? '{count} Ground Truth action available. Utility and exploration actions are Non-GT and cannot be saved.'
            : '{count} Ground Truth actions available. Utility and exploration actions are Non-GT and cannot be saved.',
          { count: counts.task },
        );
      }
      if (ui.feedback.dataset.state === 'idle' || ui.feedback.dataset.state === 'loading') {
        if (counts.task) {
          setFeedback('idle', 'mcp.feedback.idle', 'Pick a function, then press Run preview.');
        } else {
          setFeedback(
            'idle', 'mcp.feedback.idleNoGT',
            'Pick a function to preview. No function here can be saved as GT.',
          );
        }
      }
    }

    function renderToolOptions(preferredName = ui.tool.value) {
      state.tools = filterToolsForPolicy(
        state.providerTools,
        state.allowedTools,
        utilityTools,
        state.explorationEnabled,
      );
      const explorationCount = state.providerTools.length
        - filterToolsForPolicy(
          state.providerTools, state.allowedTools, utilityTools, false,
        ).length;
      ui.exploration.disabled = state.running || state.frozen || explorationCount === 0;
      if (!state.tools.length) {
        ui.tool.replaceChildren(createOption(
          documentRef,
          state.explorationEnabled
            ? t('mcp.tool.noFunctions', 'No viewer functions')
            : t('mcp.tool.noCompliant', 'No task-compliant functions'),
          '',
        ));
        ui.tool.disabled = true;
        renderToolForm();
        return;
      }
      ui.tool.replaceChildren(...state.tools.map(tool => {
        const policy = getPolicy(tool.name);
        const marker = policy.policy_source === 'task_allowed_tools'
          ? t('mcp.tool.marker.gt', '[GT action] ')
          : policy.exploration_only
            ? t('mcp.tool.marker.exploration', '[Non-GT exploration] ')
            : t('mcp.tool.marker.utility', '[Non-GT utility] ');
        // The wire name stays in the label: a task names its Ground Truth actions
        // by it and every receipt records it, so the expert cross-references it.
        // The gloss follows, for reading.
        const gloss = viewerText('tool', tool.name);
        const label = gloss === tool.name ? tool.name : `${tool.name} · ${gloss}`;
        const option = createOption(documentRef, `${marker}${label}`, tool.name);
        option.dataset.policyCompliant = String(policy.policy_compliant);
        option.dataset.explorationOnly = String(policy.exploration_only);
        return option;
      }));
      if (state.tools.some(tool => tool.name === preferredName)) {
        ui.tool.value = preferredName;
      }
      ui.tool.disabled = state.running || state.frozen || !state.api;
      renderToolForm();
    }

    function renderToolForm() {
      const tool = state.tools.find(item => item.name === ui.tool.value);
      state.fields = [];
      ui.params.replaceChildren();
      const policy = tool ? getPolicy(tool.name) : null;
      const policyLabel = policy?.exploration_only
        ? t('mcp.tool.policy.exploration', '[Non-GT exploration | cannot save] ')
        : tool && policy.policy_source === 'task_allowed_tools'
          ? t('mcp.tool.policy.gt', '[Ground Truth action | savable] ')
          : tool ? t('mcp.tool.policy.utility', '[Non-GT utility | cannot save] ') : '';
      ui.description.textContent = policyLabel
        + (tool?.description ? viewerText('help', tool.description) : '');
      if (!tool) {
        ui.run.disabled = true;
        return;
      }

      const required = new Set(tool.inputSchema.required || []);
      let fieldIndex = 0;
      for (const [name, schema] of Object.entries(tool.inputSchema.properties || {})) {
        const field = documentRef.createElement('div');
        field.className = 'sv-mcp-field';
        const head = documentRef.createElement('div');
        head.className = 'sv-mcp-field-head';
        // The gloss is what is read; the wire name and the viewer's own sentence
        // stay one hover away, because the name is the key the receipt records.
        const gloss = viewerText('arg', name);
        const label = appendTextElement(
          documentRef, head, 'label', null, required.has(name) ? `${gloss} *` : gloss,
        );
        const help = schema.description ? viewerText('help', schema.description) : '';
        const hint = [gloss === name ? '' : name, help].filter(Boolean).join(' — ');
        if (hint) label.title = hint;
        const hasDefault = Object.prototype.hasOwnProperty.call(schema, 'default');
        let present = null;
        if (!required.has(name) && !hasDefault) {
          const presentLabel = documentRef.createElement('label');
          presentLabel.className = 'sv-mcp-present-label';
          present = documentRef.createElement('input');
          present.type = 'checkbox';
          appendTextElement(
            documentRef, presentLabel, 'span', null, t('mcp.field.include', 'include'),
          );
          presentLabel.prepend(present);
          head.appendChild(presentLabel);
        }

        const control = makeInput(documentRef, name, schema, initialValue(name, schema));
        const fieldId = `${root.id || 'sv-mcp'}-field-${fieldIndex}`;
        control.nodes.forEach((node, index) => {
          node.id = index === 0 ? fieldId : `${fieldId}-${index}`;
          if (present) node.disabled = true;
        });
        label.htmlFor = fieldId;
        if (present) {
          present.addEventListener('change', () => {
            control.nodes.forEach(node => { node.disabled = !present.checked; });
          });
        }
        field.append(head, control.element);
        ui.params.appendChild(field);
        state.fields.push({ name, schema, present, control });
        fieldIndex += 1;
      }
      ui.run.disabled = state.running || state.frozen || !state.api;
    }

    function readArguments() {
      const args = {};
      for (const field of state.fields) {
        if (field.present && !field.present.checked) continue;
        const { control, name } = field;
        let value;
        if (control.kind === 'boolean') value = control.nodes[0].checked;
        else if (control.kind === 'number' || control.kind === 'integer') {
          if (control.nodes[0].value.trim() === '') throw new Error(`${name} is required.`);
          value = Number(control.nodes[0].value);
          if (!Number.isFinite(value)) throw new Error(`${name} must be a finite number.`);
          if (control.kind === 'integer' && !Number.isInteger(value)) {
            throw new Error(`${name} must be an integer.`);
          }
        } else if (control.kind === 'fixed-array') {
          value = control.nodes.map((node, index) => {
            const number = Number(node.value);
            if (!Number.isFinite(number)) {
              throw new Error(`${name}[${index}] must be a finite number.`);
            }
            return number;
          });
        } else if (control.kind === 'json') {
          try {
            value = JSON.parse(control.nodes[0].value);
          } catch (error) {
            throw new Error(`${name} must be valid JSON: ${error.message}`);
          }
        } else value = control.nodes[0].value;
        args[name] = value;
      }
      return args;
    }

    async function handleFrameLoad() {
      const loadedURL = currentFrameHref();
      const loadedToken = navigationToken(loadedURL, documentRef.baseURI);
      const generation = state.generation;
      const expectedURL = state.expectedURL;
      const expectedToken = state.expectedToken;
      if (loadedToken !== expectedToken
          || !navigationMatches(generation, expectedURL, expectedToken)) return;
      const api = currentAPI();
      if (!api) {
        if (navigationMatches(generation, expectedURL, expectedToken)) {
          setStatus(
            'error',
            `Functions unavailable for ${state.task?.asset?.viewer || 'this viewer'}`,
          );
        }
        return;
      }
      setStatus('loading', `Loading ${state.task?.asset?.viewer || 'viewer'} functions...`);
      try {
        await api.whenReady();
        if (!navigationMatches(generation, expectedURL, expectedToken)
            || currentAPI() !== api) return;
        const listed = api.listTools();
        if (!Array.isArray(listed) || !listed.length) {
          throw new Error('Viewer returned no callable functions.');
        }
        const sorted = [...listed].sort((left, right) => {
          const leftPolicy = getPolicy(left.name);
          const rightPolicy = getPolicy(right.name);
          return Number(rightPolicy.policy_compliant) - Number(leftPolicy.policy_compliant)
            || Number(rightPolicy.policy_source === 'task_allowed_tools')
              - Number(leftPolicy.policy_source === 'task_allowed_tools')
            || left.name.localeCompare(right.name);
        });
        state.api = api;
        state.providerTools = sorted;
        renderToolOptions();
        readyStatus();
        state.ready?.resolve(getIdentity());
      } catch (error) {
        if (!navigationMatches(generation, expectedURL, expectedToken)
            || currentAPI() !== api) return;
        state.api = null;
        ui.run.disabled = true;
        setStatus('error', diagnostic(errorMessage(error)));
      }
    }

    async function replayCalls(api, actions) {
      return replayViewerActions(api, actions);
    }

    function jsonClone(value) {
      return value == null ? value : JSON.parse(JSON.stringify(value));
    }

    async function waitForReadyIdentity() {
      if (state.api && identityMatches(getIdentity())) return getIdentity();
      const ready = state.ready;
      const identity = await ready?.promise;
      if (!identity || !identityMatches(identity)) throw staleOperationError();
      return identity;
    }

    async function previewTool(name, args, context = {}) {
      const identity = context.identity || await waitForReadyIdentity();
      const policy = context.policy || getPolicy(name);
      const startedAt = context.startedAt || new Date().toISOString();
      const replayActions = Object.prototype.hasOwnProperty.call(context, 'replayActions')
        ? context.replayActions : state.replayProvider?.();
      const completion = await operationQueue.enqueue(identity, 'preview', async () => {
        try {
          if (Array.isArray(replayActions)) {
            context.onStage?.('replay');
            await replayCalls(identity.api, replayActions);
          }
          context.onStage?.('executing');
          const stateBefore = await viewerState(identity.api);
          const result = await identity.api.callTool(name, args);
          context.onStage?.('capturing');
          const stateAfter = await viewerState(identity.api);
          return {
            ok: true,
            function: name,
            ...policy,
            arguments: jsonClone(args),
            result: jsonClone(result),
            state_before: jsonClone(stateBefore),
            state_after: jsonClone(stateAfter),
            viewer_identity: {
              ...receiptIdentity(identity),
            },
            started_at: startedAt,
            ended_at: new Date().toISOString(),
          };
        } catch (error) {
          return {
            ok: false,
            function: name,
            ...policy,
            arguments: jsonClone(args),
            error: errorMessage(error),
            viewer_identity: {
              ...receiptIdentity(identity),
            },
            started_at: startedAt,
            ended_at: new Date().toISOString(),
          };
        }
      }, settled => {
        settled.value.completion_seq = settled.completion_seq;
        if (context.dispatch !== false) dispatchPreview(settled.value);
      });
      return completion.value;
    }

    async function runTool() {
      if (!state.api || state.running || state.frozen) return;
      const identity = getIdentity();
      const name = ui.tool.value;
      const policy = getPolicy(name);
      let args;
      try {
        args = readArguments();
      } catch (error) {
        const message = errorMessage(error);
        setStatus('error', diagnostic(message));
        setFeedback('error', 'mcp.feedback.error', '{name}: {message}', { name, message });
        ui.result.textContent = JSON.stringify({
          ok: false, function: name, ...policy, error: message,
        }, null, 2);
        ui.resultDetails.open = true;
        return;
      }

      state.running = true;
      ui.run.disabled = true;
      ui.tool.disabled = true;
      ui.exploration.disabled = true;
      setStatus('running', t('mcp.status.running', '{prefix}{name} running...', {
        prefix: policy.exploration_only ? 'exploration_only / ' : '',
        name,
      }));
      setRunBusy(true);
      setFeedback(
        'running', 'mcp.feedback.starting',
        '{name}: starting (reset -> replay -> execute)...', { name },
      );
      try {
        const receipt = await previewTool(name, args, {
          identity,
          policy,
          onStage(stage) {
            const labels = {
              replay: t('mcp.stage.replay', 'replaying saved GT steps'),
              executing: t('mcp.stage.executing', 'executing {name}', { name }),
              capturing: t('mcp.stage.capturing', 'capturing result state'),
            };
            setStatus('running', t('mcp.status.previewStage', 'Preview / {label}...', {
              label: labels[stage] || stage,
            }));
            setFeedback('running', 'mcp.feedback.stage', '{name}: {label}...', {
              name, label: labels[stage] || stage,
            });
          },
        });
        ui.result.textContent = JSON.stringify(receipt, null, 2);
        delete ui.result.dataset.placeholder;
        if (receipt.ok) {
          setStatus('success', t('mcp.status.succeeded', '{prefix}{name} succeeded', {
            prefix: policy.exploration_only ? 'exploration_only / ' : '',
            name,
          }));
          if (policy.exploration_only) {
            setFeedback(
              'success', 'mcp.feedback.explorationOnly',
              '{name} ran (exploration only - this step cannot be saved).', { name },
            );
          } else {
            setFeedback(
              'success', 'mcp.feedback.saveHint',
              '{name} ran. Press Save step to record it as Ground Truth.', { name },
            );
          }
        } else {
          setStatus('error', diagnostic(receipt.error));
          setFeedback(
            'error', 'mcp.feedback.error', '{name}: {message}',
            { name, message: receipt.error },
          );
          ui.resultDetails.open = true;
        }
      } catch (error) {
        if (!identityMatches(identity)) return;
        const message = errorMessage(error);
        ui.result.textContent = JSON.stringify({ ok: false, function: name, error: message }, null, 2);
        delete ui.result.dataset.placeholder;
        setStatus('error', diagnostic(message));
        setFeedback('error', 'mcp.feedback.error', '{name}: {message}', { name, message });
        ui.resultDetails.open = true;
      } finally {
        if (identityMatches(identity)) {
          state.running = false;
          setRunBusy(false);
          ui.run.disabled = state.frozen || !state.tools.length;
          ui.tool.disabled = state.frozen || !state.tools.length;
          ui.exploration.disabled = state.frozen
            || state.providerTools.every(tool => !getPolicy(tool.name).exploration_only);
        }
      }
    }

    function toggleExplorationTools() {
      state.explorationEnabled = ui.exploration.checked;
      renderToolOptions();
      if (state.api) readyStatus();
    }

    function navigate(task, url) {
      if (!task || !url) {
        clear();
        return;
      }
      state.generation += 1;
      state.ready?.resolve(null);
      state.ready = newReadySignal();
      state.expectedToken = `navigation-${state.generation}`;
      state.viewerInstanceId = state.expectedToken;
      state.expectedURL = navigationURL(
        url, documentRef.baseURI, state.expectedToken,
      );
      frame.dataset.mcpNavigationGeneration = String(state.generation);
      frame.dataset.mcpExpectedUrl = state.expectedURL;
      frame.dataset.mcpNavigationToken = state.expectedToken;
      resetUI(task);
      frame.src = state.expectedURL;
    }

    function clear() {
      state.generation += 1;
      state.ready?.resolve(null);
      state.ready = null;
      state.expectedURL = null;
      state.expectedToken = null;
      state.viewerInstanceId = '';
      delete frame.dataset.mcpExpectedUrl;
      delete frame.dataset.mcpNavigationToken;
      frame.dataset.mcpNavigationGeneration = String(state.generation);
      resetUI(null);
      frame.removeAttribute('src');
    }

    async function captureAtIdle(identity = getIdentity()) {
      const completion = await operationQueue.enqueue(identity, 'capture', async () => {
        const screenshotAPI = currentScreenshotAPI();
        if (!screenshotAPI) throw new Error('Screenshot capture is unavailable for this viewer.');
        await screenshotAPI.whenReady();
        if (!identityMatches(identity) || screenshotAPI !== currentScreenshotAPI()) {
          throw staleOperationError();
        }
        const resultState = await viewerState(identity.api);
        const blob = await screenshotAPI.captureScreenshot();
        if (!blob) throw new Error('Viewer screenshot capture returned no image.');
        return { blob, result_state: jsonClone(resultState) };
      });
      return { ...completion.value, completion_seq: completion.completion_seq };
    }

    async function captureScreenshot() {
      return (await captureAtIdle()).blob;
    }

    function rollbackError(primary, rollbackErrors) {
      primary.rollback_failed = rollbackErrors.length > 0;
      primary.rollback_errors = rollbackErrors;
      return primary;
    }

    async function commitStep(request, identity = getIdentity()) {
      const completion = await operationQueue.enqueue(identity, 'commit-step', async sequence => {
        const screenshotAPI = currentScreenshotAPI();
        if (!screenshotAPI) throw new Error('Screenshot capture is unavailable for this viewer.');
        await screenshotAPI.whenReady();
        if (!identityMatches(identity) || screenshotAPI !== currentScreenshotAPI()) {
          throw staleOperationError();
        }
        const trajectoryAPI = global.ScienceVisionTaskTrajectory;
        if (!trajectoryAPI) throw new Error('Trajectory runtime is unavailable.');
        const selected = request.retry_action
          ? trajectoryAPI.validateRetrySelection(
            request.retry_action,
            receiptIdentity(identity),
            state.allowedTools,
          )
          : trajectoryAPI.validateCommitSelection(
            request,
            receiptIdentity(identity),
            state.allowedTools,
            sequence.start_seq,
          );
        try {
          const startedAt = new Date().toISOString();
          const capture = await trajectoryAPI.executeCommittedCapture(
            identity.api,
            screenshotAPI,
            request.saved_actions || [],
            selected,
            request.onStage,
          );
          if (!identityMatches(identity) || screenshotAPI !== currentScreenshotAPI()) {
            throw staleOperationError();
          }
          const endedAt = new Date().toISOString();
          const executionSequence = sequence.allocateSequence();
          const normalizedCapture = normalizeCommittedCapture(capture);
          const resultState = normalizedCapture.result_state;
          return {
            ...normalizedCapture,
            result_state: jsonClone(resultState),
            state_after: jsonClone(capture.state_after ?? resultState),
            preview_cut_seq: sequence.start_seq,
            captured_at: endedAt,
            execution_receipt: {
              action_id: selected.action_id,
              function_name: selected.function_name,
              function_input: jsonClone(selected.function_input),
              result: jsonClone(capture.result),
              state_before: jsonClone(capture.state_before),
              state_after: jsonClone(resultState),
              viewer_identity: receiptIdentity(identity),
              status: 'succeeded',
              start_time: startedAt,
              end_time: endedAt,
              completion_seq: executionSequence,
            },
          };
        } catch (error) {
          const rollbackErrors = [];
          try {
            await replayCalls(identity.api, request.saved_actions || []);
          } catch (rollbackFailure) {
            rollbackErrors.push(rollbackFailure);
          }
          throw rollbackError(error, rollbackErrors);
        }
      });
      return { ...completion.value, completion_seq: completion.completion_seq };
    }

    async function recover(actions, identity = null) {
      const activeIdentity = identity || await waitForReadyIdentity();
      const completion = await operationQueue.enqueue(activeIdentity, 'replay', async () => {
        await replayCalls(activeIdentity.api, actions || []);
        return viewerState(activeIdentity.api);
      });
      return { result_state: completion.value, completion_seq: completion.completion_seq };
    }

    async function rebuildTrajectory(steps, identity = getIdentity(), rollbackSteps = []) {
      const completion = await operationQueue.enqueue(identity, 'rebuild', async sequence => {
        const screenshotAPI = currentScreenshotAPI();
        if (!screenshotAPI) throw new Error('Screenshot capture is unavailable for this viewer.');
        await screenshotAPI.whenReady();
        const disallowed = (steps || []).find(step => !state.allowedTools.has(step.function_name));
        if (disallowed) {
          throw new Error(`${disallowed.function_name} is no longer allowed by the current task.`);
        }
        try {
          await identity.api.callTool('viewer_reset', {});
          const rebuilt = [];
          for (const step of steps || []) {
            const startedAt = new Date().toISOString();
            const stateBefore = await viewerState(identity.api);
            const result = await identity.api.callTool(
              step.function_name, jsonClone(step.function_input),
            );
            const resultState = await viewerState(identity.api);
            const blob = await screenshotAPI.captureScreenshot();
            if (!blob || blob.type !== 'image/png') {
              throw new Error('Rebuilt trajectory screenshot must be PNG.');
            }
            rebuilt.push({
              result: jsonClone(result),
              state_before: jsonClone(stateBefore),
              result_state: jsonClone(resultState),
              blob,
              started_at: startedAt,
              ended_at: new Date().toISOString(),
              completion_seq: sequence.allocateSequence(),
            });
          }
          return { steps: rebuilt, start_seq: sequence.start_seq };
        } catch (error) {
          const rollbackErrors = [];
          try {
            await replayCalls(identity.api, rollbackSteps || []);
          } catch (rollbackFailure) {
            rollbackErrors.push(rollbackFailure);
          }
          throw rollbackError(error, rollbackErrors);
        }
      });
      return { ...completion.value, completion_seq: completion.completion_seq };
    }

    async function resetViewer(identity = getIdentity(), rollbackSteps = []) {
      const completion = await operationQueue.enqueue(identity, 'reset', async () => {
        try {
          return await identity.api.callTool('viewer_reset', {});
        } catch (error) {
          const rollbackErrors = [];
          try {
            await replayCalls(identity.api, rollbackSteps || []);
          } catch (rollbackFailure) {
            rollbackErrors.push(rollbackFailure);
          }
          throw rollbackError(error, rollbackErrors);
        }
      });
      return { result: completion.value, completion_seq: completion.completion_seq };
    }

    function lockTargets() {
      return documentRef.querySelectorAll(
        '[data-task], #sv-task-load, #sv-task-set, #sv-task-filter',
      );
    }

    function refreshInteractionLock() {
      applyInteractionLock([frame, ...lockTargets()], state.frozen);
      if (state.frozen) {
        const MutationObserverType = documentRef.defaultView?.MutationObserver
          || global.MutationObserver;
        if (!lockObserver && MutationObserverType && documentRef.documentElement) {
          lockObserver = new MutationObserverType(() => {
            if (state.frozen) applyInteractionLock(lockTargets(), true);
          });
          lockObserver.observe(documentRef.documentElement, {
            subtree: true,
            childList: true,
          });
        }
        renderToolOptions();
        return;
      }

      lockObserver?.disconnect();
      lockObserver = null;
      if (interactionRefresh) interactionRefresh();
      renderToolOptions();
    }

    function setInteractionFrozen(frozen) {
      state.frozen = Boolean(frozen);
      refreshInteractionLock();
    }

    function setInteractionRefresh(refresh) {
      interactionRefresh = typeof refresh === 'function' ? refresh : null;
      refreshInteractionLock();
    }

    function setReplayProvider(provider) {
      state.replayProvider = typeof provider === 'function' ? provider : null;
    }

    function destroy() {
      lockObserver?.disconnect();
      lockObserver = null;
      frame.removeEventListener('load', handleFrameLoad);
      root.removeEventListener('toggle', syncOpenState);
      ui.tool.removeEventListener('change', renderToolForm);
      ui.exploration.removeEventListener('change', toggleExplorationTools);
      ui.run.removeEventListener('click', runTool);
      ui.tabs.removeEventListener('click', handleTabClick);
      instances.delete(root);
    }

    function handleTabClick(event) {
      const tab = event.target.closest('button[data-panel-target]');
      if (tab) showPanel(tab.dataset.panelTarget);
    }

    function applyLanguage() {
      ui.summaryLabel.textContent = t('mcp.panel.title', 'Record GT');
      ui.functionLabel.textContent = t('mcp.panel.function', 'Function');
      ui.explorationText.textContent = t(
        'mcp.panel.exploration', 'Show non-GT exploration tools',
      );
      setRunBusy(state.running);
      feedbackRender?.();
      ui.run.title = t(
        'mcp.panel.runHint',
        'Reset the viewer, replay the saved GT steps, then execute the selected'
        + ' function. Nothing is saved until you press Save step.',
      );
      ui.resultSummary.textContent = t('mcp.panel.rawReceipt', 'Raw receipt');
      ui.result.setAttribute('aria-label', t('mcp.panel.resultLabel', 'Viewer function result'));
      ui.functionTab.textContent = t('mcp.panel.tab.functions', 'Functions');
      ui.trajectoryTab.textContent = t('mcp.panel.tab.trajectory', 'Trajectory');
      if (ui.result.dataset.placeholder === 'true') {
        ui.result.textContent = t('mcp.result.empty', 'No result.');
      }
      // Derived text belongs to whichever state produced it: re-run that
      // rendering rather than guessing which wording is on screen.
      if (state.api && state.providerTools.length) {
        renderToolOptions();
        readyStatus();
      } else if (!state.running) {
        applyIdleText(state.task);
      }
    }

    global.SVI18n?.onChange(() => {
      // The layer offers no unsubscribe hook, so a destroyed panel opts out here.
      if (instances.get(root) !== controller) return;
      applyLanguage();
    });

    frame.addEventListener('load', handleFrameLoad);
    root.addEventListener('toggle', syncOpenState);
    ui.tool.addEventListener('change', renderToolForm);
    ui.exploration.addEventListener('change', toggleExplorationTools);
    ui.run.addEventListener('click', runTool);
    ui.tabs.addEventListener('click', handleTabClick);
    syncOpenState();
    resetUI(null);

    const controller = Object.freeze({
      navigate,
      clear,
      destroy,
      captureScreenshot,
      captureAtIdle,
      commitStep,
      preview: previewTool,
      recover,
      rebuildTrajectory,
      resetViewer,
      whenIdle: identity => operationQueue.whenIdle(identity || getIdentity()),
      getIdentity,
      setInteractionFrozen,
      setInteractionRefresh,
      setReplayProvider,
      showPanel,
      getTask: () => state.task,
      getToolAvailability: () => countToolsForPolicy(
        state.providerTools, state.allowedTools, utilityTools,
      ),
      getTrajectoryRoot: () => ui.trajectoryRoot,
    });
    instances.set(root, controller);
    return controller;
  }

  let defaultController = null;
  function mountDefault(options = {}) {
    if (defaultController) return defaultController;
    const documentRef = options.document || global.document;
    const frame = options.frame || documentRef?.getElementById('sv-task-frame');
    const root = options.root || documentRef?.getElementById('sv-mcp-controls');
    if (!frame || !root) return null;
    defaultController = create({ ...options, document: documentRef, frame, root });
    return defaultController;
  }

  const api = Object.freeze({
    version: 1,
    create,
    mountDefault,
    navigate(task, url) {
      const controller = mountDefault();
      if (!controller) throw new Error('Default viewer function controls are unavailable.');
      controller.navigate(task, url);
    },
    clear() {
      const controller = mountDefault();
      if (controller) controller.clear();
    },
    policyForTool(name, allowed = [], utility = DEFAULT_UTILITY_TOOLS) {
      return policyForTool(name, new Set(allowed), new Set(utility));
    },
    filterTools(tools, allowed = [], utility = DEFAULT_UTILITY_TOOLS, includeExploration = false) {
      return filterToolsForPolicy(
        tools, new Set(allowed), new Set(utility), includeExploration,
      );
    },
    countTools(tools, allowed = [], utility = DEFAULT_UTILITY_TOOLS) {
      return countToolsForPolicy(tools, new Set(allowed), new Set(utility));
    },
    normalizeCommittedCapture,
    navigationURL,
    navigationToken,
    isExpectedNavigation,
    createOperationQueue,
    replayViewerActions,
    requireViewerState,
    applyInteractionLock,
  });

  Object.defineProperty(global, 'ScienceVisionTaskMCPControls', {
    configurable: false,
    enumerable: true,
    value: api,
    writable: false,
  });

  if (global.document?.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', () => mountDefault(), { once: true });
  } else {
    mountDefault();
  }
}(window));
