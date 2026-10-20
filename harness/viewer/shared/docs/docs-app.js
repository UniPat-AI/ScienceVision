// viewer/shared/docs/docs-app.js — router + markdown render orchestrator.
//
// Public API on window.DocsApp:
//   DocsApp.navigate(docPath, anchor)   — pushState + render
//   DocsApp.render(docPath) -> Promise  — fetch .md, render to main column,
//                                          emit docs:rendered CustomEvent
//   DocsApp.parseUrlDoc() -> {doc, anchor, q}
//
// Default doc: 'platform/quickstart' when ?doc= is absent.

(function () {
  'use strict';

  const DEFAULT_DOC = 'platform/quickstart';

  function $(id) { return document.getElementById(id); }

  function parseUrlDoc() {
    const params = new URLSearchParams(window.location.search);
    const q   = params.get('q');
    const doc = params.get('doc') || DEFAULT_DOC;
    const anchor = window.location.hash ? window.location.hash.slice(1) : null;
    return { doc: doc, anchor: anchor, q: q };
  }

  function slug(text) {
    return text.toLowerCase()
      .replace(/[^\w\s-]/g, '')
      .trim()
      .replace(/\s+/g, '-');
  }

  function configureMarked() {
    // Walker after marked.parse: ensure every h2/h3 has a stable id.
    // We rely on marked's default renderer for headings (which doesn't add
    // ids), then assign one in the DOM afterward.
  }

  function applyHeadingIds(root) {
    const headings = root.querySelectorAll('h2, h3');
    const seen = new Set();
    const list = [];
    headings.forEach((h) => {
      let base = h.id || slug(h.textContent);
      if (!base) base = 'heading-' + (list.length + 1);
      let candidate = base;
      let n = 1;
      while (seen.has(candidate)) { n++; candidate = base + '-' + n; }
      seen.add(candidate);
      h.id = candidate;
      list.push({ level: h.tagName === 'H2' ? 2 : 3, id: candidate, text: h.textContent });
    });
    return list;
  }

  function showError(message) {
    const content = $('docs-content');
    content.innerHTML =
      '<div class="docs-404"><h2>Document not found</h2>' +
      '<p>' + escapeHtml(message) + '</p>' +
      '<p><a href="?doc=platform/quickstart">Back to Quickstart</a></p></div>';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function fetchDoc(docPath) {
    const base = window.Manifest.urls.docs.base_url;     // e.g. ".../docs/"
    const url  = new URL(docPath + '.md', base).href;
    const resp = await fetch(url, { cache: 'no-cache' });
    if (!resp.ok) {
      throw new Error('HTTP ' + resp.status + ' for ' + url);
    }
    return resp.text();
  }

  async function render(docPath) {
    const content = $('docs-content');
    content.innerHTML = '<p class="empty">Loading…</p>';
    let mdText;
    try {
      if (docPath.startsWith('mcp/tools/') && window.DocsMcp) {
        const name = docPath.slice('mcp/tools/'.length);
        mdText = await DocsMcp.compose(name);
      } else {
        mdText = await fetchDoc(docPath);
      }
    } catch (e) {
      showError(e.message);
      return;
    }
    // marked produces standard HTML. We inject as innerHTML and then
    // post-process via DOM walks.
    const html = marked.parse(mdText, {
      gfm: true,
      breaks: false,
    });
    content.innerHTML = html;
    const headings = applyHeadingIds(content);
    if (window.Prism && Prism.highlightAllUnder) {
      Prism.highlightAllUnder(content);
    }
    if (window.DocsTree && DocsTree.setActive) DocsTree.setActive(docPath);
    document.title = content.querySelector('h1')
      ? content.querySelector('h1').textContent + ' — Docs — ' + (window.Manifest.brand || 'BV2')
      : 'Docs — ' + (window.Manifest.brand || 'BV2');
    // Fire event so later tasks (links, toc, search) can hook in.
    window.dispatchEvent(new CustomEvent('docs:rendered', {
      detail: { docPath: docPath, headings: headings, root: content },
    }));
  }

  function navigate(docPath, anchor) {
    const url = '?doc=' + encodeURIComponent(docPath) + (anchor ? ('#' + anchor) : '');
    history.pushState({ doc: docPath, anchor: anchor || null }, '', url);
    render(docPath).then(() => {
      if (anchor) {
        const el = document.getElementById(anchor);
        if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
      } else {
        window.scrollTo(0, 0);
      }
    });
  }

  // popstate (browser back/forward) replays the route.
  window.addEventListener('popstate', () => {
    const route = parseUrlDoc();
    render(route.doc).then(() => {
      if (route.anchor) {
        const el = document.getElementById(route.anchor);
        if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
      }
    });
  });

  // Sidebar clicks → intercept and navigate via History API.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.docs-tree-link');
    if (!a) return;
    e.preventDefault();
    const docPath = a.dataset.docPath;
    if (docPath) navigate(docPath, null);
  });

  // Bootstrap.
  window.ManifestReady.then(async (m) => {
    // Update meta line + brand.
    const meta = $('docs-meta');
    if (meta) meta.textContent = m.brand + ' · v' + m.version;
    const brand = $('brand');
    if (brand) brand.textContent = m.brand;

    // Fetch tree index, mount sidebar.
    let indexJson;
    try {
      const resp = await fetch(m.urls.docs.tree_index, { cache: 'no-cache' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      indexJson = await resp.json();
    } catch (e) {
      const tree = $('docs-tree');
      if (tree) tree.innerHTML = '<p class="empty">Tree index unreachable: ' + escapeHtml(e.message) + '</p>';
      // Continue without sidebar — main render still works.
      indexJson = { tree: [] };
    }
    DocsApp._index = indexJson;
    DocsTree.mount($('docs-tree'), indexJson);

    // Initial render.
    const route = parseUrlDoc();
    await render(route.doc);
    if (route.anchor) {
      const el = document.getElementById(route.anchor);
      if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
    }
  });

  window.DocsApp = {
    navigate: navigate,
    render:   render,
    parseUrlDoc: parseUrlDoc,
  };
})();
