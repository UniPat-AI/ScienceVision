// viewer/shared/bv2-home.js — homepage Domain coverage + launcher count rendering.
//
// Run after window.ManifestReady resolves. Populates:
//   #bv2-launcher-doc-count    (no count — doc count is shipped/visible state)
//   #bv2-launcher-assets-count
//   #bv2-launcher-qagen-count
//   #bv2-domain-mosaic         (11 tiles from Manifest.domains[])
//   #counts                    (top-right meta line)

(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function setCount(elId, n) {
    const el = $(elId);
    if (!el) return;
    el.textContent = (n && n > 0) ? n : '—';
  }

  function setShippedCount(elId, domains) {
    const el = $(elId);
    if (!el) return;
    const shipped = domains.filter((d) => d.shipped).length;
    const pending = domains.length - shipped;
    el.textContent = shipped + ' shipped · ' + pending + ' partner-in-progress';
  }

  function renderDomainMosaic(domains) {
    const root = $('bv2-domain-mosaic');
    if (!root) return;
    root.innerHTML = '';
    for (const d of domains) {
      const tile = document.createElement('a');
      tile.className = 'bv2-domain-tile' + (d.shipped ? '' : ' is-pending');
      tile.style.setProperty('--domain-accent', d.color);
      tile.href = d.shipped
        ? ('assets.html#' + encodeURIComponent(d.id))
        : 'javascript:void(0)';
      tile.setAttribute('aria-disabled', d.shipped ? 'false' : 'true');
      tile.innerHTML =
        '<div class="bv2-domain-tile-label">' + d.label + '</div>' +
        '<div class="bv2-domain-tile-state">' +
        (d.shipped ? 'shipped' : 'coming via partner') +
        '</div>';
      root.appendChild(tile);
    }
  }

  window.ManifestReady.then((m) => {
    // Top-right meta.
    const counts = $('counts');
    if (counts) counts.textContent = m.brand + ' v' + m.version;

    // Launcher tile counts.
    setCount('bv2-launcher-assets-count', m.counts.assets);
    setCount('bv2-launcher-qagen-count',  m.counts.qagen);

    // Doc launcher: show MCP tool count (more meaningful than "doc count").
    setCount('bv2-launcher-doc-count', m.counts.mcp_tools);

    // Domain shipped/pending summary in the launcher.
    setShippedCount('bv2-launcher-domain-state', m.domains);

    // Domain mosaic.
    renderDomainMosaic(m.domains);
  });
})();
