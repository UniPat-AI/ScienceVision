// viewer/shared/docs/docs-links.js — rewrite internal links after each render.
//
// Rules:
//   [t](other.md)                  -> SPA navigation, relative to current doc's dir
//   [t](../platform/quickstart.md) -> SPA navigation, cross-directory
//   [t](#anchor)                   -> leave alone (in-page anchor)
//   [t](https://...)               -> target="_blank" rel="noopener"
//   [t](/spec/...)                 -> leave alone (absolute repo path)
//   [[wiki-link]] -> looks up by title in DocsApp._index; if hit, replace with
//                    SPA <a>; if miss, render as <span class="broken-link"> with
//                    tooltip.
//
// We hook into the docs:rendered event and walk the freshly-rendered DOM.

(function () {
  'use strict';

  function isMdSpaLink(href) {
    if (!href) return false;
    if (/^[a-z]+:\/\//i.test(href)) return false; // absolute http(s)
    if (href.startsWith('/')) return false;       // absolute repo path
    if (href.startsWith('#')) return false;       // in-page
    if (href.startsWith('mailto:')) return false;
    return href.endsWith('.md') || href.includes('.md#');
  }

  function resolveRelative(currentDocPath, mdHref) {
    // currentDocPath like "platform/quickstart"; mdHref like "evaluation-protocol.md"
    // or "../platform/quickstart.md#step-1".
    const [pathPart, anchor] = mdHref.split('#');
    const currentDir = currentDocPath.includes('/')
      ? currentDocPath.slice(0, currentDocPath.lastIndexOf('/'))
      : '';
    // Normalize via URL semantics against a synthetic base.
    const base = 'docs:///' + currentDir + (currentDir ? '/' : '');
    const resolved = new URL(pathPart, base).pathname.replace(/^\/+/, '');
    const docPath = resolved.replace(/\.md$/, '');
    return { docPath: docPath, anchor: anchor || null };
  }

  function rewriteAnchor(a, currentDocPath) {
    const raw = a.getAttribute('href') || '';
    // 1. External (http/https): open in new tab.
    if (/^https?:\/\//i.test(raw)) {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener');
      return;
    }
    // 2. In-page anchor: leave alone.
    if (raw.startsWith('#')) return;
    // 3. Absolute repo path: leave alone.
    if (raw.startsWith('/')) return;
    // 4. Cross-doc .md link: rewrite to SPA route.
    if (isMdSpaLink(raw)) {
      const target = resolveRelative(currentDocPath, raw);
      const spaHref = '?doc=' + encodeURIComponent(target.docPath)
        + (target.anchor ? '#' + target.anchor : '');
      a.setAttribute('href', spaHref);
      a.dataset.spa = 'doc';
      a.dataset.docPath = target.docPath;
      if (target.anchor) a.dataset.anchor = target.anchor;
    }
    // 5. Other relative links: leave alone (e.g. images, raw assets).
  }

  // [[wiki-link]] processing: after marked.parse the DOM contains literal
  // text "[[name]]" (marked doesn't treat double-brackets specially). Walk
  // text nodes, replace with anchor or span as appropriate.
  const WIKI_PATTERN = /\[\[([^\]]+)\]\]/g;

  function indexByTitle(indexJson) {
    const map = new Map();
    for (const section of (indexJson && indexJson.tree) || []) {
      for (const child of section.children || []) {
        if (!map.has(child.title)) map.set(child.title, child);
      }
    }
    return map;
  }

  function walkText(root, byTitle) {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) =>
        n.parentNode && /CODE|PRE|A|SCRIPT|STYLE/.test(n.parentNode.tagName)
          ? NodeFilter.FILTER_REJECT
          : NodeFilter.FILTER_ACCEPT,
    });
    const targets = [];
    let node;
    while ((node = tw.nextNode())) {
      if (WIKI_PATTERN.test(node.nodeValue)) {
        WIKI_PATTERN.lastIndex = 0;
        targets.push(node);
      }
    }
    for (const tn of targets) {
      const frag = document.createDocumentFragment();
      const text = tn.nodeValue;
      let lastIdx = 0;
      let m;
      WIKI_PATTERN.lastIndex = 0;
      while ((m = WIKI_PATTERN.exec(text)) !== null) {
        if (m.index > lastIdx) {
          frag.appendChild(document.createTextNode(text.slice(lastIdx, m.index)));
        }
        const title = m[1].trim();
        const hit = byTitle.get(title);
        if (hit) {
          const a = document.createElement('a');
          a.href = '?doc=' + encodeURIComponent(hit.path);
          a.textContent = title;
          a.dataset.spa = 'doc';
          a.dataset.docPath = hit.path;
          frag.appendChild(a);
        } else {
          const span = document.createElement('span');
          span.className = 'broken-link';
          span.textContent = title;
          span.title = 'No doc with title "' + title + '" in the index.';
          frag.appendChild(span);
        }
        lastIdx = m.index + m[0].length;
      }
      if (lastIdx < text.length) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      }
      tn.parentNode.replaceChild(frag, tn);
    }
  }

  // SPA navigation hijack for rewritten cross-doc links.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-spa="doc"]');
    if (!a) return;
    e.preventDefault();
    const docPath = a.dataset.docPath;
    const anchor  = a.dataset.anchor || null;
    if (docPath && window.DocsApp) {
      DocsApp.navigate(docPath, anchor);
    }
  });

  window.addEventListener('docs:rendered', (e) => {
    const { docPath, root } = e.detail;
    // 1. Rewrite explicit <a href> tags.
    root.querySelectorAll('a').forEach((a) => rewriteAnchor(a, docPath));
    // 2. Walk text nodes for [[wiki-link]] tokens.
    const byTitle = indexByTitle(window.DocsApp && DocsApp._index);
    if (byTitle.size > 0) walkText(root, byTitle);
  });

  window.DocsLinks = { rewriteAnchor: rewriteAnchor, _walkText: walkText };
})();
