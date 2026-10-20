/**
 * NcPicker — variable / time / level UI for the plot pane.
 *
 * Stateless renderer: the picker owns its DOM, listens to user events,
 * and calls back with a fresh state object on every change. Callers
 * (field3d.html) own the canonical state and pass it back via setState()
 * if they want to drive the picker programmatically.
 */
export class NcPicker {
  /**
   * @param {HTMLElement} container
   * @param {object} manifest         from NcSlicesLoader.loadManifest()
   * @param {(state:object)=>void} onChange  fired with new state
   */
  constructor(container, manifest, onChange) {
    this.container = container;
    this.manifest = manifest;
    this.onChange = onChange;
    this.state = this._initialState();
    this._render();
  }

  _initialState() {
    const first = this.manifest.vars[0];
    return {
      varName: first.name,
      timeIndex: 0,
      levelIndex: 0,
    };
  }

  setState(patch) {
    this.state = { ...this.state, ...patch };
    this._render();
  }

  getState() {
    return this.state;
  }

  _emit() {
    this.onChange({ ...this.state });
  }

  _currentVar() {
    return this.manifest.vars.find((v) => v.name === this.state.varName);
  }

  _render() {
    const v = this._currentVar();
    const T = this.manifest.shape.time;
    const timeUnits = this.manifest.axes?.time?.units || '';
    const timeVals = this.manifest.axes?.time?.values || [];

    const varOptions = this.manifest.vars
      .map((vv) => {
        const cls = this.state.varName === vv.name ? ' selected' : '';
        const label = `${vv.name}${vv.units ? ` (${vv.units})` : ''}`;
        return `<option value="${vv.name}"${cls}>${label}</option>`;
      })
      .join('');

    let levelControl = '';
    if (v.kind === '3d' && Array.isArray(v.levels) && v.levels.length > 0) {
      const opts = v.levels
        .map((lv, idx) => {
          const cls = this.state.levelIndex === idx ? ' selected' : '';
          return `<option value="${idx}"${cls}>~${Math.round(lv.pfull_mb)} mb (L${lv.pfull_index})</option>`;
        })
        .join('');
      levelControl = `
        <label class="nc-picker__label">
          <span>level</span>
          <select class="nc-picker__level">${opts}</select>
        </label>`;
    }

    const tIdx = Math.min(this.state.timeIndex, T - 1);
    const tDay = timeVals[tIdx];
    const tLabel =
      typeof tDay === 'number'
        ? `t = ${tIdx} (day ${tDay.toFixed(1)})`
        : `t = ${tIdx}`;

    this.container.innerHTML = `
      <div class="nc-picker">
        <label class="nc-picker__label">
          <span>variable</span>
          <select class="nc-picker__var">${varOptions}</select>
        </label>
        ${levelControl}
        <label class="nc-picker__label nc-picker__label--time">
          <span>time</span>
          <input class="nc-picker__time" type="range" min="0" max="${T - 1}" step="1" value="${tIdx}" />
          <span class="nc-picker__time-label">${tLabel}</span>
        </label>
        <div class="nc-picker__var-meta">
          <span class="nc-picker__long">${escapeHtml(v.long_name || v.name)}</span>
          ${timeUnits ? `<span class="nc-picker__units">time units: ${escapeHtml(timeUnits)}</span>` : ''}
        </div>
      </div>
    `;

    this._wireEvents();
  }

  _wireEvents() {
    const varSel = this.container.querySelector('.nc-picker__var');
    const timeIn = this.container.querySelector('.nc-picker__time');
    const timeLabel = this.container.querySelector('.nc-picker__time-label');
    const levelSel = this.container.querySelector('.nc-picker__level');
    const timeVals = this.manifest.axes?.time?.values || [];

    varSel?.addEventListener('change', () => {
      const newVarName = varSel.value;
      // Reset levelIndex when switching variables (the new var might be 2D)
      this.state = { ...this.state, varName: newVarName, levelIndex: 0 };
      this._render();
      this._emit();
    });

    levelSel?.addEventListener('change', () => {
      this.state = { ...this.state, levelIndex: parseInt(levelSel.value, 10) };
      this._emit();
    });

    timeIn?.addEventListener('input', () => {
      const idx = parseInt(timeIn.value, 10);
      this.state = { ...this.state, timeIndex: idx };
      const tDay = timeVals[idx];
      if (timeLabel) {
        timeLabel.textContent =
          typeof tDay === 'number'
            ? `t = ${idx} (day ${tDay.toFixed(1)})`
            : `t = ${idx}`;
      }
      this._emit();
    });
  }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
