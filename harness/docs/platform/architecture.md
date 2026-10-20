# Architecture

> One-page overview of how the BabyVision-v2 platform pieces fit together.

> Status: **draft / scaffold**. Diagram and component breakdown to be filled in.

## Components

- **MCP server** (`sci3d_agent/mcp/`): 56 tools across 6 categories. Entry point: `sciencevision-mcp`. Spec: `docs/mcp/MCP_SERVER_SPEC.md` (mirrored).
- **Agent runner** (`sci3d_agent/agent.py`): SWE-Vision-shaped loop; `sci3d_agent/cli.py` is the CLI.
- **Viewer** (`viewer/*.html`): URL-parameterized static pages. MCP `sv_build_*_url` tools generate links.
- **QA-gen pipeline** (`QA-Gen/`): deterministic at runtime; LLM cost is build-time only. `QA-Gen/scripts/` holds shared utilities; `QA-Gen/tasks/<batch>/` holds each task batch (e.g. `MedicalKAxisPilot/`, `Genesis_VP_Hard/`).
- **`_matched/` whitelist** (`datasets/_matched/`): 3D-grounded asset whitelist consumed by all downstream tools.
- **SAM service**: HTTP service at `SAM_SERVICE_URL` (default `http://localhost:8900`). Fake server for dev: `FAKE_SAM_QUALITY=noisy python -m scripts.sam_service.fake_server`.

## Cross-cutting conventions

- Immutable session state (`@dataclass(frozen=True)` in `vision3d/_types.py`)
- REGISTRY-lookup dispatch (no hard imports across categories)
- Error envelope (`{"error": "..."}`) rather than exceptions across MCP boundary
- NIfTI axis convention: `sagittal=0, coronal=1, axial=2`
- Files ≤450 lines in `vision3d/`, ≤800 lines elsewhere

(All above is mirrored convention from ScienceVision; see `CLAUDE.md` in the source repo if you need the rationale.)

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

### Event API

The loader dispatches a `manifest:ready` CustomEvent on `window` after the
manifest has been frozen and exposed. Pages that prefer event listeners
over promise consumers can use it:

```javascript
window.addEventListener('manifest:ready', (e) => {
  // e.detail is the same frozen manifest as window.Manifest
});
```

### URL override for non-root pages

By default the loader fetches `manifest.json` relative to the page that
includes `manifest.js`. Pages located deeper inside `viewer/` (e.g.
`viewer/some/sub/page.html`) can override the lookup with a `<meta>` tag
in `<head>`:

```html
<meta name="manifest-url" content="../../manifest.json">
<script src="../../shared/manifest.js"></script>
```

The loader prefers the meta value over the default; everything else in
the contract is unchanged.

### Partner-local overrides

Partners replace `viewer/manifest.json` with their own version pointing at
private data paths. See `viewer/manifest.local.example.json` for the
documented swap-points (typically only the `data.*` paths and the `domains`
list change; `docs.*` and `brand` usually stay).

### Immutability

The loader `Object.freeze`s the parsed manifest and its nested objects.
Pages must not mutate `window.Manifest`; derive per-page state into
page-scope objects instead.
