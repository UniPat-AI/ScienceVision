# MCP library — the tool surface an expert annotates with

This is the tool surface of the **expert annotation platform**, per viewer.
If you are about to say "the question's tools were X", read the viewer's list
below, not a flat list — a tool is callable only in the viewers that declare it.

For the agent-side server (`sciencevision-mcp`, *ccmcp*) and how it diverges,
see [Relationship to ccmcp](#relationship-to-ccmcp) at the end.

## Provenance

| | |
|---|---|
| registries | `tools/offline_task_manager/expert_app/mcp/registries/*.json` (7 files) |
| source | `origin/prod` @ `6c7e3794` (2026-09-12); `prod` and `dev` are the same commit |
| entries | 127 entry records naming 59 distinct tools |
| expert-visible | 94 of 127 entries |

The registries are the authority. A count quoted from anywhere else is a
count of something else.

## How an expert's toolset is decided

The registry alone does not decide it. `resolve_tool_policy` in
`tools/offline_task_manager/tool_policy.py` builds the set the Functions panel
offers:

```python
public = [t for t in registry["tools"]
          if t.get("visibility") == "public"
          and t["name"] not in _EXPERT_UI_DENYLIST]   # {sv3d_render, sv3d_save_operation_trace}
# then per task: public - tool_policy.forbidden_tools
```

Two consequences worth holding onto:

- `visibility: internal` entries are listed in the registry but never reach
  the panel. They are 33 of 127 entries here.
- `tool_policy.allowed_tools` **no longer narrows anything** — the registry's
  public set is the only source of what an expert may use. The field is still
  read out of stored payloads so historical bundles keep validating; it is not
  what gated the expert. Use `visibility: internal` or `forbidden_tools` instead.

Both denylisted names are already `internal` in every registry, so the denylist
removes nothing further: expert-visible == `public` for this revision.

## The seven viewer families

| registry | entries | expert-visible | internal | covers |
|---|---:|---:|---:|---|
| `molstar.v1` | 9 | 6 | 3 | molecular structure — legacy surface |
| `molstar.v2` | 18 | 15 | 3 | molecular structure — current |
| `niivue.v1` | 14 | 12 | 2 | volumetric imaging (NIfTI) and masks |
| `occt.v1` | 26 | 18 | 8 | mechanical CAD, B-Rep and parts |
| `gltf.v1` | 17 | 13 | 4 | mesh/glTF scenes |
| `bim_coordination.v1` | 22 | 16 | 6 | multi-model building coordination |
| `architecture_inspection.v1` | 21 | 14 | 7 | single-building inspection |
| **all** | **127** | **94** | **33** | |

## Tools that more than one family declares

**In all seven (4):**

- `sv3d_clear_measurements` — visibility: public
- `viewer_get_state` — visibility: internal
- `viewer_reset` — visibility: internal
- `viewer_zoom` — visibility: public

So the surface every family shares, and that an expert can actually call, is
just `viewer_zoom` and `sv3d_clear_measurements` — the other two are `internal`.

**In two to six (20):**

| tool | families | which |
|---|---:|---|
| `sv3d_orbit` | 6 | molstar.v1, molstar.v2, occt.v1, gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_measure` | 5 | molstar.v1, molstar.v2, niivue.v1, occt.v1, architecture_inspection.v1 |
| `viewer_pan` | 5 | molstar.v2, niivue.v1, gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_fit_plane` | 4 | occt.v1, gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_measure_angle` | 4 | occt.v1, gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_set_view` | 4 | occt.v1, gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `viewer_pick_point` | 4 | occt.v1, gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_annotate` | 3 | gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_measure_area` | 3 | gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_measure_distance` | 3 | occt.v1, gltf.v1, bim_coordination.v1 |
| `sv3d_set_camera` | 3 | molstar.v1, molstar.v2, bim_coordination.v1 |
| `sv3d_set_cutaway` | 3 | occt.v1, gltf.v1, architecture_inspection.v1 |
| `viewer_pick_vertex` | 3 | gltf.v1, bim_coordination.v1, architecture_inspection.v1 |
| `sv3d_fit_view` | 2 | occt.v1, bim_coordination.v1 |
| `sv3d_render` | 2 | occt.v1, architecture_inspection.v1 |
| `sv3d_set_model_opacity` | 2 | occt.v1, bim_coordination.v1 |
| `sv3d_set_orthographic_view` | 2 | gltf.v1, architecture_inspection.v1 |
| `sv3d_set_symmetry` | 2 | molstar.v1, molstar.v2 |
| `sv3d_switch_structure` | 2 | molstar.v1, molstar.v2 |
| `sv3d_toggle_layer` | 2 | gltf.v1, architecture_inspection.v1 |

The remaining **35 names are declared by exactly one family** — listed below.

## Every family, in full

`shared` marks a name that more than one family declares.

### `molstar.v1` — 9 entries (6 expert-visible, 3 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `sv3d_orbit` | yes | shared |
| `viewer_zoom` | yes | shared |
| `sv3d_measure` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_switch_structure` | yes | shared |
| `sv3d_set_symmetry` | yes | shared |
| `sv3d_set_camera` | no | shared |

### `molstar.v2` — 18 entries (15 expert-visible, 3 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `sv3d_orbit` | yes | shared |
| `viewer_zoom` | yes | shared |
| `viewer_pan` | yes | shared |
| `sv3d_measure` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_switch_structure` | yes | shared |
| `sv3d_set_symmetry` | yes | shared |
| `sv3d_set_camera` | no | shared |
| `sv3d_set_component_visibility` | yes |  |
| `sv3d_set_representation` | yes |  |
| `sv3d_clash` | yes |  |
| `sv3d_get_unit_cell` | yes |  |
| `sv3d_measure_atoms` | yes |  |
| `sv3d_set_chain_visibility` | yes |  |
| `sv3d_focus_residue` | yes |  |
| `sv3d_set_atom_labels` | yes |  |

### `niivue.v1` — 14 entries (12 expert-visible, 2 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `sv3d_set_view_mode` | yes |  |
| `sv3d_set_slice` | yes |  |
| `viewer_window_level` | yes |  |
| `viewer_zoom` | yes | shared |
| `viewer_pan` | yes | shared |
| `sv3d_set_render_angles` | yes |  |
| `sv3d_set_clip_plane` | yes |  |
| `sv3d_set_mask_visibility` | yes |  |
| `sv3d_set_mask_opacity` | yes |  |
| `sv3d_measure` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_mask_geometry` | yes |  |

### `occt.v1` — 26 entries (18 expert-visible, 8 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `sv3d_set_view` | yes | shared |
| `sv3d_orbit` | yes | shared |
| `viewer_zoom` | yes | shared |
| `sv3d_fit_view` | yes | shared |
| `sv3d_set_part_visibility` | yes |  |
| `sv3d_set_model_opacity` | yes | shared |
| `sv3d_set_render_mode` | yes |  |
| `sv3d_set_cutaway` | yes | shared |
| `sv3d_set_reference_view` | no |  |
| `sv3d_set_reference_zoom` | no |  |
| `sv3d_set_reference_pan` | no |  |
| `sv3d_reset_reference_view` | no |  |
| `sv3d_select_part` | yes |  |
| `sv3d_pick_entity` | yes |  |
| `sv3d_clear_selection` | yes |  |
| `viewer_pick_point` | no | shared |
| `sv3d_measure` | yes | shared |
| `sv3d_measure_distance` | yes | shared |
| `sv3d_measure_distance_exact` | yes |  |
| `sv3d_measure_angle` | yes | shared |
| `sv3d_measure_bounds` | yes |  |
| `sv3d_fit_plane` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_render` | no | shared |

### `gltf.v1` — 17 entries (13 expert-visible, 4 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `viewer_pick_point` | no | shared |
| `viewer_pick_vertex` | no | shared |
| `sv3d_orbit` | yes | shared |
| `sv3d_set_view` | yes | shared |
| `sv3d_set_orthographic_view` | yes | shared |
| `viewer_zoom` | yes | shared |
| `viewer_pan` | yes | shared |
| `sv3d_toggle_layer` | yes | shared |
| `sv3d_set_cutaway` | yes | shared |
| `sv3d_measure_distance` | yes | shared |
| `sv3d_fit_plane` | yes | shared |
| `sv3d_measure_area` | yes | shared |
| `sv3d_measure_angle` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_annotate` | yes | shared |

### `bim_coordination.v1` — 22 entries (16 expert-visible, 6 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `sv3d_open_multi_scene` | no |  |
| `sv3d_set_view` | yes | shared |
| `sv3d_orbit` | yes | shared |
| `viewer_zoom` | yes | shared |
| `viewer_pan` | yes | shared |
| `sv3d_fit_view` | yes | shared |
| `sv3d_set_model_visibility` | yes |  |
| `sv3d_set_model_opacity` | yes | shared |
| `sv3d_register_overlay` | yes |  |
| `sv3d_toggle_ground` | yes |  |
| `sv3d_toggle_envelopes` | yes |  |
| `viewer_pick_point` | no | shared |
| `viewer_pick_vertex` | no | shared |
| `sv3d_measure_distance` | yes | shared |
| `sv3d_fit_plane` | yes | shared |
| `sv3d_measure_area` | yes | shared |
| `sv3d_measure_angle` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_annotate` | yes | shared |
| `sv3d_set_camera` | no | shared |

### `architecture_inspection.v1` — 21 entries (14 expert-visible, 7 internal)

| tool | expert-visible | shared |
|---|---|---|
| `viewer_get_state` | no | shared |
| `viewer_reset` | no | shared |
| `sv3d_open_scene` | no |  |
| `sv3d_orbit` | yes | shared |
| `sv3d_set_view` | yes | shared |
| `sv3d_set_orthographic_view` | yes | shared |
| `viewer_zoom` | yes | shared |
| `viewer_pan` | yes | shared |
| `sv3d_toggle_layer` | yes | shared |
| `sv3d_set_cutaway` | yes | shared |
| `sv3d_set_deformation_amplification` | yes |  |
| `viewer_pick_point` | no | shared |
| `viewer_pick_vertex` | no | shared |
| `sv3d_measure` | yes | shared |
| `sv3d_fit_plane` | yes | shared |
| `sv3d_measure_area` | yes | shared |
| `sv3d_measure_angle` | yes | shared |
| `sv3d_clear_measurements` | yes | shared |
| `sv3d_render` | no | shared |
| `sv3d_annotate` | yes | shared |
| `sv3d_save_operation_trace` | no |  |

## Domain and viewer are different axes

"Domain" does not select a toolset -- **viewer** does. The viewer hangs off the
task's assets (`bundle_ingest.py` reads `asset.viewer`), not off its domain, so one
domain can span several families and one family serves several domains.

Over this repository's own `QA-Gen/tasks/**/*.jsonl`, the pairing actually recorded is:

| domain | viewer | tasks |
|---|---|---:|
| medical | `niivue` | 2563 |
| chemistry | `molstar` | 834 |
| biology | `molstar` | 641 |
| multi | `molstar` | 131 |
| architecture | `gltf` 11, `cityjson` 9, `pointcloud` 6, `svg` 2 | 28 |
| embodied | `genesis_view` | 4 |

Two things follow, and both are reasons not to write a domain-level tool list:

- `architecture` alone spreads across four viewers, so one list for that domain
  would be wrong for at least three of them.
- `cityjson`, `pointcloud`, `svg` and `genesis_view` have **no registry among the
  seven** above -- not every viewer has an MCP surface at all.

**`mechanical_cad` does not appear in these corpora**, so no viewer is asserted
for it here even though `occt.v1` exists and is presumably what serves it. The
authoritative answer is a join of the platform database's task `domain` against
its `payload_json.viewer`.

## Relationship to ccmcp

The agent-side server is a **different surface, not a mirror of this one.** Of
the 59 names above, 9 are reachable under the same name with a compatible call
shape, 26 have no ccmcp equivalent at all, and the rest are reachable only via a
renamed, split, folded or partial route. Three same-name traps silently change a
measured number (`sv3d_open_scene`, `sv3d_set_render_mode`, `sv3d_measure_angle`).

Row-by-row: [`PLATFORM_PARITY.md`](PLATFORM_PARITY.md). What each set of "the MCP
tools" counts: [`MCP_TOOLS_IN_USE.md`](MCP_TOOLS_IN_USE.md).

## Regenerating this file

The tables are generated from the registries, not hand-maintained:

```bash
python3 tools/offline_task_manager/expert_app/mcp/render_library_doc.py
```

A change to a registry that this file does not reflect is a bug in this file.
