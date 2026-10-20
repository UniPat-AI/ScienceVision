/**
 * NcSlicesLoader — fetch the slices manifest + Float32 arrays.
 *
 * One instance per .nc.slices/ directory. Holds a small LRU cache of the
 * most recently fetched slices so flipping between (var, time, level)
 * combinations stays interactive without redownloading.
 *
 * No mutation: every method either returns the same manifest object or a
 * fresh Float32Array view onto a fetched ArrayBuffer.
 */
export class NcSlicesLoader {
  constructor(manifestUrl, { cacheSize = 4 } = {}) {
    this.manifestUrl = manifestUrl;
    this.baseDir = manifestUrl.substring(0, manifestUrl.lastIndexOf('/') + 1);
    this.manifest = null;
    this.cacheSize = cacheSize;
    /** @type {Map<string, Float32Array>} */
    this.cache = new Map();
  }

  async loadManifest() {
    if (this.manifest) return this.manifest;
    const res = await fetch(this.manifestUrl, { cache: 'force-cache' });
    if (!res.ok) {
      throw new Error(`manifest fetch failed: ${res.status} ${res.statusText}`);
    }
    this.manifest = await res.json();
    return this.manifest;
  }

  /**
   * Look up a variable entry by name. Returns null if not present.
   */
  varSpec(varName) {
    if (!this.manifest) throw new Error('NcSlicesLoader: call loadManifest() first');
    return this.manifest.vars.find((v) => v.name === varName) || null;
  }

  /**
   * Resolve the bin path + per-slice colormap for a (var, levelIndex) tuple.
   * Returns { binUrl, colormap, statsForLevel } or null if invalid.
   *
   * For 2D vars, levelIndex is ignored.
   * For 3D vars, levelIndex indexes into vars[i].levels[].
   */
  resolveSliceRef(varName, levelIndex) {
    const v = this.varSpec(varName);
    if (!v) return null;
    if (v.kind === '2d') {
      return {
        binUrl: this.baseDir + v.bin,
        colormap: v.colormap,
        stats: v.stats,
        kind: '2d',
        shape: v.shape, // [time, lat, lon]
      };
    }
    if (v.kind === '3d') {
      const lv = v.levels[levelIndex] || v.levels[0];
      if (!lv) return null;
      return {
        binUrl: this.baseDir + lv.bin,
        colormap: lv.colormap,
        stats: lv.stats,
        kind: '3d',
        levelInfo: { pfull_mb: lv.pfull_mb, pfull_index: lv.pfull_index },
        shape: v.shape_per_level, // [time, lat, lon]
      };
    }
    return null;
  }

  /**
   * Fetch the .bin for a (var, levelIndex) tuple as Float32Array. Cached.
   */
  async loadBin(varName, levelIndex = 0) {
    const ref = this.resolveSliceRef(varName, levelIndex);
    if (!ref) throw new Error(`unknown slice: ${varName}@${levelIndex}`);
    const cacheKey = ref.binUrl;
    if (this.cache.has(cacheKey)) {
      // touch LRU
      const arr = this.cache.get(cacheKey);
      this.cache.delete(cacheKey);
      this.cache.set(cacheKey, arr);
      return { ...ref, data: arr };
    }
    const res = await fetch(ref.binUrl, { cache: 'force-cache' });
    if (!res.ok) {
      throw new Error(`bin fetch failed: ${ref.binUrl} ${res.status}`);
    }
    const buf = await res.arrayBuffer();
    const data = new Float32Array(buf);
    this.cache.set(cacheKey, data);
    while (this.cache.size > this.cacheSize) {
      const firstKey = this.cache.keys().next().value;
      this.cache.delete(firstKey);
    }
    return { ...ref, data };
  }

  /**
   * Extract a single time-slice (lat × lon) Float32Array view from the
   * full [time, lat, lon] flattened array. Zero-copy view via subarray().
   */
  timeSliceView(data, shape, timeIndex) {
    const [T, H, W] = shape;
    if (timeIndex < 0 || timeIndex >= T) {
      throw new Error(`time index ${timeIndex} out of range [0, ${T})`);
    }
    const sliceLen = H * W;
    return data.subarray(timeIndex * sliceLen, (timeIndex + 1) * sliceLen);
  }
}
