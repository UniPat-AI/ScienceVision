# Controlled viewer adapter status

The repository has a real local host-side session implementation under
`sci3d_agent/mcp/tools/vision3d/`, but it is not a solver-facing controlled
viewer adapter.

The current `sv3d_open_scene` contract accepts `qid`, `asset_path`, and
`mask_path`. Its host-side inspectors read asset files and its session outputs
include scene metadata such as bounding boxes, mask identifiers, voxel counts,
and centroids. The legacy `sci3d_agent/viewer_bridge.py` remains an unavailable
stub. That raw MCP contract has no anonymous scene registry or solver-safe
attestation boundary. It also remains unsuitable for the Mol chirality asset.

For those reasons QA-Gen does not connect the existing MCP API to a solver.
`qagen.viewer.ControlledViewerAdapter` defines the required boundary, including
a host-only anonymous scene resolver, solver-scoped operations, opaque sessions,
sanitized observations, and attested traces. Measurement operations are denied
unless the adapter profile explicitly identifies the same operation as
viewer-native.

Run the static backend/profile diagnostic with:

```bash
qa-gen viewer diagnose \
  --workspace /path/to/workspace \
  --adapter-config configs/viewer-adapters/sciencevision-mcp-local.template.yaml
```

The committed template is intentionally disabled and the command exits with
the gate-pending code. The diagnostic reads only local configuration and the
backend's source/schema files; it does not import the backend, open a scene,
read an asset, start a browser, or access the network.

The local chirality pilot now has a concrete host proxy under
`qagen.viewer.chirality_host`. It builds its anonymous mapping only below the
runtime workspace's `.qagen/private/` directory, serves Mol* and the selected
asset through a loopback-only allowlist, blocks browser requests outside that
origin, and exposes only `rotate` and `render`. Each action stores a private PNG
and chained receipt while returning only the render digest, controlled camera,
and receipt attestation.

The viewer does not accept an ad-hoc "version approved" flag. Before preparing
the scene, the same private workspace must contain the canonical SeedIndex
written by a second `seed sync` after a digest-bound resolution receipt accepts
version `1.0.3`. The viewer verifies the current and frozen SeedIndex copies,
the receipt and its original request, inventory and SourceSpec digests, exact
observed labels, and resolved version.

Prepare the scene after that seed workflow with:

```bash
qa-gen viewer chirality-prepare \
  --workspace /path/to/workspace \
  --package-root "$QAGEN_EV2_SOURCE_ROOT" \
  --source-spec configs/sources/ev2_seed_source_spec.yaml \
  --seed-task-id HOST_ONLY_VERIFIED_SEED
```

Missing, stale, duplicated, tampered, or mismatched SeedIndex/request/receipt
artifacts leave the registry blocked. A valid resolution removes the version
blocker, but successful runtime diagnosis or rendering remains local-only and
does not make the SourceSpec production eligible.

General production integration remains blocked until all of the following are resolved:

- release policy accepts the resulting provenance lock rather than this pilot-only registry;
- each additional asset family has a separately validated host adapter;
- any future measurement operation is implemented viewer-natively before it is advertised.
