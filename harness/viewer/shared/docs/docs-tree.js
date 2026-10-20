// viewer/shared/docs/docs-tree.js — left sidebar tree component.
//
// Public API on window.DocsTree:
//   DocsTree.mount(rootEl, indexJson)   — render the tree
//   DocsTree.setActive(docPath)         — highlight the leaf matching docPath
//
// Persisted state: per-section open/closed in localStorage under key
// "docs-tree:open".

(function () {
  'use strict';

  const STORAGE_KEY = 'docs-tree:open';

  function readOpenState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? new Set(JSON.parse(raw)) : null;
    } catch (e) {
      return null;
    }
  }

  function writeOpenState(setOfTitles) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...setOfTitles]));
    } catch (e) { /* quota or privacy-mode — ignore */ }
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const k of Object.keys(attrs)) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
    if (children) for (const c of children) node.appendChild(c);
    return node;
  }

  function buildSection(section, openSet) {
    const details = el('details', { class: 'docs-tree-section' });
    if (openSet === null || openSet.has(section.title)) {
      details.open = true;
    }
    details.addEventListener('toggle', () => {
      const cur = readOpenState() || new Set();
      if (details.open) cur.add(section.title);
      else cur.delete(section.title);
      writeOpenState(cur);
    });

    const summary = el('summary', { class: 'docs-tree-section-summary' });
    summary.textContent = section.title;
    if (section.children && section.children.length) {
      const count = el('span', { class: 'docs-tree-count' });
      count.textContent = section.children.length;
      summary.appendChild(count);
    }
    details.appendChild(summary);

    const list = el('ul', { class: 'docs-tree-list' });
    for (const child of section.children || []) {
      const li = el('li', { class: 'docs-tree-leaf' });
      const a  = el('a',  { class: 'docs-tree-link', href: '?doc=' + encodeURIComponent(child.path) });
      a.dataset.docPath = child.path;
      a.textContent = child.title;
      if (child.kind === 'mcp-tool') {
        a.classList.add('is-mcp-tool');
      }
      li.appendChild(a);
      list.appendChild(li);
    }
    details.appendChild(list);
    return details;
  }

  function mount(rootEl, indexJson) {
    rootEl.innerHTML = '';
    const openSet = readOpenState();
    for (const section of indexJson.tree || []) {
      rootEl.appendChild(buildSection(section, openSet));
    }
  }

  function setActive(docPath) {
    const root = document.getElementById('docs-tree');
    if (!root) return;
    const all = root.querySelectorAll('a.docs-tree-link');
    all.forEach((a) => a.classList.remove('is-active'));
    const match = root.querySelector('a.docs-tree-link[data-doc-path="' + cssEscape(docPath) + '"]');
    if (match) {
      match.classList.add('is-active');
      // Ensure the containing <details> is open so the active leaf is visible.
      const section = match.closest('details');
      if (section && !section.open) section.open = true;
    }
  }

  // Minimal CSS.escape polyfill: we control the input format so a tight
  // allowlist replacement is enough.
  function cssEscape(s) {
    return s.replace(/[^a-zA-Z0-9_\-/]/g, '\\$&');
  }

  window.DocsTree = { mount: mount, setActive: setActive };
})();
