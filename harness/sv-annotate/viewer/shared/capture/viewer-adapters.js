// Viewer-specific hooks behind one interface. Phase-0 implements molstar only.

function molstarAdapter() {
  const viewer = window.__sv_viewer;
  if (!viewer) throw new Error('molstar viewer not exposed on window.__sv_viewer');
  const plugin = viewer.plugin;
  const canvas = document.querySelector('#viewer canvas');
  let lastCamEmit = 0;

  return {
    name: 'molstar',
    getCanvas() { return canvas; },
    onCameraChange(cb) {
      // Sample camera on each draw, throttled to 100ms (spec §4.1).
      plugin.canvas3d?.didDraw.subscribe(() => {
        const now = performance.now();
        if (now - lastCamEmit < 100) return;
        lastCamEmit = now;
        cb(this.getCameraSnapshot());
      });
    },
    getCameraSnapshot() {
      const s = plugin.canvas3d?.camera?.getSnapshot?.() || {};
      return { pos: s.position, target: s.target, up: s.up, fov: s.fov };
    },
    getViewMode() { return 'render'; },   // molstar single 3D mode in Phase-0
    async screenshot() {
      const helper = plugin.helpers?.viewportScreenshot;
      if (helper) {
        const img = await helper.getImageDataUri();
        return img;
      }
      return canvas.toDataURL('image/png');   // fallback
    },
  };
}

function niivueAdapter() {
  const nv = window.__sv_viewer;
  if (!nv) throw new Error('niivue viewer not exposed on window.__sv_viewer');
  const canvas = document.getElementById('gl');
  const SLICE_NAME = { 0:'axial', 1:'coronal', 2:'sagittal', 3:'multiplanar', 4:'render' };
  let lastEmit = 0;

  return {
    name: 'niivue',
    getCanvas() { return canvas; },
    getCameraSnapshot() {
      const sc = nv.scene || {};
      return {
        sliceType: nv.opts ? nv.opts.sliceType : null,
        azimuth: sc.renderAzimuth, elevation: sc.renderElevation,
        crosshairPos: sc.crosshairPos, clipPlane: sc.clipPlane,
        pan2Dxyzmm: sc.pan2Dxyzmm,
      };
    },
    getViewMode() { return SLICE_NAME[nv.opts ? nv.opts.sliceType : 4] || 'render'; },
    onCameraChange(cb) {
      const emit = () => {
        const now = performance.now();
        if (now - lastEmit < 100) return;
        lastEmit = now; cb(this.getCameraSnapshot());
      };
      // NiiVue 0.45 fires these on interaction; wrap without dropping any existing handler.
      const prevLoc = nv.onLocationChange; nv.onLocationChange = (d) => { if (prevLoc) prevLoc(d); emit(); };
      const prevAz  = nv.onAzimuthElevationChange; nv.onAzimuthElevationChange = (a,e) => { if (prevAz) prevAz(a,e); emit(); };
      const prevZ   = nv.onZoom3DChange; nv.onZoom3DChange = (z) => { if (prevZ) prevZ(z); emit(); };
    },
    async screenshot() {
      // Force a fresh draw so the WebGL backbuffer is populated before capture.
      if (typeof nv.drawScene === 'function') nv.drawScene();
      return canvas.toDataURL('image/png');
    },
  };
}

function notImplemented(name) {
  return () => { throw new Error(`${name} adapter not implemented in phase-0`); };
}

export function makeAdapter(viewerName) {
  switch (viewerName) {
    case 'molstar': return molstarAdapter();
    case 'niivue': return niivueAdapter();
    case 'cornerstone': return notImplemented('cornerstone')();
    default: throw new Error(`unknown viewer: ${viewerName}`);
  }
}
