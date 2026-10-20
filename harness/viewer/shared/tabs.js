// SPA tab router for /viewer/. Single source of truth for which tab is
// active. Public API:
//   activate(tabId | null)      — switch tab, also fired by hash sync
//   setBadge(tabId, value)      — update [data-count="<tabId>"] elements
// See docs/superpowers/specs/2026-06-03-viewer-spa-tabs-design.md §6.

// TAB_IDS is populated from DOM at DOMContentLoaded so adding a new
// <button role="tab" data-tab="..."> to index.html requires no JS change.
// Until then, fall back to the known v1.5 set so early activate() calls
// (e.g. from hashchange firing before DOMContentLoaded) still resolve.
let TAB_IDS = ["assets", "vqa", "matched", "qagen", "tool"];
const HOME_ID = null;

let _current = HOME_ID;

function _hashFor(tabId) {
  return tabId === null ? window.location.pathname : `#${tabId}`;
}

function _idsToShow(tabId) {
  // Returns the set of element ids that should be visible.
  return tabId === null ? new Set(["home-view"]) : new Set([`pane-${tabId}`]);
}

export function activate(tabId) {
  if (tabId !== null && !TAB_IDS.includes(tabId)) {
    // Unknown tab id — fall back to Home silently.
    tabId = HOME_ID;
  }
  if (tabId === _current) return;
  _current = tabId;

  // 1. aria-selected on tab buttons
  for (const btn of document.querySelectorAll("[role=tab]")) {
    btn.setAttribute(
      "aria-selected",
      String(btn.dataset.tab === tabId)
    );
  }

  // 2. show/hide #home-view and all .tab-pane
  const show = _idsToShow(tabId);
  const allViews = [
    document.getElementById("home-view"),
    ...document.querySelectorAll(".tab-pane"),
  ].filter(Boolean);
  for (const el of allViews) {
    el.hidden = !show.has(el.id);
  }

  // 3. URL hash (replaceState — never pushState; see §6.2)
  const target = _hashFor(tabId);
  if (
    target !== window.location.hash &&
    !(tabId === HOME_ID && window.location.hash === "")
  ) {
    history.replaceState(null, "", target);
  }

  // 4. fire reserved hook (no v1 consumer)
  window.dispatchEvent(
    new CustomEvent("sv:tab-activated", { detail: { tabId } })
  );
}

export function setBadge(tabId, value) {
  const text = String(value);
  for (const el of document.querySelectorAll(
    `[data-count="${tabId}"]`
  )) {
    el.textContent = text;
    el.classList.toggle("badge--error", text === "!");
  }
}

function _syncFromHash() {
  const raw = window.location.hash.slice(1); // '#assets' → 'assets'
  activate(raw === "" ? HOME_ID : raw);
}

document.addEventListener("DOMContentLoaded", () => {
  // Replace the fallback TAB_IDS with whatever the current DOM declares.
  // This is the single source of truth from here on.
  const fromDom = Array.from(
    document.querySelectorAll("[role=tab][data-tab]")
  ).map(b => b.dataset.tab);
  if (fromDom.length > 0) TAB_IDS = fromDom;

  _syncFromHash();
  for (const btn of document.querySelectorAll("[role=tab]")) {
    btn.addEventListener("click", () => activate(btn.dataset.tab));
  }
  for (const el of document.querySelectorAll(".home-link")) {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      activate(HOME_ID);
    });
  }
  window.addEventListener("hashchange", _syncFromHash);
});
