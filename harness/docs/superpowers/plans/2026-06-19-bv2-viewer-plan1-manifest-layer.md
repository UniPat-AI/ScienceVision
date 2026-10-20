# BabyVision-v2 Viewer — Plan 1: Manifest Decoupling Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish `viewer/manifest.json` as the single source of truth for data paths, build the `viewer/shared/manifest.js` browser-side loader, and add Python-side JSON Schema validation in CI. The result is a foundation that Plans 2 (Docs SPA) and 3 (Homepage redesign) can rely on without re-engineering paths.

**Architecture:** Two artifacts ship as a pair: (1) a JSON Schema (`viewer/shared/manifest.schema.json`) describing manifest shape, and a valid v0.1.0 document (`viewer/manifest.json`); (2) a tiny vanilla-JS loader (`viewer/shared/manifest.js`) that fetches, structurally validates, `Object.freeze`s, and exposes `window.Manifest`. A Python validator (`tools/validate_manifest.py`) runs in CI; a smoke HTML page (`viewer/_manifest_check.html`) lets us eyeball the loader in a real browser.

**Tech Stack:**
- Python ≥3.10 (already required by `pyproject.toml`)
- `jsonschema>=4.20` (new dependency — added to `dev` extras)
- pytest (already in `dev` extras)
- Vanilla browser JS (no new vendored libraries)

## Global Constraints

- Python floor: `>=3.10` (from `pyproject.toml` line 10) — never use 3.11+ syntax (e.g. no `Self`, no `match` exhaustiveness assumptions beyond 3.10).
- File-size cap: ≤450 lines for any file under `viewer/` mirrored material; ≤800 lines elsewhere (from `docs/platform/architecture.md` line 23).
- Commit message format: `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:`. No `Co-Authored-By:` trailer (attribution disabled globally per `~/.claude/settings.json`).
- Immutable patterns: no in-place mutation. `window.Manifest` must be `Object.freeze`d at load. Python validators must not mutate their inputs.
- No CDN requests at runtime: all browser-side JS must be local.
- Branch: `feat/medical-spatial-stage1` is the current working branch from prior plan; this work continues on the same branch unless reviewer asks for split.

---

## File Structure

### Files created or modified by this plan

| File | Action | Responsibility |
|---|---|---|
| `viewer/manifest.json` | CREATE | v0.1.0 manifest. Frozen single source for `brand`, `version`, `generated_at`, `counts`, `data{}`, `docs{}`, `domains[]`. Tracked in git. |
| `viewer/manifest.local.example.json` | CREATE | Example partner-side override (different `data.*` paths pointing at a partner's private dataset). Documents the swap-points without forcing partners to clone the schema. |
| `viewer/shared/manifest.schema.json` | CREATE | JSON Schema (draft 2020-12) describing the manifest shape: required keys, value types, regex constraints on paths, `domains[]` entry shape. |
| `viewer/shared/manifest.js` | CREATE | Browser loader. Fetches `manifest.json`, runs minimal client-side structural checks (presence of `brand`, `data`, `docs`, `domains`), `Object.freeze`s the parsed object, exposes as `window.Manifest`. Throws + renders a visible top-of-page banner on any failure. Size target: ~80 LOC. |
| `viewer/_manifest_check.html` | CREATE | Smoke page. Loads `manifest.js`, renders `brand`, `version`, total `counts.assets`, and a list of `domains[]`. Used to eyeball-verify the loader in a real browser. |
| `tools/validate_manifest.py` | CREATE | CLI script using `jsonschema`. Loads `viewer/manifest.json` and `viewer/shared/manifest.schema.json`; prints validation report; exit code 0 on pass, 1 on fail. ~60 LOC. |
| `tests/test_manifest_schema.py` | CREATE | pytest. Cases: (a) production `manifest.json` validates; (b) each required key, when removed, fails validation; (c) wrong-type counts fail; (d) malformed domain entry fails; (e) extra top-level key does NOT fail (forward compatibility). |
| `pyproject.toml` | MODIFY | Append `"jsonschema>=4.20"` to `dev` optional-extra. |
| `MIRROR.md` | (untouched) | No change — the mirror manifest doesn't cover `viewer/shared/manifest.*` since those are native BV2 additions. |

---

## Interface contract (frozen for Plans 2 & 3)

This is the public surface every later plan depends on.

**Browser side**, after `<script src="shared/manifest.js"></script>` loads:

```javascript
window.Manifest = Object.freeze({
  brand: "BabyVision-v2",              // string, required
  version: "0.1.0",                    // semver string, required
  generated_at: "2026-06-19T00:00:00Z",// ISO-8601 string
  counts: {                            // object, required
    assets: 0,                         //   number ≥ 0
    qagen: 0,                          //   number ≥ 0
    mcp_tools: 0,                      //   number ≥ 0
    leaderboard_entries: 0             //   number ≥ 0
  },
  data: {                              // object, required
    assets_index: "data/assets.index.json",
    qagen_index:  "data/qagen_index.json",
    leaderboard:  "data/leaderboard.json"
  },
  docs: {                              // object, required
    tree_index:   "docs/_index.json",
    search_index: "docs/_search.json",
    base_url:     "docs/"
  },
  domains: [                           // array, required, length ≥ 0
    { id: "medical", label: "Medical", color: "#56d364", shipped: true },
    // ...
  ]
});
```

**Failure mode:** if `manifest.json` is missing, malformed, or fails structural check, `manifest.js` throws synchronously *during load* AND injects a red banner div at the top of `<body>`:

```html
<div id="manifest-error" style="background:#f85149;color:white;padding:8px 16px;font-family:monospace">
  Manifest load failed: <reason>. See console.
</div>
```

This shape is **frozen for Plans 2 & 3**. Changes require revising this plan or a new plan amendment.

---

## Task 1: Define the JSON Schema and ship the initial manifest

**Files:**
- Create: `viewer/shared/manifest.schema.json`
- Create: `viewer/manifest.json`

**Interfaces:**
- Consumes: nothing (this is the root task)
- Produces: schema + sample doc that all other tasks validate against

- [ ] **Step 1: Write the schema file**

Create `viewer/shared/manifest.schema.json` with this content:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://babyvision.unipat.ai/viewer/manifest.schema.json",
  "title": "BabyVision-v2 viewer manifest",
  "type": "object",
  "required": ["brand", "version", "generated_at", "counts", "data", "docs", "domains"],
  "additionalProperties": true,
  "properties": {
    "brand": {
      "type": "string",
      "minLength": 1,
      "maxLength": 64
    },
    "version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+([-+].*)?$"
    },
    "generated_at": {
      "type": "string",
      "format": "date-time"
    },
    "counts": {
      "type": "object",
      "required": ["assets", "qagen", "mcp_tools", "leaderboard_entries"],
      "additionalProperties": false,
      "properties": {
        "assets":               { "type": "integer", "minimum": 0 },
        "qagen":                { "type": "integer", "minimum": 0 },
        "mcp_tools":            { "type": "integer", "minimum": 0 },
        "leaderboard_entries":  { "type": "integer", "minimum": 0 }
      }
    },
    "data": {
      "type": "object",
      "required": ["assets_index", "qagen_index", "leaderboard"],
      "additionalProperties": false,
      "properties": {
        "assets_index": { "type": "string", "minLength": 1 },
        "qagen_index":  { "type": "string", "minLength": 1 },
        "leaderboard":  { "type": "string", "minLength": 1 }
      }
    },
    "docs": {
      "type": "object",
      "required": ["tree_index", "search_index", "base_url"],
      "additionalProperties": false,
      "properties": {
        "tree_index":   { "type": "string", "minLength": 1 },
        "search_index": { "type": "string", "minLength": 1 },
        "base_url":     { "type": "string", "minLength": 1 }
      }
    },
    "domains": {
      "type": "array",
      "minItems": 0,
      "items": {
        "type": "object",
        "required": ["id", "label", "color", "shipped"],
        "additionalProperties": false,
        "properties": {
          "id": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$",
            "minLength": 1,
            "maxLength": 64
          },
          "label":   { "type": "string", "minLength": 1, "maxLength": 128 },
          "color":   { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
          "shipped": { "type": "boolean" }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Write the initial manifest**

Create `viewer/manifest.json` with this content:

```json
{
  "$schema": "shared/manifest.schema.json",
  "brand": "BabyVision-v2",
  "version": "0.1.0",
  "generated_at": "2026-06-19T00:00:00Z",
  "counts": {
    "assets": 0,
    "qagen": 0,
    "mcp_tools": 0,
    "leaderboard_entries": 0
  },
  "data": {
    "assets_index": "data/assets.index.json",
    "qagen_index":  "data/qagen_index.json",
    "leaderboard":  "data/leaderboard.json"
  },
  "docs": {
    "tree_index":   "docs/_index.json",
    "search_index": "docs/_search.json",
    "base_url":     "docs/"
  },
  "domains": [
    { "id": "medical",  "label": "Medical",  "color": "#56d364", "shipped": true  },
    { "id": "embodied", "label": "Embodied", "color": "#ffa657", "shipped": true  }
  ]
}
```

Note: only `medical` and `embodied` show `shipped: true` in v0.1.0 — these are the two demo families (Medical Spatial GT + Genesis_VP). The remaining 9 domains will be added in Plan 3 when § Domain coverage is implemented, pending GH #6/#7/#8 resolutions. This is intentional — Plan 1 only ships what's actually shipped today.

- [ ] **Step 3: Sanity-check by hand**

Run:
```bash
python -c "import json; json.load(open('viewer/manifest.json')); json.load(open('viewer/shared/manifest.schema.json')); print('OK')"
```

Expected output:
```
OK
```

- [ ] **Step 4: Commit**

```bash
git add viewer/manifest.json viewer/shared/manifest.schema.json
git commit -m "feat(viewer): add manifest.json + JSON Schema for data-path decoupling"
```

---

## Task 2: Python schema validator + tests

**Files:**
- Modify: `pyproject.toml` (add jsonschema to dev extras)
- Create: `tools/validate_manifest.py`
- Create: `tests/test_manifest_schema.py`

**Interfaces:**
- Consumes: `viewer/manifest.json` + `viewer/shared/manifest.schema.json` from Task 1
- Produces: `validate_manifest_file(manifest_path: Path, schema_path: Path) -> list[str]` (returns list of error messages; empty list on success). Public from `tools/validate_manifest.py`.

- [ ] **Step 1: Add `jsonschema` to dev extras**

Edit `pyproject.toml` lines 50-54. Change:

```toml
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "ruff>=0.4",
]
```

to:

```toml
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "ruff>=0.4",
    "jsonschema>=4.20",
]
```

Run:
```bash
pip install -e ".[dev]"
```

Expected: `jsonschema-4.x.y` installed, no other changes.

- [ ] **Step 2: Write the failing test (TDD red phase)**

Create `tests/test_manifest_schema.py`:

```python
"""Tests for viewer/manifest.json schema validation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.validate_manifest import validate_manifest_file, validate_manifest_obj

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "viewer" / "manifest.json"
SCHEMA_PATH = REPO_ROOT / "viewer" / "shared" / "manifest.schema.json"


@pytest.fixture
def valid_manifest() -> dict:
    """Load the committed production manifest as a fresh dict."""
    with MANIFEST_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture
def schema() -> dict:
    with SCHEMA_PATH.open() as fh:
        return json.load(fh)


def test_production_manifest_validates() -> None:
    """The shipped viewer/manifest.json must validate against its schema."""
    errors = validate_manifest_file(MANIFEST_PATH, SCHEMA_PATH)
    assert errors == [], f"Production manifest failed validation: {errors}"


@pytest.mark.parametrize("missing_key", ["brand", "version", "generated_at", "counts", "data", "docs", "domains"])
def test_missing_required_key_fails(valid_manifest: dict, schema: dict, missing_key: str) -> None:
    """Removing any required top-level key must produce a validation error."""
    bad = copy.deepcopy(valid_manifest)
    del bad[missing_key]
    errors = validate_manifest_obj(bad, schema)
    assert errors, f"Removing '{missing_key}' should have failed validation"


def test_counts_must_be_integer(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    bad["counts"]["assets"] = "zero"
    errors = validate_manifest_obj(bad, schema)
    assert errors, "String count should fail validation"


def test_negative_count_rejected(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    bad["counts"]["assets"] = -1
    errors = validate_manifest_obj(bad, schema)
    assert errors, "Negative count should fail validation"


def test_domain_color_must_be_hex(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    if not bad["domains"]:
        pytest.skip("manifest has no domains to mutate")
    bad["domains"][0]["color"] = "red"
    errors = validate_manifest_obj(bad, schema)
    assert errors, "Non-hex color should fail validation"


def test_domain_id_must_be_snake_case(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    if not bad["domains"]:
        pytest.skip("manifest has no domains to mutate")
    bad["domains"][0]["id"] = "Bad-Id"
    errors = validate_manifest_obj(bad, schema)
    assert errors, "Non-snake_case id should fail validation"


def test_extra_top_level_key_allowed(valid_manifest: dict, schema: dict) -> None:
    """Forward compatibility: schema allows unknown top-level keys."""
    ok = copy.deepcopy(valid_manifest)
    ok["future_field"] = {"experimental": True}
    errors = validate_manifest_obj(ok, schema)
    assert errors == [], f"Extra top-level keys should be allowed: {errors}"
```

- [ ] **Step 3: Run the test to verify it fails**

Run:
```bash
pytest tests/test_manifest_schema.py -v
```

Expected: ImportError or ModuleNotFoundError because `tools.validate_manifest` does not exist yet.

- [ ] **Step 4: Write the validator**

Create `tools/validate_manifest.py`:

```python
"""Validate viewer/manifest.json against viewer/shared/manifest.schema.json.

CLI usage:
    python tools/validate_manifest.py
    python tools/validate_manifest.py --manifest other/manifest.json

Exit code 0 on success, 1 on validation failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "viewer" / "manifest.json"
DEFAULT_SCHEMA = REPO_ROOT / "viewer" / "shared" / "manifest.schema.json"


def validate_manifest_obj(manifest: dict, schema: dict) -> list[str]:
    """Validate a manifest object against a schema object.

    Returns a list of human-readable error messages; empty list means valid.
    Does not mutate either input.
    """
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(manifest), key=lambda e: e.path):
        location = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{location}: {err.message}")
    return errors


def validate_manifest_file(manifest_path: Path, schema_path: Path) -> list[str]:
    """Load files from disk and validate.

    Returns the same list-of-errors contract as validate_manifest_obj.
    Raises FileNotFoundError if either file is missing.
    """
    with manifest_path.open() as fh:
        manifest = json.load(fh)
    with schema_path.open() as fh:
        schema = json.load(fh)
    return validate_manifest_obj(manifest, schema)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="Path to manifest.json (default: viewer/manifest.json)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA,
                        help="Path to schema.json (default: viewer/shared/manifest.schema.json)")
    args = parser.parse_args(argv)

    errors = validate_manifest_file(args.manifest, args.schema)
    if errors:
        print(f"FAIL: {args.manifest} has {len(errors)} validation error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"OK: {args.manifest} validates against {args.schema}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the test to verify it passes**

Run:
```bash
pytest tests/test_manifest_schema.py -v
```

Expected: all tests PASS (8 tests: 1 production validates, 7 parametrized missing-key tests, plus the type/format/range/extra-key tests = ~13 total).

- [ ] **Step 6: Run the CLI manually**

Run:
```bash
python tools/validate_manifest.py
```

Expected:
```
OK: /Users/.../BabyVision-v2/viewer/manifest.json validates against /Users/.../BabyVision-v2/viewer/shared/manifest.schema.json
```

Exit code 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tools/validate_manifest.py tests/test_manifest_schema.py
git commit -m "feat(tools): add manifest schema validator + pytest coverage"
```

---

## Task 3: Browser-side manifest loader

**Files:**
- Create: `viewer/shared/manifest.js`
- Create: `viewer/_manifest_check.html`

**Interfaces:**
- Consumes: `viewer/manifest.json` (HTTP-fetched at page load)
- Produces: `window.Manifest` (frozen object, structure per "Interface contract" section above)
- Produces: visible red banner div `#manifest-error` on failure

- [ ] **Step 1: Write the loader**

Create `viewer/shared/manifest.js`:

```javascript
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
    throw new Error('Manifest: ' + message);
  }

  function checkKeys(obj, requiredKeys, label) {
    for (const k of requiredKeys) {
      if (!(k in obj)) {
        fail("missing required key '" + label + '.' + k + "'");
      }
    }
  }

  function findManifestUrl() {
    // Default: same-directory manifest.json relative to the entry page.
    // Pages deep in viewer/ subdirs can override via <meta name="manifest-url" content="...">.
    if (typeof document === 'undefined') return 'manifest.json';
    const meta = document.querySelector('meta[name="manifest-url"]');
    if (meta && meta.content) return meta.content;
    // If we're inside viewer/ but the page lives at viewer/<page>.html, manifest.json is "manifest.json".
    // If we're under viewer/some/sub/, we walk back. Simplest fallback: try relative, then absolute.
    return 'manifest.json';
  }

  function loadManifest() {
    const url = findManifestUrl();
    return fetch(url, { cache: 'no-cache' })
      .then((resp) => {
        if (!resp.ok) {
          fail('HTTP ' + resp.status + ' fetching ' + url);
        }
        return resp.json();
      })
      .catch((err) => {
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
        // Deep-freeze: brand-new object, freeze top + nested.
        Object.freeze(obj.counts);
        Object.freeze(obj.data);
        Object.freeze(obj.docs);
        obj.domains.forEach(Object.freeze);
        Object.freeze(obj.domains);
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
```

- [ ] **Step 2: Write the smoke check page**

Create `viewer/_manifest_check.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Manifest check — BabyVision-v2</title>
<style>
  body { background: #0d1117; color: #c9d1d9; font: 14px/1.5 -apple-system, system-ui, sans-serif; padding: 24px; }
  h1 { font-size: 18px; }
  table { border-collapse: collapse; margin-top: 12px; }
  th, td { padding: 4px 12px; text-align: left; border-bottom: 1px solid #30363d; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  th { color: #8b949e; }
  .ok { color: #56d364; }
  .fail { color: #f85149; }
</style>
</head>
<body>
<h1>Manifest check</h1>
<div id="status">loading…</div>
<table id="fields" hidden>
  <thead><tr><th>field</th><th>value</th></tr></thead>
  <tbody></tbody>
</table>
<h2 style="margin-top:24px;font-size:14px">Domains</h2>
<table id="domains" hidden>
  <thead><tr><th>id</th><th>label</th><th>color</th><th>shipped</th></tr></thead>
  <tbody></tbody>
</table>
<script src="shared/manifest.js"></script>
<script>
  window.ManifestReady.then((m) => {
    document.getElementById('status').innerHTML = '<span class="ok">OK — Manifest loaded.</span>';
    const ftbody = document.querySelector('#fields tbody');
    [
      ['brand', m.brand],
      ['version', m.version],
      ['generated_at', m.generated_at],
      ['counts.assets', m.counts.assets],
      ['counts.qagen', m.counts.qagen],
      ['counts.mcp_tools', m.counts.mcp_tools],
      ['counts.leaderboard_entries', m.counts.leaderboard_entries],
      ['data.assets_index', m.data.assets_index],
      ['docs.tree_index', m.docs.tree_index],
    ].forEach(([k, v]) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${k}</td><td>${v}</td>`;
      ftbody.appendChild(tr);
    });
    document.getElementById('fields').hidden = false;

    const dtbody = document.querySelector('#domains tbody');
    m.domains.forEach((d) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${d.id}</td><td>${d.label}</td>` +
        `<td style="color:${d.color}">${d.color}</td>` +
        `<td>${d.shipped ? '✓' : '—'}</td>`;
      dtbody.appendChild(tr);
    });
    document.getElementById('domains').hidden = false;
  }).catch((err) => {
    document.getElementById('status').innerHTML =
      '<span class="fail">FAIL: ' + err.message + '</span>';
  });
</script>
</body>
</html>
```

- [ ] **Step 3: Verify in a real browser**

Start the static server from repo root:

```bash
python -m http.server 8000 --directory .
```

Open in browser: `http://localhost:8000/viewer/_manifest_check.html`

Expected: green "OK — Manifest loaded." status, table showing `brand=BabyVision-v2 · version=0.1.0 · counts.assets=0 · …`, and a domains table with two rows (`medical` and `embodied`).

Open devtools console — expected: no errors, no warnings.

Stop the server (`Ctrl+C`).

- [ ] **Step 4: Verify immutability via console**

While server is running, in devtools console:

```javascript
window.Manifest.brand = 'hacked';
window.Manifest.counts.assets = 999;
```

Expected: both assignments either silently fail (non-strict mode) or throw (strict mode). Then:

```javascript
console.log(window.Manifest.brand, window.Manifest.counts.assets);
```

Expected: `BabyVision-v2 0`. The freeze held.

- [ ] **Step 5: Verify failure mode**

While server still running, temporarily corrupt `viewer/manifest.json` (e.g., delete the `brand` key) and reload `_manifest_check.html`.

Expected: red banner at top of page reading `Manifest load failed: missing required key 'manifest.brand'. See console.` Console has a matching `[manifest]` error.

Restore `manifest.json` to the original state (`git restore viewer/manifest.json`).

- [ ] **Step 6: Commit**

```bash
git add viewer/shared/manifest.js viewer/_manifest_check.html
git commit -m "feat(viewer): browser-side manifest.js loader + smoke check page"
```

---

## Task 4: Partner-local example manifest

**Files:**
- Create: `viewer/manifest.local.example.json`

**Interfaces:**
- Consumes: schema from Task 1
- Produces: a documented example of how partners override `data.*` paths

- [ ] **Step 1: Write the example**

Create `viewer/manifest.local.example.json`:

```json
{
  "$schema": "shared/manifest.schema.json",
  "brand": "BabyVision-v2 (Partner — Example Corp)",
  "version": "0.1.0",
  "generated_at": "2026-06-19T00:00:00Z",
  "counts": {
    "assets": 1234,
    "qagen": 56,
    "mcp_tools": 75,
    "leaderboard_entries": 0
  },
  "data": {
    "assets_index": "../private-data/assets.index.json",
    "qagen_index":  "../private-data/qagen_index.json",
    "leaderboard":  "../private-data/leaderboard.json"
  },
  "docs": {
    "tree_index":   "docs/_index.json",
    "search_index": "docs/_search.json",
    "base_url":     "docs/"
  },
  "domains": [
    { "id": "medical",     "label": "Medical (private subset)", "color": "#56d364", "shipped": true },
    { "id": "embodied",    "label": "Embodied (private subset)", "color": "#ffa657", "shipped": true },
    { "id": "topological_photonics", "label": "Topological Photonics", "color": "#ff7b72", "shipped": true }
  ]
}
```

- [ ] **Step 2: Validate the example against the schema**

Run:
```bash
python tools/validate_manifest.py --manifest viewer/manifest.local.example.json
```

Expected:
```
OK: viewer/manifest.local.example.json validates against viewer/shared/manifest.schema.json
```

Exit code 0.

- [ ] **Step 3: Commit**

```bash
git add viewer/manifest.local.example.json
git commit -m "docs(viewer): partner-local manifest example documenting swap-points"
```

---

## Task 5: Document the manifest contract for partners

**Files:**
- Modify: `docs/platform/architecture.md` — add a "Manifest layer" subsection

**Interfaces:**
- Consumes: everything Tasks 1–4 shipped
- Produces: partner-facing documentation of the manifest contract

- [ ] **Step 1: Inspect current architecture doc**

Run:
```bash
wc -l docs/platform/architecture.md
grep -n "^#\|^##" docs/platform/architecture.md
```

Note the existing section structure. The new "Manifest layer" section goes immediately before "Mirror policy" if such a section exists, or as the final section otherwise.

- [ ] **Step 2: Append the Manifest layer section**

Append to `docs/platform/architecture.md`:

```markdown
## Manifest layer

The viewer is decoupled from any specific dataset through a single
`viewer/manifest.json` file. Same viewer code runs against BabyVision-v2's
public demo data and against a partner's private dataset, with only the
manifest differing.

### Schema

`viewer/shared/manifest.schema.json` (JSON Schema draft 2020-12) defines the
exact shape. Required top-level keys: `brand`, `version`, `generated_at`,
`counts`, `data`, `docs`, `domains`. See the schema file for full rules.

### Validation

```bash
python tools/validate_manifest.py
python tools/validate_manifest.py --manifest viewer/manifest.local.example.json
```

CI runs the validator on every PR; manifest changes that fail the schema
block the merge.

### Browser usage

Pages include the loader once:

```html
<script src="shared/manifest.js"></script>
```

The loader fetches and freezes `manifest.json`, exposing it as
`window.Manifest`. Async consumers can await `window.ManifestReady`:

```javascript
window.ManifestReady.then((m) => {
  // m.data.assets_index, m.docs.tree_index, m.domains, etc.
});
```

On failure (missing file, malformed JSON, missing required keys), the
loader injects a red `#manifest-error` banner at the top of the page and
throws. Pages cannot silently continue with a half-valid manifest.

### Partner-local overrides

Partners replace `viewer/manifest.json` with their own version pointing at
private data paths. See `viewer/manifest.local.example.json` for the
documented swap-points (typically only the `data.*` paths and the `domains`
list change; `docs.*` and `brand` usually stay).

### Immutability

The loader `Object.freeze`s the parsed manifest and its nested objects.
Pages must not mutate `window.Manifest`; derive per-page state into
page-scope objects instead.
```

- [ ] **Step 3: Verify the doc still passes any link / format check**

Run:
```bash
python -c "open('docs/platform/architecture.md').read()" && echo "readable OK"
```

(BV2 has no markdown linter wired up yet, so this is just a smoke check.)

- [ ] **Step 4: Commit**

```bash
git add docs/platform/architecture.md
git commit -m "docs(platform): document manifest layer contract"
```

---

## Task 6: Wire CI to run the validator

**Files:**
- Inspect / modify: any existing CI workflow under `.github/workflows/`

**Interfaces:**
- Consumes: validator from Task 2
- Produces: PR-blocking schema check

- [ ] **Step 1: Inspect current CI**

Run:
```bash
ls -la .github/workflows/ 2>&1
```

If the directory does not exist:
```bash
mkdir -p .github/workflows
```

If there's already a `tests.yml` or similar, the validator step is appended to it. Otherwise create a new minimal workflow.

- [ ] **Step 2a (if a workflow file exists): append a validation step**

Find the existing job that runs pytest. Add a step immediately after `pip install -e ".[dev]"`:

```yaml
      - name: Validate viewer manifest
        run: python tools/validate_manifest.py
```

- [ ] **Step 2b (if no workflow exists): create one**

Create `.github/workflows/manifest-check.yml`:

```yaml
name: manifest-check

on:
  pull_request:
    paths:
      - 'viewer/manifest.json'
      - 'viewer/shared/manifest.schema.json'
      - 'tools/validate_manifest.py'
      - 'tests/test_manifest_schema.py'
      - 'pyproject.toml'
  push:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      - name: Install dev deps
        run: pip install -e ".[dev]"
      - name: Validate manifest against schema
        run: python tools/validate_manifest.py
      - name: Run manifest schema tests
        run: pytest tests/test_manifest_schema.py -v
```

- [ ] **Step 3: Lint the YAML locally**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/manifest-check.yml'))" && echo OK
```

Expected: `OK`. If `yaml` is missing, install: `pip install pyyaml`, then re-run.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/manifest-check.yml
git commit -m "ci: validate viewer manifest against schema on PR"
```

If the validator was appended to an existing workflow instead, adjust the `git add` path accordingly and use the same commit message.

---

## Task 7: Plan-1 acceptance check

**Files:** none modified — read-only verification.

**Interfaces:** none — terminal verification task.

- [ ] **Step 1: Re-run all tests cleanly**

Run:
```bash
pytest tests/test_manifest_schema.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the validator on both manifests**

Run:
```bash
python tools/validate_manifest.py
python tools/validate_manifest.py --manifest viewer/manifest.local.example.json
```

Expected: both print `OK` and return exit 0.

- [ ] **Step 3: Real-browser smoke**

Run:
```bash
python -m http.server 8000 --directory . &
SERVER_PID=$!
sleep 1
curl -s http://localhost:8000/viewer/manifest.json | python -m json.tool > /dev/null && echo "manifest fetchable: OK"
curl -s http://localhost:8000/viewer/shared/manifest.js | head -c 100
echo
kill $SERVER_PID
```

Expected:
```
manifest fetchable: OK
// viewer/shared/manifest.js — single source of truth loader.
// Fetches viewer/manifest.json at page load, does minimal structur
```

Then open `http://localhost:8000/viewer/_manifest_check.html` in a real browser and confirm the green OK status, the fields table, and the domain rows render. No banner. No console errors.

- [ ] **Step 4: Verify file count and lines**

Run:
```bash
wc -l viewer/manifest.json viewer/manifest.local.example.json \
      viewer/shared/manifest.schema.json viewer/shared/manifest.js \
      viewer/_manifest_check.html tools/validate_manifest.py \
      tests/test_manifest_schema.py
```

Expected: every individual file is well under 800 lines. `manifest.js` should be roughly 80–100 lines, `validate_manifest.py` roughly 50–70 lines.

- [ ] **Step 5: Git log sanity**

Run:
```bash
git log --oneline -10
```

Expected: 6 new commits from this plan (Tasks 1, 2, 3, 4, 5, 6), each with a conventional prefix.

- [ ] **Step 6: Mark Plan 1 done**

No code change. State in conversation: "Plan 1 complete. Proceeding to write Plan 2 (Docs SPA)." Then trigger Plan 2 authoring.

---

## What this plan explicitly does NOT do

The following items are spec requirements but belong to **Plan 2** or **Plan 3** and are intentionally out of scope here. They are listed only so reviewers don't expect them in this plan's deliverable.

- Patching existing per-modality pages (`molstar.html`, `niivue.html`, etc.) to consume `window.Manifest` for data paths. **→ Plan 3** (homepage redesign sweep).
- Rewriting `viewer/index.html` to the new homepage layout (hero, module launcher, task cards, etc.). **→ Plan 3.**
- Building `viewer/docs.html` (the docs SPA). **→ Plan 2.**
- Building `viewer/leaderboard.html`. **→ Plan 3.**
- `scripts/build_docs_index.py`, `build_docs_search.py`, `build_mcp_docs.py`. **→ Plan 2.**
- MCP virtual-doc composer. **→ Plan 2.**
- Filling in the remaining 9 domains in `manifest.json.domains[]`. **→ Plan 3**, pending GH #6/#7/#8 resolutions.
- Compare table content. **→ tracked in GH #5**, separate from any plan.

---

## Self-review against the spec

| Spec section | Plan 1 coverage |
|---|---|
| §1 Goals — manifest-driven decoupling | Task 1 (schema), Task 3 (loader), Task 4 (partner example) |
| §1 Non-goals — no SSG, no CDN | enforced by Task 3 (vanilla JS, no vendor lib) |
| §2 Directory layout — `manifest.json`, `manifest.schema.json`, `manifest.js`, `manifest.local.example.json` | all created across Tasks 1, 3, 4 |
| §6 Data flow diagram | matches Task 3's loader behavior and exposed `window.Manifest` shape |
| §6 Schema example | Task 1 produces the schema; Task 1 ships an initial manifest matching the example |
| §6 Immutability | Task 3 implements `Object.freeze` on top + nested; Task 7 Step 3 verifies |
| §7 Static checks (CI) — manifest validates against schema | Task 6 adds the CI step |
| Visual language — no Plan 1 surfaces beyond `_manifest_check.html` smoke | smoke page uses inline GitHub-dark palette; not a permanent UI |
| §10 Out of scope | none of the deferred items are touched here |

No gaps. No placeholders. All steps contain executable commands or complete code. Type / signature consistency verified: `validate_manifest_obj` and `validate_manifest_file` are referenced from tests with the exact signatures defined in Task 2.
