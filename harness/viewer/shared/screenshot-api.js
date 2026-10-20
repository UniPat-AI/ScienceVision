(function installScienceVisionScreenshotAPI() {
  'use strict';

  let provider = null;
  let viewDescriber = null;
  let loadError = null;
  let settleReadiness;
  const readiness = new Promise(resolve => { settleReadiness = resolve; });

  function dataURLToBlob(dataURL) {
    const match = /^data:([^;,]*)(;base64)?,([\s\S]*)$/.exec(dataURL);
    if (!match) throw new Error('Viewer returned an invalid screenshot data URL.');
    const mimeType = match[1] || 'application/octet-stream';
    let bytes;
    if (match[2]) {
      const binary = atob(match[3]);
      bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
    } else {
      bytes = new TextEncoder().encode(decodeURIComponent(match[3]));
    }
    return new Blob([bytes], { type: mimeType });
  }

  async function asPngBlob(value) {
    let blob = value;
    if (typeof value === 'string') blob = dataURLToBlob(value);
    if (!(blob instanceof Blob)) {
      throw new Error('Viewer screenshot did not return a Blob or data URL.');
    }
    if (!blob.size) throw new Error('Viewer screenshot is empty.');
    if (blob.type && blob.type !== 'image/png') {
      throw new Error(`Viewer screenshot has unsupported type ${blob.type}.`);
    }
    return blob.type === 'image/png' ? blob : new Blob([blob], { type: 'image/png' });
  }

  const api = Object.freeze({
    version: 1,
    get ready() {
      return typeof provider === 'function' && !loadError;
    },
    async whenReady() {
      const result = await readiness;
      if (result.error) throw result.error;
      return true;
    },
    // Optional: a viewer that can report what the camera is looking at does,
    // and every other viewer answers null, so a caller never has to know which
    // viewer is behind the frame. A broken describer degrades to null too --
    // losing the view state must never lose the screenshot.
    describeView() {
      if (typeof viewDescriber !== 'function') return null;
      let view;
      try {
        view = viewDescriber();
      } catch (error) {
        return null;
      }
      return view && typeof view === 'object' ? view : null;
    },
    async captureScreenshot() {
      if (loadError) throw loadError;
      if (typeof provider !== 'function') {
        throw new Error('Viewer is still loading; screenshot is not ready.');
      }
      try {
        return await asPngBlob(await provider());
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        throw new Error(`Unable to capture viewer screenshot: ${message}`);
      }
    },
  });

  const registration = Object.freeze({
    ready(nextProvider, options) {
      if (provider || loadError) return;
      if (typeof nextProvider !== 'function') {
        this.fail(new Error('Screenshot provider is invalid.'));
        return;
      }
      provider = nextProvider;
      const describe = options && options.describeView;
      if (typeof describe === 'function') viewDescriber = describe;
      settleReadiness({ ready: true });
    },
    fail(error) {
      if (provider || loadError) return;
      const message = error instanceof Error ? error.message : String(error);
      loadError = new Error(`Viewer is unavailable for screenshots: ${message}`);
      settleReadiness({ error: loadError });
    },
  });

  Object.defineProperty(window, 'ScienceVisionViewerScreenshot', {
    configurable: false,
    enumerable: true,
    value: api,
    writable: false,
  });
  Object.defineProperty(window, '__ScienceVisionScreenshotProvider', {
    configurable: false,
    enumerable: false,
    value: registration,
    writable: false,
  });
}());
