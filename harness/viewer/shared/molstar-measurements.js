(function installScienceVisionMolstarMeasurements() {
  'use strict';

  if (window.ScienceVisionInstallMolstarMeasurements) return;

  const PANEL_ID = 'sv-mol-measure';
  const MODE_INFO = Object.freeze({
    distance: Object.freeze({ label: 'Distance', points: 2, method: 'addDistance' }),
    angle: Object.freeze({ label: 'Angle', points: 3, method: 'addAngle' }),
    dihedral: Object.freeze({ label: 'Dihedral', points: 4, method: 'addDihedral' }),
  });
  const MEASUREMENT_KINDS = Object.freeze(['distances', 'angles', 'dihedrals']);
  const AXIS_COLORS = Object.freeze({ x: 0xe74c3c, y: 0x2ecc71, z: 0x4c78ff });

  function vecSub(a, b) {
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  }

  function vecDot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function vecCross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }

  function vecLength(a) {
    return Math.hypot(a[0], a[1], a[2]);
  }

  function distance3(a, b) {
    return vecLength(vecSub(a, b));
  }

  function angle3(a, b, c) {
    const ba = vecSub(a, b);
    const bc = vecSub(c, b);
    const denominator = vecLength(ba) * vecLength(bc);
    if (!(denominator > 1e-12)) return null;
    const cosine = Math.max(-1, Math.min(1, vecDot(ba, bc) / denominator));
    return Math.acos(cosine) * 180 / Math.PI;
  }

  function dihedral4(a, b, c, d) {
    const b0 = vecSub(a, b);
    const b1 = vecSub(c, b);
    const b2 = vecSub(d, c);
    const b1Length = vecLength(b1);
    if (!(b1Length > 1e-12)) return null;
    const b1Unit = b1.map(value => value / b1Length);
    const v = vecSub(b0, b1Unit.map(value => value * vecDot(b0, b1Unit)));
    const w = vecSub(b2, b1Unit.map(value => value * vecDot(b2, b1Unit)));
    if (!(vecLength(v) > 1e-12) || !(vecLength(w) > 1e-12)) return null;
    return Math.atan2(vecDot(vecCross(b1Unit, v), w), vecDot(v, w)) * 180 / Math.PI;
  }

  function measurementParentRefs(state, kinds = MEASUREMENT_KINDS) {
    const refs = new Set();
    for (const kind of kinds) {
      for (const cell of state?.[kind] || []) {
        const parent = cell?.transform?.parent;
        if (parent) refs.add(parent);
      }
    }
    return Array.from(refs);
  }

  // Mol* OrderedSet uses either a sorted typed array or an IntTuple packed into
  // a Float64. Picking one atom normally produces the packed singleton form.
  function orderedSetInfo(indices) {
    if (typeof indices === 'number') {
      const buffer = new ArrayBuffer(8);
      new Float64Array(buffer)[0] = indices;
      const tuple = new Int32Array(buffer);
      return { first: tuple[0], size: tuple[1] - tuple[0] };
    }
    const size = Number(indices?.length || 0);
    return { first: size ? Number(indices[0]) : -1, size };
  }

  function columnValue(column, index) {
    if (!column || typeof column.value !== 'function' || index < 0) return '';
    const value = column.value(index);
    return value == null || value === '.' || value === '?' ? '' : value;
  }

  function singleAtomFromLoci(loci) {
    if (!loci || loci.kind !== 'element-loci' || !Array.isArray(loci.elements)) {
      throw new Error('Only atoms can be measurement points.');
    }
    let picked = null;
    let count = 0;
    for (const element of loci.elements) {
      const info = orderedSetInfo(element.indices);
      count += info.size;
      if (info.size === 1 && !picked) picked = { unit: element.unit, localIndex: info.first };
    }
    if (count !== 1 || !picked) {
      throw new Error('Please click an atom sphere, not a bond or residue.');
    }

    const { unit, localIndex } = picked;
    if (unit?.kind !== 0 || !unit.model?.atomicHierarchy || !unit.elements) {
      throw new Error('This point is not an atomic model element.');
    }
    const element = Number(unit.elements[localIndex]);
    if (!Number.isInteger(element)) throw new Error('The selected atom index is unavailable.');

    const coordinate = [0, 0, 0];
    unit.conformation.position(element, coordinate);
    if (!coordinate.every(Number.isFinite)) throw new Error('The selected atom has invalid coordinates.');

    const hierarchy = unit.model.atomicHierarchy;
    const atoms = hierarchy.atoms || {};
    const residues = hierarchy.residues || {};
    const chains = hierarchy.chains || {};
    const residueIndex = Number(hierarchy.residueAtomSegments?.index?.[element] ?? -1);
    const chainIndex = Number(hierarchy.chainAtomSegments?.index?.[element] ?? -1);
    const chain = String(columnValue(chains.auth_asym_id, chainIndex)
      || columnValue(chains.label_asym_id, chainIndex));
    const authSequenceValue = columnValue(residues.auth_seq_id, residueIndex);
    const labelSequenceValue = columnValue(residues.label_seq_id, residueIndex);
    const authSequence = authSequenceValue === '' ? NaN : Number(authSequenceValue);
    const labelSequence = labelSequenceValue === '' ? NaN : Number(labelSequenceValue);
    const sequence = Number.isFinite(authSequence) ? authSequence
      : (Number.isFinite(labelSequence) ? labelSequence : '');
    const insertionCode = String(columnValue(residues.pdbx_PDB_ins_code, residueIndex));
    const residue = String(columnValue(atoms.auth_comp_id, element)
      || columnValue(atoms.label_comp_id, element));
    const atom = String(columnValue(atoms.auth_atom_id, element)
      || columnValue(atoms.label_atom_id, element));
    const symbol = String(columnValue(atoms.type_symbol, element));
    const residueId = `${chain ? `${chain}:` : ''}${sequence}${insertionCode}`;
    const residueText = [residueId, residue].filter(Boolean).join(' ');
    const atomText = atom || `${symbol || 'Atom'} ${element + 1}`;

    return {
      loci,
      coordinate: coordinate.map(Number),
      identity: [residueText, atomText].filter(Boolean).join(' / '),
      unitId: Number(unit.id),
      element,
    };
  }

  function formatValue(mode, points) {
    let value = null;
    if (mode === 'distance') value = distance3(points[0].coordinate, points[1].coordinate);
    if (mode === 'angle') value = angle3(points[0].coordinate, points[1].coordinate, points[2].coordinate);
    if (mode === 'dihedral') {
      value = dihedral4(
        points[0].coordinate,
        points[1].coordinate,
        points[2].coordinate,
        points[3].coordinate,
      );
    }
    if (!Number.isFinite(value)) return '';
    return mode === 'distance' ? `${value.toFixed(3)} Å` : `${value.toFixed(1)}°`;
  }

  function buildButton(icon, label, title, className = '') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `sv-mol-measure__button ${className}`.trim();
    button.title = title;
    const iconElement = document.createElement('span');
    iconElement.className = 'sv-mol-measure__icon';
    iconElement.setAttribute('aria-hidden', 'true');
    iconElement.textContent = icon;
    const labelElement = document.createElement('span');
    labelElement.textContent = label;
    button.append(iconElement, labelElement);
    return button;
  }

  function buildPanel() {
    const panel = document.createElement('section');
    panel.id = PANEL_ID;
    panel.className = 'sv-mol-measure';
    panel.setAttribute('aria-label', 'Molecular measurement tools');

    const head = document.createElement('div');
    head.className = 'sv-mol-measure__head';
    const title = document.createElement('strong');
    title.textContent = 'Measure';
    const axes = document.createElement('span');
    axes.className = 'sv-mol-measure__axes';
    axes.append('Structure Cartesian coordinates (Å)  ');
    for (const axis of ['X', 'Y', 'Z']) {
      const axisLabel = document.createElement('b');
      axisLabel.className = `sv-mol-measure__axis sv-mol-measure__axis--${axis.toLowerCase()}`;
      axisLabel.textContent = axis;
      axes.append(axisLabel, axis === 'Z' ? '' : ' ');
    }
    const collapse = document.createElement('button');
    collapse.type = 'button';
    collapse.className = 'sv-mol-measure__collapse';
    collapse.textContent = '−';
    collapse.title = 'Collapse measurement panel';
    collapse.setAttribute('aria-label', 'Collapse measurement panel');
    head.append(title, axes, collapse);

    const body = document.createElement('div');
    body.className = 'sv-mol-measure__body';
    const toolbar = document.createElement('div');
    toolbar.className = 'sv-mol-measure__toolbar';
    toolbar.setAttribute('role', 'toolbar');
    toolbar.setAttribute('aria-label', 'Measurement mode');

    const navigate = buildButton('↖', 'Navigate', 'Exit measurement mode and restore camera navigation');
    navigate.dataset.mode = 'navigate';
    const distance = buildButton('↔', 'Distance', 'Measure the 3D distance between two atoms');
    distance.dataset.mode = 'distance';
    const angle = buildButton('∠', 'Angle', 'Measure a three-atom angle; the second atom is the vertex');
    angle.dataset.mode = 'angle';
    const dihedral = buildButton('∿', 'Dihedral', 'Measure a four-atom dihedral angle');
    dihedral.dataset.mode = 'dihedral';
    const undo = buildButton('↶', 'Undo', 'Remove the latest pending point or measurement', 'sv-mol-measure__button--action');
    undo.dataset.action = 'undo';
    const clear = buildButton('×', 'Clear', 'Clear all distance, angle and dihedral measurements', 'sv-mol-measure__button--action');
    clear.dataset.action = 'clear';
    toolbar.append(navigate, distance, angle, dihedral, undo, clear);

    const feedback = document.createElement('div');
    feedback.className = 'sv-mol-measure__feedback';
    const status = document.createElement('div');
    status.className = 'sv-mol-measure__status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const progress = document.createElement('span');
    progress.className = 'sv-mol-measure__progress';
    status.append(progress);
    const statusText = document.createElement('span');
    statusText.className = 'sv-mol-measure__status-text';
    status.append(statusText);
    const picks = document.createElement('ol');
    picks.className = 'sv-mol-measure__picks';
    feedback.append(status, picks);

    const hint = document.createElement('p');
    hint.className = 'sv-mol-measure__hint';
    hint.textContent = 'Click atoms in order. Endpoints are atom-bound; free-space dragging is not used.';
    body.append(toolbar, feedback, hint);
    panel.append(head, body);
    document.body.appendChild(panel);

    return {
      panel, body, collapse, toolbar, status, statusText, progress, picks,
      modeButtons: { navigate, distance, angle, dihedral }, undo, clear,
    };
  }

  function enableCoordinateAxes(plugin) {
    const canvas3d = plugin?.canvas3d;
    if (!canvas3d?.setProps) return false;
    const current = canvas3d.props?.camera?.helper?.axes;
    const defaults = {
      alpha: 0.78,
      colorX: AXIS_COLORS.x,
      colorY: AXIS_COLORS.y,
      colorZ: AXIS_COLORS.z,
      scale: 0.33,
      location: 'bottom-left',
      locationOffsetX: 0,
      locationOffsetY: 0,
      originColor: 0x808080,
      radiusScale: 0.075,
      showPlanes: false,
      planeColorXY: 0x808080,
      planeColorXZ: 0x808080,
      planeColorYZ: 0x808080,
      showLabels: true,
      labelX: 'X',
      labelY: 'Y',
      labelZ: 'Z',
      labelColorX: AXIS_COLORS.x,
      labelColorY: AXIS_COLORS.y,
      labelColorZ: AXIS_COLORS.z,
      labelOpacity: 1,
      labelScale: 0.28,
    };
    const params = current?.name === 'on' ? { ...defaults, ...current.params } : defaults;
    Object.assign(params, {
      colorX: AXIS_COLORS.x,
      colorY: AXIS_COLORS.y,
      colorZ: AXIS_COLORS.z,
      showPlanes: false,
      showLabels: true,
      labelX: 'X',
      labelY: 'Y',
      labelZ: 'Z',
      labelColorX: AXIS_COLORS.x,
      labelColorY: AXIS_COLORS.y,
      labelColorZ: AXIS_COLORS.z,
    });
    canvas3d.setProps({ camera: { helper: { axes: { name: 'on', params } } } });
    return true;
  }

  async function deleteStateRefs(plugin, refs) {
    const unique = Array.from(new Set(refs)).filter(ref => plugin.state.data.cells.has(ref));
    if (!unique.length) return 0;
    const update = plugin.state.data.build();
    for (const ref of unique) update.delete(ref);
    await update.commit({ canUndo: false });
    return unique.length;
  }

  let activeInstance = null;

  function install(viewer) {
    if (activeInstance) return activeInstance;
    const plugin = viewer?.plugin;
    const manager = plugin?.managers?.structure?.measurement;
    const selection = plugin?.managers?.structure?.selection;
    const interactivity = plugin?.managers?.interactivity;
    if (!plugin || !manager || !selection || !interactivity) {
      throw new Error('Mol* measurement or selection managers are unavailable.');
    }

    const previousSelectionMode = Boolean(plugin.selectionMode);
    const previousGranularity = interactivity.props?.granularity;
    const previousAxes = plugin.canvas3d?.props?.camera?.helper?.axes;
    enableCoordinateAxes(plugin);
    interactivity.setProps({ granularity: 'element' });
    const ui = buildPanel();
    const createdRefs = [];
    const subscriptions = [];
    let mode = null;
    let currentPicks = [];
    let lastMessage = '';
    let errorMessage = '';
    let busy = false;
    let collapsed = false;
    let changingSelectionMode = false;
    let historyRevision = 0;
    let historyWorker = null;
    let destroyed = false;

    function setPluginSelectionMode(enabled) {
      changingSelectionMode = true;
      plugin.selectionMode = enabled;
      changingSelectionMode = false;
    }

    function measurementCount() {
      return MEASUREMENT_KINDS.reduce((sum, kind) => sum + (manager.state?.[kind]?.length || 0), 0);
    }

    function hasUndoableMeasurement() {
      return createdRefs.some(ref => plugin.state.data.cells.has(ref));
    }

    function render() {
      if (destroyed) return;
      const info = mode ? MODE_INFO[mode] : null;
      ui.panel.classList.toggle('is-busy', busy);
      ui.panel.classList.toggle('is-collapsed', collapsed);
      ui.body.hidden = collapsed;
      ui.collapse.textContent = collapsed ? '+' : '−';
      ui.collapse.title = collapsed ? 'Expand measurement panel' : 'Collapse measurement panel';
      ui.collapse.setAttribute('aria-label', ui.collapse.title);

      for (const [name, button] of Object.entries(ui.modeButtons)) {
        const active = name === (mode || 'navigate');
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
        button.disabled = busy;
      }
      ui.undo.disabled = busy || (!currentPicks.length && !hasUndoableMeasurement());
      ui.clear.disabled = busy || (!currentPicks.length && measurementCount() === 0);

      ui.progress.textContent = info ? `${currentPicks.length}/${info.points}` : 'NAV';
      ui.progress.classList.toggle('is-error', Boolean(errorMessage));
      if (errorMessage) ui.statusText.textContent = errorMessage;
      else if (!info) ui.statusText.textContent = 'Camera navigation mode. Choose a measurement to start.';
      else if (busy) ui.statusText.textContent = `Creating ${info.label.toLowerCase()} measurement…`;
      else if (currentPicks.length) ui.statusText.textContent = `${info.label}: click atom ${currentPicks.length + 1} of ${info.points}.`;
      else if (lastMessage) ui.statusText.textContent = `${lastMessage} Continue with the next atom set.`;
      else ui.statusText.textContent = `${info.label}: click atom 1 of ${info.points}.`;

      ui.picks.replaceChildren();
      currentPicks.forEach((pick, index) => {
        const row = document.createElement('li');
        row.className = 'sv-mol-measure__pick';
        const identity = document.createElement('span');
        identity.className = 'sv-mol-measure__identity';
        identity.textContent = pick.identity;
        identity.title = `${pick.identity} (unit ${pick.unitId})`;
        const coordinate = document.createElement('code');
        coordinate.className = 'sv-mol-measure__coordinate';
        coordinate.textContent = `X ${pick.coordinate[0].toFixed(3)}  Y ${pick.coordinate[1].toFixed(3)}  Z ${pick.coordinate[2].toFixed(3)} Å`;
        row.append(identity, coordinate);
        ui.picks.appendChild(row);
      });
    }

    async function clearTransientSelection() {
      await manager.addOrderLabels([]);
      interactivity.lociSelects.deselectAll();
      currentPicks = [];
    }

    async function setMode(nextMode) {
      if (destroyed || busy || nextMode === mode) return;
      busy = true;
      errorMessage = '';
      lastMessage = '';
      mode = nextMode;
      setPluginSelectionMode(false);
      render();
      try {
        await clearTransientSelection();
      } finally {
        busy = false;
        setPluginSelectionMode(Boolean(mode));
        render();
        scheduleHistorySync();
      }
    }

    async function rejectHistoryEntry(entry, message) {
      selection.modifyHistory(entry, 'remove');
      interactivity.lociSelects.deselect({ loci: entry.loci }, false);
      errorMessage = message;
      currentPicks = [];
      render();
    }

    async function createMeasurement(points) {
      const info = MODE_INFO[mode];
      if (!info || points.length !== info.points || busy) return;
      busy = true;
      errorMessage = '';
      setPluginSelectionMode(false);
      render();
      try {
        const result = await manager[info.method](...points.map(point => point.loci));
        const ref = result?.selection?.ref;
        if (!ref) throw new Error(`Mol* could not create the ${info.label.toLowerCase()} annotation.`);
        createdRefs.push(ref);
        const value = formatValue(mode, points);
        lastMessage = `${info.label} created${value ? `: ${value}` : ''}.`;
        await clearTransientSelection();
      } catch (error) {
        errorMessage = error?.message || String(error);
      } finally {
        busy = false;
        setPluginSelectionMode(Boolean(mode));
        render();
      }
    }

    async function syncFromHistory() {
      if (destroyed) return;
      if (!mode || busy) {
        if (!mode) currentPicks = [];
        render();
        return;
      }
      const info = MODE_INFO[mode];
      const history = selection.additionsHistory.slice(0, info.points);
      const parsed = [];
      for (const entry of history) {
        try {
          parsed.push(singleAtomFromLoci(entry.loci));
        } catch (error) {
          await rejectHistoryEntry(entry, error?.message || String(error));
          return;
        }
      }
      if (history.length) errorMessage = '';
      currentPicks = parsed.reverse();
      await manager.addOrderLabels(currentPicks.map(point => point.loci));
      render();
      if (currentPicks.length === info.points) await createMeasurement(currentPicks.slice());
    }

    function scheduleHistorySync() {
      if (destroyed) return;
      historyRevision += 1;
      if (historyWorker) return;
      historyWorker = (async () => {
        let observed = -1;
        while (observed !== historyRevision) {
          observed = historyRevision;
          await syncFromHistory();
        }
      })()
        .catch(error => {
          errorMessage = error?.message || String(error);
          console.error('[molstar-measurements]', error);
          render();
        })
        .finally(() => { historyWorker = null; });
    }

    async function undo() {
      if (destroyed || busy) return;
      errorMessage = '';
      const latestPick = selection.additionsHistory[0];
      if (latestPick) {
        selection.modifyHistory(latestPick, 'remove');
        interactivity.lociSelects.deselect({ loci: latestPick.loci }, false);
        lastMessage = 'Latest point removed.';
        scheduleHistorySync();
        return;
      }
      while (createdRefs.length) {
        const ref = createdRefs.pop();
        if (!plugin.state.data.cells.has(ref)) continue;
        busy = true;
        setPluginSelectionMode(false);
        render();
        try {
          await deleteStateRefs(plugin, [ref]);
          lastMessage = 'Latest measurement removed.';
        } catch (error) {
          errorMessage = error?.message || String(error);
        } finally {
          busy = false;
          setPluginSelectionMode(Boolean(mode));
          render();
        }
        return;
      }
    }

    async function clear() {
      if (destroyed || busy) return;
      busy = true;
      errorMessage = '';
      setPluginSelectionMode(false);
      render();
      try {
        await clearTransientSelection();
        const refs = measurementParentRefs(manager.state, MEASUREMENT_KINDS);
        const removed = await deleteStateRefs(plugin, refs);
        createdRefs.length = 0;
        lastMessage = removed ? `Cleared ${removed} measurement${removed === 1 ? '' : 's'}.` : 'Measurements are clear.';
      } catch (error) {
        errorMessage = error?.message || String(error);
      } finally {
        busy = false;
        setPluginSelectionMode(Boolean(mode));
        render();
      }
    }

    ui.modeButtons.navigate.addEventListener('click', () => { void setMode(null); });
    for (const name of Object.keys(MODE_INFO)) {
      ui.modeButtons[name].addEventListener('click', () => { void setMode(name); });
    }
    ui.undo.addEventListener('click', () => { void undo(); });
    ui.clear.addEventListener('click', () => { void clear(); });
    ui.collapse.addEventListener('click', () => {
      collapsed = !collapsed;
      render();
    });

    subscriptions.push(selection.events.additionsHistoryUpdated.subscribe(scheduleHistorySync));
    subscriptions.push(manager.behaviors.state.subscribe(render));
    const selectionModeBehavior = plugin.behaviors?.interaction?.selectionMode;
    if (selectionModeBehavior?.subscribe) {
      subscriptions.push(selectionModeBehavior.subscribe(enabled => {
        if (!changingSelectionMode && mode && !enabled) void setMode(null);
      }));
    }

    const api = Object.freeze({
      setMode,
      undo,
      clear,
      getState() {
        return {
          mode: mode || 'navigate',
          pending_points: currentPicks.map((point, index) => ({
            order: index + 1,
            atom: point.identity,
            unit_id: point.unitId,
            coordinate_A: point.coordinate.slice(),
          })),
          measurements: {
            distances: manager.state?.distances?.length || 0,
            angles: manager.state?.angles?.length || 0,
            dihedrals: manager.state?.dihedrals?.length || 0,
          },
          coordinate_reference: 'displayed structure Cartesian coordinates after unit operators',
          coordinate_unit: 'Å',
        };
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        setPluginSelectionMode(false);
        subscriptions.forEach(subscription => subscription?.unsubscribe?.());
        ui.panel.remove();
        if (activeInstance === api) activeInstance = null;
        if (window.__svMolstarMeasurements === api) delete window.__svMolstarMeasurements;
        void clearTransientSelection()
          .catch(error => console.error('[molstar-measurements] cleanup failed', error))
          .finally(() => {
            if (typeof previousGranularity === 'string') {
              interactivity.setProps({ granularity: previousGranularity });
            }
            if (previousAxes !== undefined && plugin.canvas3d?.setProps) {
              plugin.canvas3d.setProps({ camera: { helper: { axes: previousAxes } } });
            }
            setPluginSelectionMode(previousSelectionMode);
          });
      },
    });

    activeInstance = api;
    render();
    return api;
  }

  Object.defineProperty(window, 'ScienceVisionMolstarMeasurementMath', {
    configurable: false,
    enumerable: false,
    writable: false,
    value: Object.freeze({ distance3, angle3, dihedral4, measurementParentRefs, orderedSetInfo }),
  });
  Object.defineProperty(window, 'ScienceVisionInstallMolstarMeasurements', {
    configurable: false,
    enumerable: true,
    writable: false,
    value: install,
  });
}());
