import { shouldEmitMove } from './pure.js';
import { openBuffer } from './idb.js';

// Records viewer interaction events (spec §4.1) into an IndexedDB buffer.
// Coordinates are canvas-relative only (spec §4.4 privacy).
export class CaptureSession {
  constructor(adapter, { sessionKey }) {
    this.adapter = adapter;
    this.buffer = openBuffer(sessionKey);
    this.t0 = null;
    this._lastMove = null;         // last EMITTED move
    this._active = 0;              // accumulated active ms
    this._lastActivity = null;
    this._handlers = [];
  }

  _now() { return performance.now() - this.t0; }

  _emit(ev) {
    if (this._stopped) return;
    ev.t = Math.round(this._now());
    this.buffer.append(ev).catch(err => console.warn('[capture] append failed', err));
    // active-time accumulation: any interaction within 5s of the previous counts.
    const now = performance.now();
    if (this._lastActivity !== null && now - this._lastActivity < 5000) {
      this._active += now - this._lastActivity;
    }
    this._lastActivity = now;
  }

  _canvasXY(e, canvas) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top,
             inside: e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom };
  }

  start() {
    if (this.t0 !== null) return;
    this.buffer.clear(); // fire-and-forget; IDB serialises clear before any subsequent append
    this.t0 = performance.now();
    const canvas = this.adapter.getCanvas();
    const on = (target, type, fn) => { target.addEventListener(type, fn); this._handlers.push([target, type, fn]); };

    on(canvas, 'mousemove', e => {
      const p = this._canvasXY(e, canvas);
      if (!p.inside) return;
      const cur = { t: this._now(), x: p.x, y: p.y };
      if (!shouldEmitMove(this._lastMove, cur)) return;
      this._lastMove = cur;
      this._emit({ type: 'mouse_move', x: Math.round(p.x), y: Math.round(p.y),
                   button_state: e.buttons, modifiers: { shift: e.shiftKey, ctrl: e.ctrlKey, alt: e.altKey } });
    });
    on(canvas, 'mousedown', e => { const p = this._canvasXY(e, canvas); if (p.inside) this._emit({ type: 'mouse_down', x: Math.round(p.x), y: Math.round(p.y), button: e.button }); });
    on(canvas, 'mouseup', e => { const p = this._canvasXY(e, canvas); if (p.inside) this._emit({ type: 'mouse_up', x: Math.round(p.x), y: Math.round(p.y), button: e.button }); });
    on(canvas, 'wheel', e => { const p = this._canvasXY(e, canvas); if (p.inside) this._emit({ type: 'mouse_wheel', x: Math.round(p.x), y: Math.round(p.y), delta: e.deltaY }); }, { passive: true });

    // Camera changes come pre-throttled from the adapter (spec §4.1 100ms).
    this.adapter.onCameraChange(pose => this._emit({ type: 'camera_change', viewer: this.adapter.name, pose }));
  }

  async markCheckpoint(reasoningText) {
    const shot = await this.adapter.screenshot();     // dataURL PNG
    const cp = {
      t: Math.round(this._now()),
      viewer_state: this.adapter.getCameraSnapshot(),
      reasoning: reasoningText,
      screenshot: shot,                                // inlined; annotate-shell externalizes on package
    };
    this._emit({ type: 'reasoning_checkpoint', reasoning_text: reasoningText, view_mode: this.adapter.getViewMode() });
    if (!this._checkpoints) this._checkpoints = [];
    this._checkpoints.push(cp);
    return cp;
  }

  get checkpoints() { return this._checkpoints || []; }
  get activeMs() { return Math.round(this._active); }
  async getEvents() { return this.buffer.readAll(); }

  stop() {
    this._stopped = true;
    for (const [t, type, fn] of this._handlers) t.removeEventListener(type, fn);
    this._handlers = [];
  }
}
