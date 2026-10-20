// viewer/shared/manifest.js — single source of truth loader.
// Fetches viewer/manifest.json at page load, does minimal structural checks,
// Object.freezes the result, and exposes it as window.Manifest.
//
// On failure, throws synchronously AND injects a red banner so the user sees
// the problem even if they don't open devtools.

(function () {
  'use strict';

  const REQUIRED_KEYS = ['brand', 'version', 'generated_at', 'counts', 'data', 'docs', 'domains'];
  const REQUIRED_DATA_KEYS = ['assets_index', 'qagen_index', 'leaderboard'];
  const REQUIRED_DOCS_KEYS = ['tree_index', 'search_index', 'base_url'];
  const REQUIRED_COUNT_KEYS = ['assets', 'qagen', 'mcp_tools', 'leaderboard_entries'];

  function injectBanner(message) {
    if (typeof document === 'undefined') return;
    const banner = document.createElement('div');
    banner.id = 'manifest-error';
    banner.setAttribute('role', 'alert');
    banner.style.cssText =
      'background:#f85149;color:white;padding:8px 16px;' +
      'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;' +
      'position:relative;z-index:9999';
    banner.textContent = 'Manifest load failed: ' + message + '. See console.';
    if (document.body) {
      document.body.insertBefore(banner, document.body.firstChild);
    } else {
      document.addEventListener('DOMContentLoaded', () => {
        document.body.insertBefore(banner, document.body.firstChild);
      });
    }
  }

  function fail(message) {
    console.error('[manifest]', message);
    injectBanner(message);
    var err = new Error('Manifest: ' + message);
    err.__alreadyReported = true;
    throw err;
  }

  function checkKeys(obj, requiredKeys, label) {
    for (const k of requiredKeys) {
      if (!(k in obj)) {
        fail("missing required key '" + label + '.' + k + "'");
      }
    }
  }

  function resolveManifestUrl() {
    // Defaults to 'manifest.json' relative to the current page.
    // Pages at non-root depth may override with <meta name="manifest-url" content="...">.
    if (typeof document === 'undefined') return 'manifest.json';
    const meta = document.querySelector('meta[name="manifest-url"]');
    if (meta && meta.content) return meta.content;
    return 'manifest.json';
  }

  function loadManifest() {
    const url = resolveManifestUrl();
    return fetch(url, { cache: 'no-cache' })
      .then((resp) => {
        if (!resp.ok) {
          fail('HTTP ' + resp.status + ' fetching ' + url);
        }
        return resp.json();
      })
      .catch((err) => {
        if (err && err.__alreadyReported) {
          throw err;          // already reported — re-throw to keep promise rejected
        }
        fail('fetch failed (' + url + '): ' + err.message);
      })
      .then((obj) => {
        if (!obj || typeof obj !== 'object') {
          fail('manifest is not a JSON object');
        }
        checkKeys(obj, REQUIRED_KEYS, 'manifest');
        checkKeys(obj.counts, REQUIRED_COUNT_KEYS, 'counts');
        checkKeys(obj.data, REQUIRED_DATA_KEYS, 'data');
        checkKeys(obj.docs, REQUIRED_DOCS_KEYS, 'docs');
        if (!Array.isArray(obj.domains)) {
          fail('domains must be an array');
        }
        // Pre-resolve all path values into absolute URLs against the manifest's own URL.
        // Consumers should fetch via Manifest.urls.* — those work no matter where the
        // calling page lives in the viewer/ tree.
        const manifestAbsUrl = new URL(url, document.baseURI).href;
        obj.urls = {
          data: {
            assets_index: new URL(obj.data.assets_index, manifestAbsUrl).href,
            qagen_index:  new URL(obj.data.qagen_index,  manifestAbsUrl).href,
            leaderboard:  new URL(obj.data.leaderboard,  manifestAbsUrl).href,
          },
          docs: {
            tree_index:   new URL(obj.docs.tree_index,   manifestAbsUrl).href,
            search_index: new URL(obj.docs.search_index, manifestAbsUrl).href,
            base_url:     new URL(obj.docs.base_url,     manifestAbsUrl).href,
          },
        };
        // Deep-freeze: brand-new object, freeze top + nested.
        Object.freeze(obj.counts);
        Object.freeze(obj.data);
        Object.freeze(obj.docs);
        obj.domains.forEach(Object.freeze);
        Object.freeze(obj.domains);
        Object.freeze(obj.urls.data);
        Object.freeze(obj.urls.docs);
        Object.freeze(obj.urls);
        Object.freeze(obj);
        window.Manifest = obj;
        // Dispatch a custom event for pages that want to wait for the manifest.
        window.dispatchEvent(new CustomEvent('manifest:ready', { detail: obj }));
        return obj;
      });
  }

  // Kick off the load and stash the promise so async consumers can await it.
  window.ManifestReady = loadManifest();
})();
