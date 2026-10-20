// viewer/shared/docs/docs-toc.js — right-column outline + scrollspy.
//
// Renders into #docs-toc using the headings list emitted by docs-app.js.
// Uses IntersectionObserver to mark the currently-in-view heading.

(function () {
  'use strict';

  let observer = null;

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function render(headings) {
    const wrap = document.getElementById('docs-toc-wrap') || document.querySelector('.docs-toc-wrap');
    const root = document.getElementById('docs-toc');
    if (!root) return;
    clear(root);

    // If there are fewer than 2 H2-level headings, hide the whole TOC column.
    const h2count = headings.filter((h) => h.level === 2).length;
    if (h2count < 2) {
      if (wrap) wrap.style.display = 'none';
      return;
    } else {
      if (wrap) wrap.style.display = '';
    }

    for (const h of headings) {
      const a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.text;
      a.dataset.targetId = h.id;
      a.className = 'toc-l' + h.level;
      root.appendChild(a);
    }
  }

  function attachScrollspy(root, headings) {
    if (observer) { observer.disconnect(); observer = null; }
    if (!('IntersectionObserver' in window)) return;
    const tocLinks = document.querySelectorAll('#docs-toc a');
    if (!tocLinks.length) return;
    const linkByTarget = new Map();
    tocLinks.forEach((a) => linkByTarget.set(a.dataset.targetId, a));

    const visible = new Set();
    observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      }
      // Active = first heading in document order that is currently visible.
      // Falls back to the first heading otherwise.
      let activeId = null;
      for (const h of headings) {
        if (visible.has(h.id)) { activeId = h.id; break; }
      }
      tocLinks.forEach((a) => a.classList.toggle('is-active', a.dataset.targetId === activeId));
    }, {
      // Trigger when heading enters the upper third of the viewport.
      rootMargin: '0px 0px -66% 0px',
      threshold:  [0, 1],
    });
    for (const h of headings) {
      const el = root.querySelector('#' + cssEscape(h.id));
      if (el) observer.observe(el);
    }
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_\-]/g, '\\$&');
  }

  // Smooth click → scroll (no full reload).
  document.addEventListener('click', (e) => {
    const a = e.target.closest('#docs-toc a');
    if (!a) return;
    const id = a.dataset.targetId;
    const target = document.getElementById(id);
    if (target) {
      e.preventDefault();
      history.replaceState(history.state, '', '#' + id);
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  window.addEventListener('docs:rendered', (e) => {
    const { headings, root } = e.detail;
    render(headings);
    attachScrollspy(root, headings);
  });

  window.DocsToc = { render: render };
})();
