// viewer/shared/docs/docs-search.js — client-side search using lunr.
//
// At boot, fetches docs/_search.json and constructs a lunr index from
// index_inputs[]. The constructed index is held in memory only — we don't
// serialize back. Each `index_inputs` entry is {ref, title, body}.
//
// Public API on window.DocsSearch:
//   DocsSearch.query(text, limit=10) -> [{path, title, snippet, score}, ...]

(function () {
  'use strict';

  let lunrIndex = null;
  let docsMeta = {};
  let inputEl = null;
  let resultsEl = null;

  function ensureResultsContainer() {
    if (resultsEl && document.body.contains(resultsEl)) return resultsEl;
    resultsEl = document.createElement('div');
    resultsEl.className = 'docs-search-results';
    resultsEl.id = 'docs-search-results';
    resultsEl.hidden = true;
    document.body.appendChild(resultsEl);
    return resultsEl;
  }

  function buildIndex(payload) {
    const inputs = payload.index_inputs || [];
    docsMeta = payload.docs || {};
    lunrIndex = lunr(function () {
      this.ref('ref');
      this.field('title', { boost: 5 });
      this.field('body');
      for (const doc of inputs) this.add(doc);
    });
  }

  function highlightSnippet(snippet, terms) {
    if (!snippet) return '';
    let html = escapeHtml(snippet);
    for (const t of terms) {
      if (!t) continue;
      const re = new RegExp('(' + escapeRegExp(t) + ')', 'ig');
      html = html.replace(re, '<mark>$1</mark>');
    }
    return html;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function query(text, limit) {
    if (!lunrIndex || !text || text.trim().length < 2) return [];
    limit = limit || 10;
    try {
      const hits = lunrIndex.search(text);
      const terms = text.trim().split(/\s+/);
      return hits.slice(0, limit).map((h) => {
        const meta = docsMeta[h.ref] || {};
        return {
          path:    h.ref,
          title:   meta.title || h.ref,
          snippet: meta.snippet || '',
          score:   h.score,
          terms:   terms,
        };
      });
    } catch (e) {
      // lunr throws on malformed queries (e.g. bare punctuation). Return [].
      return [];
    }
  }

  function renderResults(text) {
    const container = ensureResultsContainer();
    container.innerHTML = '';
    if (!text || text.trim().length < 2) {
      container.hidden = true;
      return;
    }
    const hits = query(text, 10);
    if (hits.length === 0) {
      container.className = 'docs-search-results is-empty';
      container.innerHTML = '<em>No matches.</em>';
      container.hidden = false;
      return;
    }
    container.className = 'docs-search-results';
    for (const h of hits) {
      const a = document.createElement('a');
      a.href = '?doc=' + encodeURIComponent(h.path);
      a.className = 'docs-search-result';
      a.dataset.spa = 'doc';
      a.dataset.docPath = h.path;
      a.innerHTML =
        '<div><strong>' + escapeHtml(h.title) + '</strong></div>' +
        '<div class="path">' + escapeHtml(h.path) + '</div>' +
        '<div>' + highlightSnippet(h.snippet, h.terms) + '</div>';
      container.appendChild(a);
    }
    container.hidden = false;
  }

  // Keyboard: '/' or '?' focuses the search input; Escape blurs.
  document.addEventListener('keydown', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
      if (e.key === 'Escape' && e.target === inputEl) {
        e.target.blur();
        if (resultsEl) resultsEl.hidden = true;
      }
      return;
    }
    if (e.key === '/' || e.key === '?') {
      e.preventDefault();
      if (inputEl) inputEl.focus();
    }
  });

  // Click outside results hides them.
  document.addEventListener('click', (e) => {
    if (!resultsEl || resultsEl.hidden) return;
    if (resultsEl.contains(e.target)) return;
    if (e.target === inputEl) return;
    resultsEl.hidden = true;
  });

  // Result clicks → SPA navigation via docs-links.js (data-spa="doc").
  // No additional code needed here — docs-links.js handles the click.

  function bootstrap() {
    inputEl = document.getElementById('docs-search-input');
    if (!inputEl) return;
    inputEl.addEventListener('input', () => renderResults(inputEl.value));
    inputEl.addEventListener('focus', () => {
      if (inputEl.value) renderResults(inputEl.value);
    });
  }

  window.ManifestReady.then(async (m) => {
    bootstrap();
    try {
      const resp = await fetch(m.urls.docs.search_index, { cache: 'no-cache' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const payload = await resp.json();
      buildIndex(payload);
    } catch (e) {
      console.error('[docs-search] failed to load index:', e);
      if (inputEl) inputEl.placeholder = 'Search unavailable';
    }
  });

  window.DocsSearch = { query: query };
})();
