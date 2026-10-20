(function scienceVisionQcActionLabel(global) {
  'use strict';

  const MAX_DETAILS = 3;
  const SKIP = new Set([
    'commit', 'camera', 'atoms', 'points_ras_mm', 'structure_id',
  ]);
  const DEGREE = new Set(['azimuth_deg', 'elevation_deg', 'yaw_deg', 'pitch_deg']);
  const FACTOR = new Set(['zoom', 'scale', 'factor']);
  const ENUM_FIELDS = new Set(['preset', 'view', 'mode', 'kind', 'plane', 'scope']);

  function translateViewer(kind, text) {
    const layer = global.SVI18n;
    if (layer && typeof layer.translateViewer === 'function') {
      return layer.translateViewer(kind, text);
    }
    return text;
  }

  function t(key, fallback) {
    const layer = global.SVI18n;
    return layer ? layer.t(key, fallback) : fallback;
  }

  function trimNumber(value) {
    const rounded = Math.round(value * 100) / 100;
    return Number.isInteger(rounded) ? String(rounded) : String(rounded);
  }

  function compactArg(name) {
    const gloss = translateViewer('arg', name);
    if (gloss && gloss !== name) {
      return gloss.replace(/（度）/g, '').replace(/\s*\(deg\)/gi, '').trim();
    }
    return name.replace(/_deg$/, '').replace(/_/g, ' ');
  }

  function formatDetail(name, value) {
    if (SKIP.has(name) || value == null) return null;
    if (Array.isArray(value) || (typeof value === 'object')) return null;
    if (typeof value === 'boolean') {
      if (name === 'visible') {
        return value
          ? t('manager.qc.flag.show', 'Shown')
          : t('manager.qc.flag.hide', 'Hidden');
      }
      if (name === 'enabled') {
        return value
          ? t('manager.qc.flag.on', 'On')
          : t('manager.qc.flag.off', 'Off');
      }
      if (name === 'all') return value ? translateViewer('enum', 'all') : null;
      return null;
    }
    if (typeof value === 'string') {
      if (name === 'value') return value;
      return translateViewer('enum', value);
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      if (DEGREE.has(name)) return `${compactArg(name)} ${Math.round(value)}°`;
      if (FACTOR.has(name)) return `×${trimNumber(value)}`;
      if (name === 'opacity') return `${Math.round(value * 100)}%`;
      if (name === 'index') return `#${value}`;
      if (name === 'sagittal' || name === 'coronal' || name === 'axial') {
        return `${translateViewer('enum', name)} ${value}`;
      }
      return `${compactArg(name)} ${trimNumber(value)}`;
    }
    return null;
  }

  function formatQcAction(toolName, input, result) {
    const name = String(toolName || '');
    const tool = name ? translateViewer('tool', name) : '';
    const merged = Object.assign(
      {},
      input && typeof input === 'object' ? input : {},
    );
    if (result && typeof result === 'object' && typeof result.value === 'string' && result.value) {
      merged.value = result.value;
    }
    const details = [];
    const order = [
      ...ENUM_FIELDS,
      'visible', 'enabled', 'all',
      ...DEGREE, ...FACTOR, 'opacity', 'window', 'level',
      'sagittal', 'coronal', 'axial', 'index', 'value',
    ];
    const seen = new Set();
    const push = (key, value) => {
      const part = formatDetail(key, value);
      if (part) details.push(part);
    };
    for (const key of order) {
      if (!Object.prototype.hasOwnProperty.call(merged, key)) continue;
      seen.add(key);
      push(key, merged[key]);
      if (details.length >= MAX_DETAILS) break;
    }
    if (details.length < MAX_DETAILS) {
      Object.keys(merged).forEach((key) => {
        if (details.length >= MAX_DETAILS || seen.has(key) || SKIP.has(key)) return;
        push(key, merged[key]);
      });
    }
    if (!tool) return details.join(' · ');
    if (!details.length) return tool;
    return [tool, ...details].join(' · ');
  }

  global.SVFormatQcAction = formatQcAction;
}(window));
