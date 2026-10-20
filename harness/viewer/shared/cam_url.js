// viewer/shared/cam_url.js
// Tiny helper: read MCP-style camera/opacity/slice params from URL and apply.
//
//   ?cam=<base64url(JSON {position,target,up,fov_deg})>
//   ?opacity=base:0.7,mask:0.4
//   ?slice=axial:0.5:400:40        (axis:frac_or_index:window:level)
//
// V1 is one-way: URL -> viewer. We do NOT push browser-side orbits back to
// the URL (the user can call sv3d_get_state instead).

export function decodeCam(s) {
  if (!s) return null;
  try {
    const b64 = s.replaceAll('-', '+').replaceAll('_', '/');
    // Pad to multiple of 4 for atob.
    const pad = b64.length % 4 === 0 ? b64 : b64 + '='.repeat(4 - (b64.length % 4));
    const obj = JSON.parse(atob(pad));
    if (!Array.isArray(obj.position) || !Array.isArray(obj.target)) return null;
    if (obj.position.length !== 3 || obj.target.length !== 3) return null;
    return obj;
  } catch (e) {
    console.warn('cam_url: bad cam=', s, e);
    return null;
  }
}

export function decodeOpacity(s) {
  if (!s) return {};
  const out = {};
  for (const kv of s.split(',')) {
    const [k, v] = kv.split(':');
    const n = parseFloat(v);
    if (Number.isFinite(n) && n >= 0 && n <= 1) out[k] = n;
  }
  return out;
}

export function decodeSlice(s) {
  if (!s) return null;
  const parts = s.split(':');
  const axis = parts[0];
  if (!['axial', 'sagittal', 'coronal'].includes(axis)) return null;
  return {
    axis,
    fraction: parts[1] != null ? parseFloat(parts[1]) : undefined,
    window: parts[2] != null ? parseFloat(parts[2]) : undefined,
    level: parts[3] != null ? parseFloat(parts[3]) : undefined,
  };
}

export function applyCamFromUrl(camera, controls, params) {
  const cam = decodeCam(params.get('cam'));
  if (!cam) return false;
  camera.position.fromArray(cam.position);
  controls.target.fromArray(cam.target);
  if (Array.isArray(cam.up) && cam.up.length === 3) {
    camera.up.fromArray(cam.up);
  }
  if (typeof cam.fov_deg === 'number') {
    camera.fov = cam.fov_deg;
    camera.updateProjectionMatrix();
  }
  controls.update();
  return true;
}
