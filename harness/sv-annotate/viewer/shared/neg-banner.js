// Per-page Negative Sample warning banner.
//
// Any viewer page that loads /sv-annotate/viewer/shared/style.css can opt in by also
// including this script. It reads `?file=` from the URL, fetches the
// negative manifest, and — if the file matches one or more negative
// samples — injects a stacked banner above the viewer canvas showing
// error_type / severity / description / expected_agent_behavior for
// each match so the user or downstream agent knows the rendering is
// intentionally a negative case.
//
// Multiple matches per file (NegativeSamples.md §3): a single file can
// be cataloged under several error_types (e.g., a PDB entry with low
// resolution AND high clashscore AND Ramachandran outliers gets one
// INDEX sample per defect aspect). All matching banners are rendered
// in INDEX order; the CSS variable --neg-banner-h is the summed height.
//
// Design (NegativeSamples.md §1.1 three-way separation): the manifest is the single
// source of truth. Viewer pages don't accept negative metadata via URL
// params — that would let the index card and the URL drift apart. Direct
// links into a viewer (skipping index.html) still get the warning.
//
// Silent on clean assets: if the manifest is missing, empty, or the path
// doesn't match anything, no banner is rendered and behavior is unchanged.

(function () {
  const params = new URLSearchParams(window.location.search);
  const file = params.get('file');
  if (!file) return;

  fetch('/sv-annotate/viewer/negative_assets.json')
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data) return;
      const matches = findMatches(data, file);
      if (matches.length > 0) injectBanners(matches);
    })
    .catch(() => {
      // Manifest unreachable (e.g., served from filesystem). Stay silent —
      // a missing negative manifest must never break clean-asset viewers.
    });

  function findMatches(data, filePath) {
    const out = [];
    for (const d of ['biology', 'medical', 'industrial']) {
      const arr = data[d];
      if (!Array.isArray(arr)) continue;
      for (const it of arr) {
        if (it && it.path === filePath) out.push(it);
      }
    }
    return out;
  }

  function buildBanner(neg) {
    const sev = neg.error_severity || 'unknown';
    const banner = document.createElement('div');
    banner.className = 'neg-page-banner';
    banner.setAttribute('role', 'alert');
    banner.innerHTML = `
      <div class="neg-page-banner__head">
        <span class="neg-page-banner__tag">NEG · ${esc(neg.error_type)}</span>
        <span class="neg-page-banner__sev severity-${esc(sev)}">[${esc(sev)}]</span>
        <span class="neg-page-banner__name">${esc(neg.name || '')}</span>
        ${neg.source_evidence_url ? `<a class="neg-page-banner__evidence" href="${esc(neg.source_evidence_url)}" target="_blank" rel="noopener">evidence ↗</a>` : ''}
      </div>
      <div class="neg-page-banner__desc">${esc(neg.error_description || '')}</div>
      <div class="neg-page-banner__expect"><strong>Expected agent behavior:</strong> ${esc(neg.expected_agent_behavior || '—')}</div>
      ${neg.viewer_hint && neg.viewer_hint.default_view_warning ? `<div class="neg-page-banner__warn">⚠ Default view: ${esc(neg.viewer_hint.default_view_warning)}</div>` : ''}
    `;
    return banner;
  }

  function injectBanners(matches) {
    // Banners are direct body children stacked in flow order; that's the
    // simplest layout that respects existing .neg-page-banner styling and
    // requires no CSS change. Insert in reverse so INDEX order = visual
    // top-to-bottom order after the insertBefore calls.
    const banners = [];
    for (let i = matches.length - 1; i >= 0; i--) {
      const b = buildBanner(matches[i]);
      document.body.insertBefore(b, document.body.firstChild);
      banners.unshift(b);
    }
    document.body.classList.add('has-neg-banner');

    // Sum heights of all stacked banners into --neg-banner-h so the
    // fixed-canvas viewer overrides in style.css push down by the right
    // amount. ResizeObserver covers reflow on narrow viewports + font load.
    const update = () => {
      const total = banners.reduce((sum, b) => sum + b.offsetHeight, 0);
      document.documentElement.style.setProperty('--neg-banner-h', total + 'px');
    };
    update();
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(update);
      for (const b of banners) ro.observe(b);
    }
    window.addEventListener('resize', update);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[c]));
  }
})();
