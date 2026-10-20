(function installScienceVisionTaskScreenshot() {
  'use strict';

  function boot() {
    // Some reference bundles already contain the original inline implementation.
    if (document.getElementById('sv-task-shot')) return;

    const frame = document.getElementById('sv-task-frame');
    const frameWrap = frame?.closest('.sv-task-frame-wrap');
    if (!frame || !frameWrap) return;

    const style = document.createElement('style');
    style.id = 'sv-task-screenshot-style';
    style.textContent = `
      .sv-task-frame-wrap.sv-task-screenshot-enabled {
        display: grid;
        grid-template-rows: 40px minmax(0, 1fr);
      }
      .sv-task-viewer-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 0 12px 0 14px;
        border-bottom: 1px solid var(--border, #30363d);
        background: var(--bg2, #161b22);
      }
      .sv-task-viewer-bar[hidden] { display: none; }
      .sv-task-viewer-title {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: var(--fg, #f0f3f6);
        font: 650 12px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
        text-transform: uppercase;
      }
      .sv-task-viewer-title::before {
        content: '';
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #3fb950;
        box-shadow: 0 0 0 3px rgba(63, 185, 80, 0.12);
      }
      .sv-task-viewer-actions {
        min-width: 0;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
      }
      .sv-task-shot-status {
        max-width: 260px;
        overflow: hidden;
        color: var(--fg-dim, #8b949e);
        font: 10px/1.25 ui-monospace, SFMono-Regular, Menlo, monospace;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .sv-task-shot,
      .sv-shot-button,
      .sv-shot-close {
        border: 1px solid var(--border, #30363d);
        border-radius: 4px;
        background: var(--bg, #0d1117);
        color: var(--fg, #f0f3f6);
        font: 11px ui-monospace, SFMono-Regular, Menlo, monospace;
        cursor: pointer;
      }
      .sv-task-shot {
        min-width: 92px;
        height: 28px;
        padding: 0 9px;
      }
      .sv-task-shot:hover:not(:disabled),
      .sv-shot-button:hover:not(:disabled),
      .sv-shot-close:hover:not(:disabled) { border-color: var(--accent, #58a6ff); }
      .sv-task-shot:focus-visible,
      .sv-shot-button:focus-visible,
      .sv-shot-close:focus-visible,
      .sv-shot-canvas:focus-visible {
        outline: 2px solid var(--accent, #58a6ff);
        outline-offset: 2px;
      }
      .sv-task-shot:disabled,
      .sv-shot-button:disabled,
      .sv-shot-close:disabled {
        cursor: not-allowed;
        color: var(--fg-dim, #8b949e);
        opacity: 0.65;
      }
      body.sv-shot-open { overflow: hidden; }
      .sv-shot-modal[hidden] { display: none; }
      .sv-shot-modal {
        position: fixed;
        inset: 0;
        z-index: 10000;
        display: grid;
        place-items: center;
        padding: 16px;
        background: rgba(1, 4, 9, 0.82);
      }
      .sv-shot-dialog {
        width: min(1120px, calc(100vw - 32px));
        height: min(820px, calc(100vh - 32px));
        min-height: 320px;
        display: grid;
        grid-template-rows: 48px minmax(0, 1fr) auto;
        overflow: hidden;
        border: 1px solid var(--border, #30363d);
        border-radius: 6px;
        background: var(--bg2, #161b22);
        box-shadow: 0 18px 60px rgba(0, 0, 0, 0.55);
      }
      .sv-shot-head,
      .sv-shot-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 0 14px;
      }
      .sv-shot-head { border-bottom: 1px solid var(--border, #30363d); }
      .sv-shot-head h2 {
        margin: 0;
        color: var(--fg, #f0f3f6);
        font-size: 15px;
        line-height: 1.2;
      }
      .sv-shot-close {
        width: 30px;
        height: 30px;
        padding: 0;
        font: 20px/1 sans-serif;
      }
      .sv-shot-preview {
        min-width: 0;
        min-height: 0;
        display: grid;
        place-items: center;
        overflow: auto;
        padding: 12px;
        background: #05070a;
      }
      .sv-shot-canvas {
        display: block;
        max-width: 100%;
        max-height: 100%;
        width: auto;
        height: auto;
        background: #000;
        cursor: crosshair;
        touch-action: none;
        user-select: none;
      }
      .sv-shot-foot {
        min-height: 58px;
        padding-top: 8px;
        padding-bottom: 8px;
        border-top: 1px solid var(--border, #30363d);
      }
      .sv-shot-info {
        min-width: 0;
        display: grid;
        gap: 3px;
      }
      .sv-shot-meta,
      .sv-shot-view,
      .sv-shot-action-status {
        overflow-wrap: anywhere;
        color: var(--fg-dim, #8b949e);
        font: 10px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
      }
      .sv-shot-action-status {
        min-height: 13px;
        color: #79c0ff;
      }
      .sv-shot-controls {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        flex-wrap: wrap;
        gap: 8px;
      }
      .sv-shot-button {
        min-height: 32px;
        padding: 0 10px;
      }
      .sv-shot-button[data-primary="true"] {
        border-color: #238636;
        background: #238636;
        color: #fff;
      }
      @media (max-width: 560px) {
        .sv-task-shot-status { display: none; }
        .sv-task-shot { min-width: 0; }
        .sv-shot-modal { padding: 8px; }
        .sv-shot-dialog {
          width: calc(100vw - 16px);
          height: calc(100vh - 16px);
        }
        .sv-shot-foot {
          align-items: stretch;
          flex-direction: column;
        }
        .sv-shot-controls { justify-content: stretch; }
        .sv-shot-button { flex: 1; }
      }
      @media (max-width: 420px) {
        .sv-task-viewer-title { font-size: 0; gap: 0; }
      }
    `;
    document.head.appendChild(style);

    const viewerBar = document.createElement('div');
    viewerBar.className = 'sv-task-viewer-bar';
    viewerBar.hidden = true;
    viewerBar.innerHTML = `
      <span class="sv-task-viewer-title" data-i18n="capture.viewerTitle">3D Viewer</span>
      <div class="sv-task-viewer-actions">
        <span class="sv-task-shot-status" id="sv-task-shot-status" role="status" aria-live="polite"></span>
        <button class="sv-task-shot" id="sv-task-shot" type="button"
          data-i18n="capture.shot" data-i18n-aria-label="capture.shotTitle"
          aria-label="Capture the complete 3D viewer"
          title="Capture the complete 3D viewer" disabled>Screenshot</button>
      </div>`;
    frameWrap.insertBefore(viewerBar, frame);

    const modal = document.createElement('div');
    modal.className = 'sv-shot-modal';
    modal.id = 'sv-shot-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'sv-shot-title');
    modal.hidden = true;
    modal.innerHTML = `
      <section class="sv-shot-dialog" id="sv-shot-dialog" tabindex="-1">
        <header class="sv-shot-head">
          <h2 id="sv-shot-title" data-i18n="capture.dialogTitle">Crop screenshot</h2>
          <button class="sv-shot-close" id="sv-shot-close" type="button"
            data-i18n-aria-label="capture.closeTitle" data-i18n-title="capture.closeTitle"
            aria-label="Close screenshot editor" title="Close screenshot editor">&times;</button>
        </header>
        <div class="sv-shot-preview">
          <canvas class="sv-shot-canvas" id="sv-shot-canvas" tabindex="0" role="img"
            aria-label="Screenshot crop preview; drag to select an area"></canvas>
        </div>
        <footer class="sv-shot-foot">
          <div class="sv-shot-info">
            <span class="sv-shot-meta" id="sv-shot-meta"></span>
            <span class="sv-shot-view" id="sv-shot-view"></span>
            <span class="sv-shot-action-status" id="sv-shot-action-status"
              role="status" aria-live="polite"></span>
          </div>
          <div class="sv-shot-controls">
            <button class="sv-shot-button" id="sv-shot-reset" type="button"
              data-i18n="capture.reset" data-i18n-title="capture.resetTitle"
              title="Reset crop to the complete image">Reset selection</button>
            <button class="sv-shot-button" id="sv-shot-copy" type="button"
              data-i18n="capture.copy"
              title="Copy the selected area as PNG">Copy selection</button>
            <button class="sv-shot-button" id="sv-shot-download" type="button"
              data-i18n="capture.download" data-i18n-title="capture.downloadTitle"
              data-primary="true" title="Download the selected area as PNG">Download PNG</button>
          </div>
        </footer>
      </section>`;
    document.body.appendChild(modal);

    const els = {
      frame,
      shot: document.getElementById('sv-task-shot'),
      shotStatus: document.getElementById('sv-task-shot-status'),
      modal,
      dialog: document.getElementById('sv-shot-dialog'),
      canvas: document.getElementById('sv-shot-canvas'),
      close: document.getElementById('sv-shot-close'),
      reset: document.getElementById('sv-shot-reset'),
      copy: document.getElementById('sv-shot-copy'),
      download: document.getElementById('sv-shot-download'),
      meta: document.getElementById('sv-shot-meta'),
      view: document.getElementById('sv-shot-view'),
      actionStatus: document.getElementById('sv-shot-action-status'),
    };
    const state = {
      generation: 0,
      modalGeneration: 0,
      api: null,
      sourceImage: null,
      releaseSourceImage: null,
      sourceWidth: 0,
      sourceHeight: 0,
      selection: null,
      drag: null,
      previousFocus: null,
      clipboardAvailable: false,
      modalBusy: false,
      barMode: 'loading',
      barStatus: null,
      actionStatus: null,
    };

    // The expert workspace loads the shared language layer before this script.
    // Standalone viewer pages and offline bundles load this script alone, so
    // every lookup keeps its English wording as the fallback.
    const layer = () => window.SVI18n || null;
    const t = (key, fallback, params) => layer()?.t(key, fallback, params) ?? fallback;

    // A status line is either translated copy or raw text from a thrown error.
    function statusText(status) {
      if (!status) return '';
      if (status.text !== undefined) return status.text;
      return t(status.key, status.fallback, status.params);
    }

    function errorMessage(error) {
      if (error instanceof Error) return error.message;
      return String(error || t('capture.error.unknown', 'Unknown screenshot error.'));
    }

    function setBar(mode, status = null) {
      state.barMode = mode;
      state.barStatus = status;
      renderBar();
    }

    function renderBar() {
      const capturing = state.barMode === 'capturing';
      const key = capturing ? 'capture.shotCapturing' : 'capture.shot';
      els.shot.setAttribute('data-i18n', key);
      els.shot.textContent = capturing
        ? t('capture.shotCapturing', 'Capturing...')
        : t('capture.shot', 'Screenshot');
      els.shot.disabled = state.barMode !== 'ready';
      const message = statusText(state.barStatus);
      els.shotStatus.textContent = message;
      els.shot.title = state.barMode === 'ready'
        ? t('capture.shotTitle', 'Capture the complete 3D viewer')
        : message || t('capture.shotUnavailable', 'Screenshot is unavailable');
    }

    function setActionStatus(status) {
      state.actionStatus = status || null;
      els.actionStatus.textContent = statusText(state.actionStatus);
    }

    function showBar() {
      viewerBar.hidden = false;
      frameWrap.classList.add('sv-task-screenshot-enabled');
    }

    function hideBar() {
      viewerBar.hidden = true;
      frameWrap.classList.remove('sv-task-screenshot-enabled');
    }

    function releaseSourceImage() {
      if (state.releaseSourceImage) state.releaseSourceImage();
      state.sourceImage = null;
      state.releaseSourceImage = null;
      state.sourceWidth = 0;
      state.sourceHeight = 0;
      state.selection = null;
      els.canvas.width = 1;
      els.canvas.height = 1;
    }

    function closeModal(restoreFocus = true) {
      if (els.modal.hidden) return;
      state.modalGeneration += 1;
      state.drag = null;
      state.modalBusy = false;
      els.modal.hidden = true;
      document.body.classList.remove('sv-shot-open');
      for (const node of [document.querySelector('body > .topbar'), document.querySelector('main')]) {
        if (node) node.inert = false;
      }
      releaseSourceImage();
      const previousFocus = state.previousFocus;
      state.previousFocus = null;
      if (restoreFocus && previousFocus?.isConnected && !previousFocus.disabled) previousFocus.focus();
    }

    function resetForNavigation(hasViewer) {
      state.generation += 1;
      state.api = null;
      closeModal(false);
      hideBar();
      setBar('loading', hasViewer
        ? { key: 'capture.status.viewerLoading', fallback: 'Viewer loading...' }
        : { key: 'capture.status.noViewer', fallback: 'No viewer selected' });
    }

    function currentAPI() {
      try {
        const api = els.frame.contentWindow?.ScienceVisionViewerScreenshot;
        if (api?.version === 1 && typeof api.captureScreenshot === 'function'
            && typeof api.whenReady === 'function') return api;
      } catch {
        return null;
      }
      return null;
    }

    async function handleFrameLoad() {
      const hasViewer = Boolean(els.frame.getAttribute('src'));
      resetForNavigation(hasViewer);
      if (!hasViewer) {
        return;
      }
      const generation = state.generation;
      const api = currentAPI();
      if (!api) {
        return;
      }
      showBar();
      setBar('loading', { key: 'capture.status.viewerLoading', fallback: 'Viewer loading...' });
      try {
        await api.whenReady();
        if (generation !== state.generation || currentAPI() !== api) return;
        state.api = api;
        setBar('ready');
      } catch (error) {
        if (generation !== state.generation || currentAPI() !== api) return;
        state.api = null;
        setBar('unavailable', { text: errorMessage(error) });
      }
    }

    function decodeBlobWithImage(blob) {
      return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(blob);
        const image = new Image();
        image.onload = () => {
          URL.revokeObjectURL(url);
          resolve({
            image,
            width: image.naturalWidth,
            height: image.naturalHeight,
            release() { image.src = ''; },
          });
        };
        image.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error(t('capture.error.decode', 'The screenshot PNG could not be decoded.')));
        };
        image.src = url;
      });
    }

    function decodeBlob(blob) {
      if (typeof createImageBitmap !== 'function') return decodeBlobWithImage(blob);
      return createImageBitmap(blob).then(bitmap => ({
        image: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        release() { bitmap.close(); },
      })).catch(() => decodeBlobWithImage(blob));
    }

    function clipboardSupportsPng() {
      if (!window.isSecureContext || typeof ClipboardItem !== 'function'
          || typeof navigator.clipboard?.write !== 'function') return false;
      try {
        return typeof ClipboardItem.supports !== 'function' || ClipboardItem.supports('image/png');
      } catch {
        return false;
      }
    }

    // Older bundles ship a screenshot API without describeView, and a viewer
    // may not describe its camera at all, so absence is the normal answer.
    function currentView() {
      const api = state.api;
      if (!api || typeof api.describeView !== 'function') return null;
      return api.describeView();
    }

    function selectionBox() {
      const width = state.sourceWidth;
      const height = state.sourceHeight;
      const selection = state.selection || { x1: 0, y1: 0, x2: width, y2: height };
      const left = Math.max(0, Math.min(width, selection.x1, selection.x2));
      const top = Math.max(0, Math.min(height, selection.y1, selection.y2));
      const right = Math.max(0, Math.min(width, Math.max(selection.x1, selection.x2)));
      const bottom = Math.max(0, Math.min(height, Math.max(selection.y1, selection.y2)));
      const x = Math.min(Math.max(0, width - 1), Math.floor(left));
      const y = Math.min(Math.max(0, height - 1), Math.floor(top));
      const x2 = Math.min(width, Math.max(x + 1, Math.ceil(right)));
      const y2 = Math.min(height, Math.max(y + 1, Math.ceil(bottom)));
      return { x, y, width: x2 - x, height: y2 - y };
    }

    function updateSelectionMeta() {
      const box = selectionBox();
      const params = {
        width: state.sourceWidth,
        height: state.sourceHeight,
        selectionWidth: box.width,
        selectionHeight: box.height,
        x: box.x,
        y: box.y,
      };
      els.meta.textContent = t(
        'capture.meta',
        `Image ${params.width} x ${params.height} px | Selection ${params.selectionWidth} x ${params.selectionHeight} px at ${params.x}, ${params.y}`,
        params,
      );
      els.canvas.setAttribute('aria-label', t(
        'capture.canvasSelected',
        `Screenshot crop preview. Selected ${box.width} by ${box.height} pixels at ${box.x}, ${box.y}. Drag to select an area.`,
        params,
      ));
    }

    // Dialog text that depends on runtime state: the copy button explains why
    // it is disabled, and the measurement line only exists once an image does.
    function renderModalText() {
      els.copy.title = state.clipboardAvailable
        ? t('capture.copyTitle', 'Copy the selected area as PNG')
        : t('capture.copyUnsupported', 'PNG clipboard copy is not supported in this browser context');
      els.view.textContent = currentView()
        ? t('capture.view.recorded', 'View state will be saved next to the PNG')
        : t('capture.view.unavailable', 'This viewer does not report a view state');
      if (!state.sourceImage) {
        els.meta.textContent = '';
        els.canvas.setAttribute('aria-label',
          t('capture.canvasLabel', 'Screenshot crop preview; drag to select an area'));
        return;
      }
      updateSelectionMeta();
    }

    function drawPreview() {
      const context = els.canvas.getContext('2d');
      if (!context || !state.sourceImage) return;
      context.clearRect(0, 0, els.canvas.width, els.canvas.height);
      context.drawImage(state.sourceImage, 0, 0, els.canvas.width, els.canvas.height);
      const box = selectionBox();
      const scaleX = els.canvas.width / state.sourceWidth;
      const scaleY = els.canvas.height / state.sourceHeight;
      const x = box.x * scaleX;
      const y = box.y * scaleY;
      const width = box.width * scaleX;
      const height = box.height * scaleY;
      context.save();
      context.beginPath();
      context.rect(0, 0, els.canvas.width, els.canvas.height);
      context.rect(x, y, width, height);
      context.fillStyle = 'rgba(0, 0, 0, 0.58)';
      context.fill('evenodd');
      context.strokeStyle = '#58a6ff';
      context.lineWidth = 2;
      context.setLineDash([7, 4]);
      context.strokeRect(x + 1, y + 1, Math.max(0, width - 2), Math.max(0, height - 2));
      context.restore();
      updateSelectionMeta();
    }

    function resetSelection() {
      state.selection = { x1: 0, y1: 0, x2: state.sourceWidth, y2: state.sourceHeight };
      setActionStatus(null);
      drawPreview();
    }

    function setModalBusy(busy) {
      state.modalBusy = busy;
      els.reset.disabled = busy;
      els.download.disabled = busy;
      els.copy.disabled = busy || !state.clipboardAvailable;
    }

    async function openModal(blob, generation, returnFocus) {
      const decoded = await decodeBlob(blob);
      if (generation !== state.generation) {
        decoded.release();
        return false;
      }
      if (!decoded.width || !decoded.height) {
        decoded.release();
        throw new Error(t('capture.error.dimensions', 'The screenshot has invalid dimensions.'));
      }
      releaseSourceImage();
      state.sourceImage = decoded.image;
      state.releaseSourceImage = decoded.release;
      state.sourceWidth = decoded.width;
      state.sourceHeight = decoded.height;
      state.previousFocus = returnFocus?.isConnected ? returnFocus : document.activeElement;
      state.clipboardAvailable = clipboardSupportsPng();
      state.modalGeneration += 1;
      const maxPreviewWidth = Math.min(1400, decoded.width);
      const maxPreviewHeight = Math.min(Math.max(260, window.innerHeight - 190), decoded.height);
      const previewScale = Math.min(1, maxPreviewWidth / decoded.width, maxPreviewHeight / decoded.height);
      els.canvas.width = Math.max(1, Math.round(decoded.width * previewScale));
      els.canvas.height = Math.max(1, Math.round(decoded.height * previewScale));
      state.selection = { x1: 0, y1: 0, x2: decoded.width, y2: decoded.height };
      setActionStatus(state.clipboardAvailable ? null : {
        key: 'capture.status.clipboardUnavailable',
        fallback: 'Clipboard PNG copy is unavailable; download remains available.',
      });
      renderModalText();
      setModalBusy(false);
      drawPreview();
      els.modal.hidden = false;
      document.body.classList.add('sv-shot-open');
      for (const node of [document.querySelector('body > .topbar'), document.querySelector('main')]) {
        if (node) node.inert = true;
      }
      els.reset.focus();
      return true;
    }

    function sourcePointFromPointer(event) {
      const rect = els.canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return {
        x: Math.max(0, Math.min(state.sourceWidth,
          (event.clientX - rect.left) * state.sourceWidth / rect.width)),
        y: Math.max(0, Math.min(state.sourceHeight,
          (event.clientY - rect.top) * state.sourceHeight / rect.height)),
      };
    }

    function finishDrag(event, cancelled) {
      const drag = state.drag;
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (els.canvas.hasPointerCapture(event.pointerId)) els.canvas.releasePointerCapture(event.pointerId);
      const selection = state.selection;
      if (cancelled || Math.abs(selection.x2 - selection.x1) < 1
          || Math.abs(selection.y2 - selection.y1) < 1) {
        state.selection = drag.previousSelection;
        setActionStatus(cancelled ? null : {
          key: 'capture.status.selectionTooSmall',
          fallback: 'Selection was too small; the previous selection was kept.',
        });
      } else {
        const box = selectionBox();
        state.selection = { x1: box.x, y1: box.y, x2: box.x + box.width, y2: box.y + box.height };
        setActionStatus(null);
      }
      state.drag = null;
      setModalBusy(false);
      drawPreview();
    }

    function cropSelectionBlob() {
      if (!state.sourceImage) {
        return Promise.reject(new Error(t('capture.error.gone', 'Screenshot is no longer available.')));
      }
      const box = selectionBox();
      const output = document.createElement('canvas');
      output.width = box.width;
      output.height = box.height;
      const context = output.getContext('2d');
      if (!context) {
        return Promise.reject(
          new Error(t('capture.error.canvasUnavailable', 'This browser cannot create a crop canvas.')));
      }
      context.drawImage(
        state.sourceImage,
        box.x, box.y, box.width, box.height,
        0, 0, box.width, box.height,
      );
      return new Promise((resolve, reject) => {
        output.toBlob(result => {
          output.width = 1;
          output.height = 1;
          if (result) resolve(result);
          else reject(new Error(t('capture.error.encode', 'The selected area could not be encoded as PNG.')));
        }, 'image/png');
      });
    }

    function screenshotFilename() {
      const params = new URLSearchParams(location.search);
      const raw = params.get('task') || els.frame.title.replace(/ asset viewer$/i, '')
        || 'sciencevision-viewer';
      const base = raw.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96)
        || 'sciencevision-viewer';
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      return `${base}-crop-${timestamp}.png`;
    }

    async function captureCurrentViewer(event) {
      const api = state.api;
      if (!api?.ready) {
        setBar('loading', { key: 'capture.status.notReady', fallback: 'Viewer screenshot is not ready' });
        return;
      }
      const returnFocus = event?.currentTarget || document.activeElement;
      const generation = state.generation;
      setBar('capturing', { key: 'capture.status.capturingFull', fallback: 'Capturing full viewer...' });
      try {
        const blob = await api.captureScreenshot();
        if (generation !== state.generation || currentAPI() !== api) return;
        const opened = await openModal(blob, generation, returnFocus);
        if (opened && generation === state.generation) {
          setBar('ready', { key: 'capture.status.captured', fallback: 'Screenshot captured' });
        }
      } catch (error) {
        if (generation === state.generation) setBar('ready', { text: errorMessage(error) });
      }
    }

    function handleDialogKeydown(event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(els.dialog.querySelectorAll(
        'button:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
      )).filter(node => !node.hidden);
      if (!focusable.length) {
        event.preventDefault();
        els.dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    els.frame.addEventListener('load', handleFrameLoad);
    new MutationObserver(() => {
      resetForNavigation(Boolean(els.frame.getAttribute('src')));
    }).observe(els.frame, { attributes: true, attributeFilter: ['src'] });
    els.shot.addEventListener('click', captureCurrentViewer);
    els.close.addEventListener('click', () => closeModal());
    els.reset.addEventListener('click', resetSelection);
    els.modal.addEventListener('click', event => {
      if (event.target === els.modal) closeModal();
    });
    els.dialog.addEventListener('keydown', handleDialogKeydown);
    els.canvas.addEventListener('pointerdown', event => {
      if (state.modalBusy || !state.sourceImage
          || (event.pointerType === 'mouse' && event.button !== 0)) return;
      const point = sourcePointFromPointer(event);
      if (!point) return;
      event.preventDefault();
      els.canvas.setPointerCapture(event.pointerId);
      state.drag = { pointerId: event.pointerId, previousSelection: { ...state.selection } };
      state.selection = { x1: point.x, y1: point.y, x2: point.x, y2: point.y };
      setModalBusy(true);
      drawPreview();
    });
    els.canvas.addEventListener('pointermove', event => {
      if (!state.drag || event.pointerId !== state.drag.pointerId) return;
      const point = sourcePointFromPointer(event);
      if (!point) return;
      event.preventDefault();
      state.selection.x2 = point.x;
      state.selection.y2 = point.y;
      drawPreview();
    });
    els.canvas.addEventListener('pointerup', event => finishDrag(event, false));
    els.canvas.addEventListener('pointercancel', event => finishDrag(event, true));

    els.copy.addEventListener('click', async () => {
      if (!state.clipboardAvailable || state.modalBusy) return;
      const modalGeneration = state.modalGeneration;
      setModalBusy(true);
      setActionStatus({ key: 'capture.status.copying', fallback: 'Copying selection...' });
      try {
        const blob = await cropSelectionBlob();
        if (modalGeneration !== state.modalGeneration) return;
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        if (modalGeneration === state.modalGeneration) {
          setActionStatus({ key: 'capture.status.copied', fallback: 'Selection copied to clipboard.' });
        }
      } catch (error) {
        if (modalGeneration === state.modalGeneration) {
          const message = errorMessage(error);
          setActionStatus({
            key: 'capture.error.copyFailed',
            fallback: `Copy failed: ${message}`,
            params: { message },
          });
        }
      } finally {
        if (modalGeneration === state.modalGeneration) setModalBusy(false);
      }
    });

    function saveBlob(blob, filename) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      requestAnimationFrame(() => URL.revokeObjectURL(url));
    }

    // A crop is only reproducible together with the view it was taken from,
    // so the same click writes a sidecar next to the PNG. The view is null
    // when the viewer cannot describe itself; the sidecar still records the
    // task, the source image and the crop.
    function viewSidecarBlob(imageFilename) {
      const box = selectionBox();
      const payload = {
        schema_version: 'sciencevision.screenshot_view.v1',
        captured_at: new Date().toISOString(),
        task: new URLSearchParams(location.search).get('task') || null,
        viewer_url: els.frame.getAttribute('src') || null,
        image: {
          file: imageFilename,
          width: state.sourceWidth,
          height: state.sourceHeight,
        },
        selection: { x: box.x, y: box.y, width: box.width, height: box.height },
        view: currentView(),
      };
      return new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    }

    els.download.addEventListener('click', async () => {
      if (state.modalBusy) return;
      const modalGeneration = state.modalGeneration;
      setModalBusy(true);
      setActionStatus({ key: 'capture.status.preparing', fallback: 'Preparing PNG...' });
      try {
        const blob = await cropSelectionBlob();
        if (modalGeneration !== state.modalGeneration) return;
        const imageFilename = screenshotFilename();
        saveBlob(blob, imageFilename);
        saveBlob(viewSidecarBlob(imageFilename), imageFilename.replace(/\.png$/, '.json'));
        setActionStatus({
          key: 'capture.status.downloadStarted',
          fallback: 'PNG and view state download started.',
        });
      } catch (error) {
        if (modalGeneration === state.modalGeneration) {
          const message = errorMessage(error);
          setActionStatus({
            key: 'capture.error.downloadFailed',
            fallback: `Download failed: ${message}`,
            params: { message },
          });
        }
      } finally {
        if (modalGeneration === state.modalGeneration) setModalBusy(false);
      }
    });

    // The layer boots on DOMContentLoaded, before this markup exists, so the
    // injected subtrees are translated here and again on every switch.
    layer()?.apply(viewerBar);
    layer()?.apply(modal);
    layer()?.onChange(() => {
      const active = layer();
      if (!active) return;
      active.apply(viewerBar);
      active.apply(modal);
      renderBar();
      setActionStatus(state.actionStatus);
      renderModalText();
    });

    const hasViewer = Boolean(els.frame.getAttribute('src'));
    resetForNavigation(hasViewer);
    if (hasViewer) {
      try {
        if (els.frame.contentDocument?.readyState === 'complete') queueMicrotask(handleFrameLoad);
      } catch {
        // The load event reports unsupported cross-origin viewers.
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
}());
