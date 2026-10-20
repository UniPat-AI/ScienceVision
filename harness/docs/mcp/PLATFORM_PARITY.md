# Platform MCP <-> ccmcp parity

<!-- VENDORED COPY. Generated in UniPat-AI/ScienceVision by `python -m
     sci3d_agent.mcp.parity.render`; that repository holds the source data
     (`sci3d_agent/mcp/parity/`). Do not hand-edit the table below -- regenerate
     it there and copy it across. -->

The expert annotation platform (this repository) and the agent-side MCP server
(*ccmcp*, in `UniPat-AI/ScienceVision`) are two layers by design: the platform is
a browser-side registry driving NiiVue, Mol\*, OCCT and glTF through an expert's
clicks, ccmcp is a Python stdio server an agent calls. This table says, for every
tool this repository's registries expose, what an agent calls instead and where
the two disagree -- so a question authored on one layer can be judged fairly on
the other.

**Read this before:**

- authoring a task from an expert trajectory (a platform call may have no agent equivalent, or a differently-named one);
- comparing an agent's answer against an expert's (a divergence below can change the number, not just the route);
- citing a leaderboard row (an ABSENT row on the question's viewer family means the agent could not do what the expert did).

## Provenance

The table describes two revisions, and both matter:

| side | repository | ref | commit |
|---|---|---|---|
| platform (this repo) | `UniPat-AI/BabyVision-v2` | `origin/prod` | `3ccee5b7096f5be56b963ee83882f9c3bd624adc` (2026-09-09) |
| ccmcp | `UniPat-AI/ScienceVision` | `worktree-parity-prod-and-family-gating` | `738135026d5985ea4bdd8d9862d7ce3a71b411e3` (2026-09-09) |

- registries read from: `tools/offline_task_manager/expert_app/mcp/registries` (7 files, 59 distinct tool names)
- vendored snapshot + entry data: `sci3d_agent/mcp/parity/` **in ScienceVision**, refreshed by `python scripts/mcp/refresh_platform_snapshot.py --ref origin/prod`

The registries are vendored rather than read live: the two layers are separate
repositories, and a table that only holds while a sibling checkout is present is
not one anyone can rely on. The platform ref is the authority on what this table
describes -- a parity claim against anything but the deployed branch describes a
surface no expert is annotating on, which is what the previous snapshot
(`feat/platform-feedback-20260819`, 4 of 7 registries) did.

### This repository's own ccmcp is behind the ccmcp column

Measured 2026-09-09: `sci3d_agent/mcp/tools` on **both** `origin/main` and
`origin/prod` here advertises **42** tools, and is missing 10 of the 25 the table's
ccmcp column names:

`sv3d_measure_distance`, `sv3d_measure_angle`, `sv3d_measure_dihedral`,
`sv3d_measure_extent`, `sv3d_clash`, `sv3d_select`, `sv3d_isolate`,
`sv3d_set_representation`, `sv3d_get_unit_cell`, `sv_inspect_nifti_mask_pair`

That is the entire measurement and selection surface. It exists in ScienceVision
and has never been promoted here. Consequences to hold in mind while reading:

- Every **SAME_NAME** and **PARTIAL** row that resolves to a measurement tool is
  describing a capability this repository's copy of ccmcp does not have.
- A `ccmcp` route built from this repository's tools would be strictly weaker than
  the one the table describes, so the ABSENT count below (26 of 59) is a
  **lower** bound for it.
- Promoting those 10 tools is the prerequisite for hosting the parity module here
  rather than vendoring its output.

## Status vocabulary

| status | count | meaning |
| --- | --- | --- |
| **SAME_NAME** | 9 | same tool name, and the platform's call shape is accepted |
| **RENAMED** | 6 | one ccmcp tool, same job, different name |
| **SPLIT** | 1 | one platform tool becomes several ccmcp tools |
| **FOLDED** | 3 | several platform tools become one ccmcp tool |
| **PARTIAL** | 14 | reachable only by combining tools, or with something named lost |
| **ABSENT** | 26 | no ccmcp equivalent, by design |

59 platform tool names, 9 of them reachable under the same name.

## At a glance

| platform tool | in registries | vis | status | ccmcp |
| --- | --- | --- | --- | --- |
| `sv3d_annotate` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1 | public | **ABSENT** | -- |
| `sv3d_clash` | molstar.v2 | public | **SAME_NAME** | `sv3d_clash` |
| `sv3d_clear_measurements` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>molstar.v1<br>molstar.v2<br>niivue.v1<br>occt.v1 | public | **ABSENT** | -- |
| `sv3d_clear_selection` | occt.v1 | public | **ABSENT** | -- |
| `sv3d_fit_plane` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>occt.v1 | public | **ABSENT** | -- |
| `sv3d_fit_view` | bim_coordination.v1<br>occt.v1 | public | **FOLDED** | `sv3d_camera_set_view` |
| `sv3d_focus_residue` | molstar.v2 | public | **PARTIAL** | `sv3d_select`<br>`sv3d_camera_look_at` |
| `sv3d_get_unit_cell` | molstar.v2 | public | **SAME_NAME** | `sv3d_get_unit_cell` |
| `sv3d_mask_geometry` | niivue.v1 | public | **PARTIAL** | `sv3d_measure_extent`<br>`sv3d_get_state`<br>`sv_inspect_nifti_mask_pair` |
| `sv3d_measure` | architecture_inspection.v1<br>molstar.v1<br>molstar.v2<br>niivue.v1<br>occt.v1 | public | **SPLIT** | `sv3d_measure_distance`<br>`sv3d_measure_angle`<br>`sv3d_measure_dihedral` |
| `sv3d_measure_angle` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>occt.v1 | public | **SAME_NAME** | `sv3d_measure_angle` |
| `sv3d_measure_area` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1 | public | **ABSENT** | -- |
| `sv3d_measure_atoms` | molstar.v2 | public | **PARTIAL** | `sv3d_measure_distance` |
| `sv3d_measure_bounds` | occt.v1 | public | **PARTIAL** | `sv3d_measure_extent` |
| `sv3d_measure_distance` | bim_coordination.v1<br>gltf.v1<br>occt.v1 | public | **SAME_NAME** | `sv3d_measure_distance` |
| `sv3d_measure_distance_exact` | occt.v1 | public | **ABSENT** | -- |
| `sv3d_open_multi_scene` | bim_coordination.v1 | internal | **ABSENT** | -- |
| `sv3d_open_scene` | architecture_inspection.v1 | internal | **SAME_NAME** | `sv3d_open_scene` |
| `sv3d_orbit` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>molstar.v1<br>molstar.v2<br>occt.v1 | public | **RENAMED** | `sv3d_camera_orbit` |
| `sv3d_pick_entity` | occt.v1 | public | **ABSENT** | -- |
| `sv3d_register_overlay` | bim_coordination.v1 | public | **ABSENT** | -- |
| `sv3d_render` | architecture_inspection.v1<br>occt.v1 | internal | **SAME_NAME** | `sv3d_render` |
| `sv3d_reset_reference_view` | occt.v1 | internal | **ABSENT** | -- |
| `sv3d_save_operation_trace` | architecture_inspection.v1 | internal | **ABSENT** | -- |
| `sv3d_select_part` | occt.v1 | public | **PARTIAL** | `sv3d_select` |
| `sv3d_set_atom_labels` | molstar.v2 | public | **ABSENT** | -- |
| `sv3d_set_camera` | bim_coordination.v1<br>molstar.v1<br>molstar.v2 | internal | **ABSENT** | -- |
| `sv3d_set_chain_visibility` | molstar.v2 | public | **PARTIAL** | `sv3d_select`<br>`sv3d_isolate` |
| `sv3d_set_clip_plane` | niivue.v1 | public | **SAME_NAME** | `sv3d_set_clip_plane` |
| `sv3d_set_component_visibility` | molstar.v2 | public | **PARTIAL** | `sv3d_select`<br>`sv3d_isolate` |
| `sv3d_set_cutaway` | architecture_inspection.v1<br>gltf.v1<br>occt.v1 | public | **RENAMED** | `sv3d_set_clip_plane` |
| `sv3d_set_deformation_amplification` | architecture_inspection.v1 | public | **ABSENT** | -- |
| `sv3d_set_mask_opacity` | niivue.v1 | public | **FOLDED** | `sv3d_toggle_mask`<br>`sv3d_set_opacity` |
| `sv3d_set_mask_visibility` | niivue.v1 | public | **FOLDED** | `sv3d_toggle_mask` |
| `sv3d_set_model_opacity` | bim_coordination.v1<br>occt.v1 | public | **ABSENT** | -- |
| `sv3d_set_model_visibility` | bim_coordination.v1 | public | **ABSENT** | -- |
| `sv3d_set_orthographic_view` | architecture_inspection.v1<br>gltf.v1 | public | **PARTIAL** | `sv3d_camera_set_view` |
| `sv3d_set_part_visibility` | occt.v1 | public | **PARTIAL** | `sv3d_select`<br>`sv3d_isolate` |
| `sv3d_set_reference_pan` | occt.v1 | internal | **ABSENT** | -- |
| `sv3d_set_reference_view` | occt.v1 | internal | **ABSENT** | -- |
| `sv3d_set_reference_zoom` | occt.v1 | internal | **ABSENT** | -- |
| `sv3d_set_render_angles` | niivue.v1 | public | **PARTIAL** | `sv3d_camera_orbit`<br>`sv3d_camera_set_view` |
| `sv3d_set_render_mode` | occt.v1 | public | **PARTIAL** | `sv3d_set_render_mode`<br>`sv3d_set_representation` |
| `sv3d_set_representation` | molstar.v2 | public | **SAME_NAME** | `sv3d_set_representation` |
| `sv3d_set_slice` | niivue.v1 | public | **SAME_NAME** | `sv3d_set_slice` |
| `sv3d_set_symmetry` | molstar.v1<br>molstar.v2 | public | **ABSENT** | -- |
| `sv3d_set_view` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>occt.v1 | public | **RENAMED** | `sv3d_camera_set_view` |
| `sv3d_set_view_mode` | niivue.v1 | public | **RENAMED** | `sv3d_set_render_mode` |
| `sv3d_switch_structure` | molstar.v1<br>molstar.v2 | public | **ABSENT** | -- |
| `sv3d_toggle_envelopes` | bim_coordination.v1 | public | **ABSENT** | -- |
| `sv3d_toggle_ground` | bim_coordination.v1 | public | **ABSENT** | -- |
| `sv3d_toggle_layer` | architecture_inspection.v1<br>gltf.v1 | public | **ABSENT** | -- |
| `viewer_get_state` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>molstar.v1<br>molstar.v2<br>niivue.v1<br>occt.v1 | internal | **PARTIAL** | `sv3d_get_state` |
| `viewer_pan` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>molstar.v2<br>niivue.v1 | public | **RENAMED** | `sv3d_camera_pan` |
| `viewer_pick_point` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>occt.v1 | internal | **ABSENT** | -- |
| `viewer_pick_vertex` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1 | internal | **ABSENT** | -- |
| `viewer_reset` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>molstar.v1<br>molstar.v2<br>niivue.v1<br>occt.v1 | internal | **PARTIAL** | `sv3d_camera_set_view` |
| `viewer_window_level` | niivue.v1 | public | **PARTIAL** | `sv3d_set_slice` |
| `viewer_zoom` | architecture_inspection.v1<br>bim_coordination.v1<br>gltf.v1<br>molstar.v1<br>molstar.v2<br>niivue.v1<br>occt.v1 | public | **RENAMED** | `sv3d_camera_dolly` |

## Per-tool detail

### `sv3d_annotate` -- ABSENT

Place a point annotation on visible geometry.

**Why absent.** ccmcp is read-only with respect to the scene's presentation: it can change camera, opacity, clipping and representation, but it cannot leave a mark. Annotation is part of the platform's evidence contract rather than its measurement surface -- it is how an expert says 'here' to a reviewer -- and an agent's equivalent is prose in its answer.

**Related:** `viewer_pick_point`, `sv3d_save_operation_trace`

### `sv3d_clash` -- SAME_NAME

Report the worst van-der-Waals overlap between a non-polymer and the polymer.

**ccmcp:** `sv3d_clash`

**Note.** Added by commit 043e714d so an agent can answer the same clash-geometry question an expert can. Both sides read the same two atom sets, exclude the same struct_conn pairs, and report nonpolymer_atom_id / polymer_atom_id as field-for-field identical atom identities.

| platform argument | ccmcp |
| --- | --- |
| `(none)` | `session_id` -- both read the loaded structure |

**Divergences.**

- threshold_gap_A (platform) and threshold_margin_A (ccmcp) are the same number under different keys -- a silent mismatch for anything comparing the two envelopes key by key.
- The platform throws when there is no comparable pair; ccmcp returns status=missing_nonpolymer|missing_polymer|no_unconnected_pair, because an error envelope reads to an agent as a failed call.
- The platform annotates the winning pair in the viewer as a side effect (addDistance); ccmcp only reports it.
- The platform refuses unless the symmetry preset is 'model'. ccmcp always scans the asymmetric unit because it cannot be in any other state -- the two agree on the number for that reason, not through a shared check.
- Platform-only keys: threshold_relation, element_pair, expert_summary. ccmcp-only keys: status, robust, threshold_overlap_A, model_index, nonpolymer_atom_count, polymer_atom_count.

### `sv3d_clear_measurements` -- ABSENT

Remove every measurement annotation from the viewer.

**Why absent.** ccmcp's measure tools are pure computations that store nothing, so there is no annotation list to clear. An expert trajectory containing this call needs no replay step -- it is a no-op, not a gap in capability.

### `sv3d_clear_selection` -- ABSENT

Clear all mechanical part and mesh-point selections.

**Why absent.** There is no selection set to clear. ccmcp's sv3d_select takes a selection string and refuses an empty one, and sv3d_isolate(false) restores visibility rather than forgetting a selection. Nothing accumulates picked points on the ccmcp side, so nothing needs clearing -- the absence is a consequence of having no picking.

**Related:** `sv3d_pick_entity`, `sv3d_select_part`

### `sv3d_fit_plane` -- ABSENT

Fit a least-squares plane through three or more picked points and report each point's departure from it.

**Why absent.** No ccmcp tool fits geometry to points. sv3d_set_clip_plane takes a plane as input (origin and normal) but never derives one, and no inspector returns a residual. An agent can only reproduce this by extracting coordinates itself and doing the least squares in Bash -- which is exactly the substitution that turns an MCP-grounding question into a text-parsing question, and it will not appear in the tool trace as a 3D operation.

**Related:** `viewer_pick_point`, `sv3d_measure_area`

### `sv3d_fit_view` -- FOLDED

Frame the camera on everything, on what is visible, or on one model.

**ccmcp:** `sv3d_camera_set_view`

| platform argument | ccmcp |
| --- | --- |
| `scope='all'` | `fit_bbox=true` -- the only scope ccmcp can express: it fits the whole session bbox |
| `scope='visible'` | *not expressible* -- ccmcp has no per-model visibility to be visible-relative to -- see the sv3d_set_model_visibility entry |
| `scope='candidate'` | *not expressible* -- names one model out of several; a ccmcp session holds one asset |
| `index` | *not expressible* -- which model to frame under scope='candidate'; nothing to index |

**Divergences.**

- The platform's schema uses a root oneOf so that index is required with scope='candidate' and forbidden otherwise. ccmcp's fit_bbox is a boolean on an otherwise unrelated tool, so the constraint has nowhere to live.

### `sv3d_focus_residue` -- PARTIAL

Focus one residue by chain and sequence id: draw it, its environment and their non-covalent interactions, then aim the camera at it.

**ccmcp:** `sv3d_select`, `sv3d_camera_look_at`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `chain_id + seq_id` | `sv3d_select` | `selection` -- ccmcp takes one selection string, so the pair has to be formatted into it rather than passed as fields |
| `icode` | `sv3d_select` | *not expressible* -- insertion codes have no place in ccmcp's selection grammar; a residue that needs one is not addressable |
| `(the camera move)` | `sv3d_camera_look_at` | `target` -- aiming is a separate ccmcp call, not a side effect |

**Divergences.**

- The environment and the non-covalent interactions are the point of this tool and ccmcp draws neither. An agent gets the residue selected and the camera pointed at it, with no contacts rendered -- so a question asking which interactions a residue participates in is answered from the file, not from the view.
- 'Replaces any previous focus' is platform state ccmcp does not keep. Two focus calls in a row leave the platform showing one residue and ccmcp showing whatever its last selection was.

### `sv3d_get_unit_cell` -- SAME_NAME

Read the crystallographic cell parameters and space group.

**ccmcp:** `sv3d_get_unit_cell`

**Note.** Added by commit 043e714d. Both sides read the same two CIF tag families (_cell.length_a mmCIF and _cell_length_a core CIF).

| platform argument | ccmcp |
| --- | --- |
| `(none)` | `session_id` -- both read the loaded structure |

**Divergences.**

- spacegroup_hm (platform) vs space_group_hm (ccmcp) -- the same value under a different spelling.
- cell_lengths_A / cell_angles_deg are 3-element arrays on the platform; ccmcp returns a_A/b_A/c_A and alpha_deg/beta_deg/gamma_deg as scalars.
- unit_cell_volume_A3 vs volume_A3, and the numbers can differ: the platform prefers the file's own _cell.volume and derives only when it is absent, while ccmcp always uses gemmi's derived volume.
- The platform throws when the file carries no cell; ccmcp returns status=missing_cell with null parameters.

### `sv3d_mask_geometry` -- PARTIAL

Return the mask's voxel-space extent, volume and best axial slice, and optionally jump there.

**ccmcp:** `sv3d_measure_extent`, `sv3d_get_state`, `sv_inspect_nifti_mask_pair`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `(none)` | `sv3d_measure_extent` | `session_id / mask_id` -- the platform reads the loaded mask; ccmcp addresses one label |
| `goto='best_axial'` | -- | *not expressible* -- nothing in ccmcp jumps to the heaviest axial slice. sv3d_set_crosshair(mask_id=...) is the nearest move and lands elsewhere -- it centres on the mask CENTROID, a different slice for any asymmetric mask |
| `goto='none'` | `sv3d_measure_extent` | `(the default)` -- ccmcp never moves the view as a side effect of measuring |

**Divergences.**

- No single ccmcp tool returns the bundle. extent_mm / bbox_world / centroid_world / axial_slice_span come from sv3d_measure_extent; voxel_count, centroid_world and best_slices come from sv3d_get_state's masks_info; bbox_voxel and centroid_voxel need the offline sv_inspect_nifti_mask_pair.
- Units differ: the platform reports voxel indices, ccmcp reports world millimetres. Converting needs the affine, which sv3d_get_state does not expose.
- Eight platform fields have no ccmcp equivalent anywhere: mask_volume_mm3, extent_vox, bbox_volume_vox, bbox_fill_ratio, max_axial_area_vox, best_slice_tied_count, longest_axes and longest_to_shortest_extent_ratio.
- best_slice_tied_count matters for question authoring: it is how the platform says 'the best axial slice is ambiguous'. ccmcp's best_slices reports one winner with no tie signal, so an agent cannot tell a unique answer from an arbitrary one.

### `sv3d_measure` -- SPLIT

Measure a distance, angle or dihedral between picked points.

**ccmcp:** `sv3d_measure_distance`, `sv3d_measure_angle`, `sv3d_measure_dihedral`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `kind` | -- | `(the tool name)` -- distance\|angle\|dihedral (Mol*) and length\|angle (NiiVue) become three separate ccmcp tools; NiiVue's 'length' is ccmcp's 'distance' |
| `atoms` | `sv3d_measure_distance` | `a / b` -- Mol*; a ccmcp endpoint is either a 1-based atom_serial or a literal [x, y, z] world point, so an atom pick and a raw coordinate go in the same slot |
| `atoms` | `sv3d_measure_angle` | `a / b / c` -- the vertex is b |
| `atoms` | `sv3d_measure_dihedral` | `a / b / c / d` -- the torsion is about the b-c bond |
| `points_ras_mm` | `sv3d_measure_distance` | `a / b` -- NiiVue kind='length'; the list of [x, y, z] becomes positional endpoints |
| `points_ras_mm` | `sv3d_measure_angle` | `a / b / c` -- NiiVue kind='angle' |
| `commit` | -- | *not expressible* -- the platform's two-phase protocol: call once to arm the picker, again with commit=true once the human has clicked. ccmcp computes in one call, so there is no phase to commit |
| `value` | -- | *not expressible* -- the platform echoes the display string it wrote into the viewer; ccmcp returns the number plus its unit |
| `plane` | -- | *not expressible* -- NiiVue can restrict a measurement to one MPR plane; ccmcp measures in 3D and has no plane-restricted form |

**Divergences.**

- The platform tool leaves an annotation in the viewer; ccmcp's are pure computations that store nothing. That is why sv3d_clear_measurements has no ccmcp counterpart.
- dihedral exists only in the Mol* registries and NiiVue offers length|angle; ccmcp offers all three regardless of session kind.
- ccmcp reports a unit ('angstrom' for molecules, 'world' otherwise); the platform's value is a pre-formatted display string.

### `sv3d_measure_angle` -- SAME_NAME

Angle at the vertex between the rays to two other points, in 3D and projected onto the horizontal plane.

**ccmcp:** `sv3d_measure_angle`

| platform argument | ccmcp |
| --- | --- |
| `vertex` | `b` -- the MIDDLE argument -- ccmcp takes the vertex positionally |
| `first` | `a` -- the first ray endpoint |
| `second` | `c` -- the second ray endpoint |
| `(none)` | `session_id` -- ccmcp addresses a session explicitly |

**Divergences.**

- ARGUMENT ORDER IS A TRAP. The platform names the vertex; ccmcp infers it from position (a, b, c -> the angle at b). A replay that maps the three arguments in the order they are written -- first->a, second->b, vertex->c -- measures the angle at the wrong point and returns a well-formed number with no error. This is the failure mode a name-only parity check cannot see.
- The platform returns TWO angles: the 3D angle and its projection onto the horizontal plane. ccmcp returns only the 3D angle. For an architectural question about a plan-view bearing, the projected angle is the answer and the agent cannot produce it with this tool.
- ccmcp additionally accepts atom serials in a, b, c; the platform's arguments are world points only. Harmless, but it means the two schemas are not interchangeable in the other direction either.

### `sv3d_measure_area` -- ABSENT

Area enclosed by three or more picked points: true fit-plane area, the XY/XZ/YZ projections, the perimeter, and the worst departure from the fit plane.

**Why absent.** ccmcp measures distances, angles, dihedrals and extents -- no area, on any surface. The platform's readout is also richer than a single number: the projected areas and the planarity residual are what let an expert say whether treating the region as planar was legitimate. None of that is expressible, so an area question cannot be scored on an agent route without conceding that the agent computed it from raw coordinates.

**Related:** `sv3d_fit_plane`, `viewer_pick_point`

### `sv3d_measure_atoms` -- PARTIAL

Distance between two atoms named by chain, residue number and atom name.

**ccmcp:** `sv3d_measure_distance`

**Related:** `sv3d_measure`

| platform argument | ccmcp |
| --- | --- |
| `chain_a + seq_id_a + atom_id_a` | `a` -- ccmcp's a is an atom serial or a coordinate triple -- never a chain/residue/atom name, so the agent must resolve the identity itself before it can measure |
| `chain_b + seq_id_b + atom_id_b` | `b` -- as above |
| `comp_id_a / comp_id_b` | *not expressible* -- the residue name is a redundant check on the platform side and has no ccmcp slot, so the check is lost |
| `icode_a / icode_b` | *not expressible* -- insertion codes are not addressable |
| `(none)` | `session_id` -- ccmcp addresses a session explicitly |

**Divergences.**

- THE IDENTIFIER IS THE DIVERGENCE, not the arithmetic. The platform is handed a chemical identity and resolves it; ccmcp is handed a serial or a coordinate. So the agent performs the resolution step -- usually by grepping the file -- and any error in it lands in the answer as a confidently measured wrong distance.
- comp_id lets the platform verify that chain A residue 291 really is a CYS before measuring. Nothing on the ccmcp side can catch a mis-resolved residue, because it never learns what residue was intended.

### `sv3d_measure_bounds` -- PARTIAL

Measure one tessellated part's axis-aligned bounding box in model units.

**ccmcp:** `sv3d_measure_extent`

| platform argument | ccmcp |
| --- | --- |
| `part_index` | *not expressible* -- ccmcp has no notion of an indexed CAD part. Its selection vocabulary is a molecular selection string (chain/residue/atom) and its volumetric handle is a mask label id, so 'part 7 of this assembly' is not addressable. sv3d_measure_extent takes an optional mask_id instead, so an agent can only bound the whole scene or a labelled volume region. |
| `(none)` | `session_id` -- both read the already-loaded geometry |

**Divergences.**

- Scope differs, not units: the platform bounds ONE part, ccmcp bounds the whole session (or one mask label). On a single-part asset the two agree; on an assembly the agent's number is the assembly envelope, which is a different quantity with the same name and dimensions.
- 'Model units' is whatever the STEP file declares. ccmcp reports in the loaded mesh's own units with no unit field, so a mm-vs-m mismatch surfaces as a 1000x discrepancy rather than as an error.

### `sv3d_measure_distance` -- SAME_NAME

Straight-line 3D distance between two picked points.

**ccmcp:** `sv3d_measure_distance`

**Related:** `sv3d_measure_distance_exact`, `viewer_pick_point`

| platform argument | ccmcp |
| --- | --- |
| `start` | `a` -- world point; ccmcp also accepts an atom serial |
| `end` | `b` -- world point; ccmcp also accepts an atom serial |
| `(none)` | `session_id` -- ccmcp addresses a session explicitly |

**Divergences.**

- UNITS ARE NAMED ON ONE SIDE ONLY. glTF reports metres and Rhino millimetres together; occt reports model units; ccmcp reports a bare number in whatever units the loaded mesh happens to use, with no unit field. A mm-vs-m mismatch is a factor of 1000 that surfaces as a wrong answer rather than an error.
- The platform's exact-kernel sibling, sv3d_measure_distance_exact, has no ccmcp form at all. So on a mechanical asset this tool is the *only* distance available to an agent, and it is the tessellated one -- see that entry for when the difference matters.
- Both sides measure between points supplied by the caller, so the arithmetic agrees. What differs is where the points came from: the expert clicked them on the model, the agent picked them out of the file.

### `sv3d_measure_distance_exact` -- ABSENT

Exact B-Rep minimum distance between two selected solids, via the pinned OpenCascade kernel.

**Why absent.** ccmcp has no B-Rep kernel. It loads geometry through trimesh as a triangulation, so the closest it can offer is a distance between two tessellations -- sv3d_measure_distance over points the agent chose. That is an approximation whose error is the tessellation deviation, and a clearance question with a tolerance below that deviation cannot be answered correctly by construction, however capable the model is.

**Related:** `sv3d_measure_distance`, `sv3d_select_part`

### `sv3d_open_multi_scene` -- ABSENT

Open several models at once as one coordination scene.

**Why absent.** The root of the other four BIM absences, and internal on the platform too -- the expert app calls it during task setup rather than exposing it. ccmcp's sv3d_open_scene takes one qid or one asset_path. Comparing models under ccmcp means one session each and no shared coordinate frame between them.

**Related:** `sv3d_register_overlay`, `sv3d_set_model_opacity`, `sv3d_set_model_visibility`

### `sv3d_open_scene` -- SAME_NAME

Report the already-opened architecture inspection scene.

**ccmcp:** `sv3d_open_scene`

**Related:** `viewer_get_state`

| platform argument | ccmcp |
| --- | --- |
| `(none)` | `(all optional)` -- qid, asset_path, mask_path, asset_kind and ttl_seconds are every one optional, which is why the platform's empty call validates |

**Divergences.**

- OPPOSITE MEANING UNDER A SHARED NAME. The platform's tool is a read -- it describes the scene the expert is already looking at. ccmcp's is a constructor: it opens a session and returns a new session_id.
- The empty call shape is ACCEPTED rather than rejected, because all of ccmcp's arguments are optional. So a replayed trajectory gets a successful envelope for an operation that did something else. Read sv3d_get_state, not sv3d_open_scene, when the intent is 'what is on screen' -- that is the ccmcp tool with this tool's semantics.
- Compounding it: ccmcp's own sv3d_open_scene already returns a success envelope over an empty scene when the molecular parser is missing (a nominal +/-10 A box). A caller cannot distinguish 'reported the existing scene', 'opened a real one' and 'opened an empty one' from the envelope shape alone.

### `sv3d_orbit` -- RENAMED

Rotate the camera around the structure.

**ccmcp:** `sv3d_camera_orbit`

| platform argument | ccmcp |
| --- | --- |
| `yaw_deg` | `d_azimuth_deg` -- molstar.v1 / bim; both relative |
| `pitch_deg` | `d_elevation_deg` -- molstar.v1 / bim; the platform clamps to +-89, ccmcp does not |
| `azimuth_deg` | *not expressible* -- molstar.v2 only, and ABSOLUTE. ccmcp orbits by a delta, so an absolute heading is reachable only through sv3d_camera_set_view's canonical views |
| `elevation_deg` | *not expressible* -- molstar.v2 only, absolute; as above |
| `(none)` | `d_roll_deg` -- ccmcp-only; no platform registry rolls the camera |

**Divergences.**

- The platform's two Mol* registries disagree with each other: molstar.v1 orbits by a relative pitch/yaw delta, molstar.v2 sets an absolute azimuth/elevation. ccmcp is relative, under its own argument names, so it matches neither side's names and only v1's meaning.
- ccmcp adds d_roll_deg, which no platform registry exposes; a trajectory using it cannot be replayed in the browser.

### `sv3d_pick_entity` -- ABSENT

Raycast the mechanical mesh at normalized viewport coordinates and append the hit point to the selection.

**Why absent.** Raycasting presupposes a rendered viewport with a camera an expert is looking through. ccmcp renders offscreen on demand and returns a PNG; there is no persistent viewport, no screen-to-world transform exposed, and no way to turn a pixel coordinate into a point on the model.

**Related:** `viewer_pick_point`

### `sv3d_register_overlay` -- ABSENT

Align a second model onto the reference with a rigid transform.

**Why absent.** ccmcp never applies a transform to loaded geometry: an asset is drawn in its own coordinates and every camera tool moves the camera instead. Registration would mean a second asset and a mutable per-asset transform, neither of which the frozen Session has.

**Related:** `sv3d_open_multi_scene`

### `sv3d_render` -- SAME_NAME

Render the current inspection or CAD view and return its state.

**ccmcp:** `sv3d_render`

| platform argument | ccmcp |
| --- | --- |
| `(none)` | `session_id` -- ccmcp addresses a session explicitly |
| `(none)` | `width / height / background / return` -- ccmcp-only: the platform renders at whatever size the pane is |

**Divergences.**

- The platform returns viewer state alongside the image -- camera, visibility, active measurements -- because the render IS the viewer. ccmcp returns a PNG (base64 or a URL) and nothing else; state comes from a separate sv3d_get_state call.
- Resolution is a ccmcp argument and a platform accident. Two renders of the same scene are not pixel-comparable across layers, which matters for any question scored on what is legible in the image.

### `sv3d_reset_reference_view` -- ABSENT

Reset the active mechanical reference image's zoom and pan.

**Why absent.** There is no reference-image pane to reset. See sv3d_set_reference_view for why the whole 2D-drawing cluster is absent.

**Related:** `sv3d_set_reference_view`

### `sv3d_save_operation_trace` -- ABSENT

Return the recorded inspection operation trace without downloading a file.

**Why absent.** ccmcp records nothing. The platform's trace is part of its evidence contract -- trajectory, keyframes, submission, replay -- which exists so an expert's reasoning can be reviewed and replayed. An agent's equivalent record lives outside the MCP layer entirely, in harbor's trajectory.json, and is written by the harness rather than requested by the agent. So this tool has no counterpart and should not acquire one: an agent that could author its own evidence trail could author a favourable one.

### `sv3d_select_part` -- PARTIAL

Select or deselect one tessellated CAD part by stable part index; selecting marks its bounds.

**ccmcp:** `sv3d_select`

| platform argument | ccmcp |
| --- | --- |
| `part_index` | *not expressible* -- ccmcp has no notion of an indexed CAD part. Its selection vocabulary is a molecular selection string (chain/residue/atom) and its volumetric handle is a mask label id, so 'part 7 of this assembly' is not addressable. |
| `selected` | *not expressible* -- sv3d_select replaces the selection rather than toggling one member of it, so 'deselect part 7' has no expression |
| `(none)` | `selection` -- a selection string, not an index |

**Divergences.**

- The platform's part index is *stable* -- it is the contract that lets a question say 'part 12' and mean the same solid every time. ccmcp has no stable per-part identifier at all, so a question phrased against an index is not portable to the agent side even by hand.
- Selecting on the platform marks the part's bounds as a side effect, which is how the expert then reads a dimension. ccmcp's selection has no geometric readout attached.

### `sv3d_set_atom_labels` -- ABSENT

Show or hide screen-space serial-number labels on chosen atoms.

**Why absent.** ccmcp draws no text. There is no label layer, and no ccmcp tool takes a list of atom ids in order to decorate them. This one is worth noting even though it sounds cosmetic: labels are how an expert reads a serial number OFF the picture, which is the step that lets the rest of the session be about geometry. An agent gets serial numbers from the file instead, so for it the picture never has to be legible at all.

### `sv3d_set_camera` -- ABSENT

Set the semantic camera (azimuth, elevation, zoom) in one call.

**Why absent.** The platform's internal replay primitive: it restores a recorded camera triple verbatim. ccmcp's camera is a position/target/up/fov_deg frustum with no semantic triple to restore, and every public ccmcp camera tool is relative or canonical. Replaying a platform camera into ccmcp would need exactly the absolute-angle support the sv3d_orbit entry records as missing.

### `sv3d_set_chain_visibility` -- PARTIAL

Show one polymer chain with its ligands, waters, ions and covalently attached glycans, hiding the rest -- or show all chains again.

**ccmcp:** `sv3d_select`, `sv3d_isolate`

**Related:** `sv3d_set_component_visibility`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `only='<chain>'` | `sv3d_select` | `selection` -- format the chain into a selection string, then isolate it |
| `only='<chain>'` | `sv3d_isolate` | `isolated=true` -- the second half of the pair; select alone does not hide anything |
| `only='all'` | `sv3d_isolate` | `isolated=false` -- restoring maps cleanly |

**Divergences.**

- The platform's chain scope is CHEMICALLY DEFINED: showing chain A also shows the ligands, waters, ions and glycans attached to it. ccmcp's isolate hides everything outside the literal selection string, so the agent's chain A is the polymer alone unless it enumerates the attachments itself -- and covalently attached glycans are exactly the thing it would omit.
- So a question about what a chain carries can be right on the platform and wrong through ccmcp for a reason that is not visible in either tool's arguments.

### `sv3d_set_clip_plane` -- SAME_NAME

Cut the volume away along a plane.

**ccmcp:** `sv3d_set_clip_plane`

**Note.** Aligned by commit 488b3298, which taught the ccmcp schema the platform's {depth, azimuth_deg, elevation_deg} shape without retiring its own {origin, normal, cap} form.

| platform argument | ccmcp |
| --- | --- |
| `enabled` | `enabled` -- required on both sides |
| `depth` | `depth` -- 0..1 in NiiVue's volume-normalized texture space, stored verbatim |
| `azimuth_deg` | `azimuth_deg` -- stored verbatim |
| `elevation_deg` | `elevation_deg` -- stored verbatim |
| `(none)` | `origin / normal / cap` -- ccmcp's own world-space plane; the two shapes cannot be mixed in one call and are not converted into each other |

**Divergences.**

- Neither parameterization is converted into the other, deliberately: a conversion would have to reproduce three NiiVue conventions exactly (its texture space, its volScale normalization, and the sign of its plane equation) and nothing in ccmcp renders or measures with the clip plane. The reply's parameterization field says which triple describes the plane in effect.
- enabled=false discards the angle triple on both sides, but ccmcp additionally keeps its world plane, so a later bare enable is a world re-enable rather than the platform's depth-0.5 default.
- Angles supplied alongside enabled=false are refused by ccmcp; the platform silently ignores them.
- ccmcp enforces depth in [0, 1]. The platform's schema declares the same range but its handler only checks finiteness, so depth 5 reaches NiiVue, quietly turns the clip off, and reports success.

### `sv3d_set_component_visibility` -- PARTIAL

Show or hide water / ligand / polymer / ion independently.

**ccmcp:** `sv3d_select`, `sv3d_isolate`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `ligand` | `sv3d_select` | `selection='ligand'` -- the one class ccmcp names (also spelled 'hetero'), and only as the thing shown -- never as the thing hidden |
| `polymer` | `sv3d_select` | *not expressible* -- ccmcp's selection grammar has no polymer class; the closest meaning, 'not hetero', is not expressible in it |
| `water` | `sv3d_select` | *not expressible* -- no water class |
| `ion` | `sv3d_select` | *not expressible* -- no ion class |
| `(none)` | `sv3d_isolate` | `isolated` -- ccmcp's mechanism: show only the current selection |

**Divergences.**

- ccmcp can only show-one-selection (sv3d_isolate), not toggle per-class visibility. 'Hide the water, keep everything else' is not expressible.
- The platform's schema requires at least one class (minProperties: 1) and fails closed on a class the structure does not contain. ccmcp accepts any free-text expression and reports n_atoms_matched=null when it cannot resolve it, which is a report rather than a refusal.
- A ccmcp-internal inconsistency, recorded because it is reachable from here: an expression sv3d_select cannot resolve is treated as matching nothing by sv3d_select, but sv3d_measure_extent's own private grammar falls back to all atoms for the same expression. The same stored selection therefore means two things.
- sv3d_get_state reports no per-component visibility, so an agent cannot read back what it hid.

### `sv3d_set_cutaway` -- RENAMED

Cut away along a chosen axis to expose internal geometry.

**ccmcp:** `sv3d_set_clip_plane`

| platform argument | ccmcp |
| --- | --- |
| `axis='x'\|'y'\|'z'` | `normal` -- ccmcp takes a normal vector, so an axis becomes a unit vector; azimuth_deg/elevation_deg are an alternative spelling |
| `value_m` | `origin / depth` -- the platform's cut position is a scalar along the axis in metres; ccmcp positions the plane by a point on it, or by depth |
| `(none)` | `enabled` -- ccmcp requires the plane be switched on explicitly |
| `(none)` | `cap` -- ccmcp-only: whether to cap the cut surface |
| `(none)` | `session_id` -- ccmcp addresses a session explicitly |

**Divergences.**

- value_m is metres by name. ccmcp's origin and depth are in the mesh's own units, so the conversion is the caller's problem and silently wrong if the asset is not in metres.
- ccmcp can cap the cut face; the platform's cutaway does not mention capping. A capped section reads as a solid surface and an uncapped one as a hollow shell, which changes what a section view looks like even when the plane is identical.

### `sv3d_set_deformation_amplification` -- ABSENT

Set the display-only displacement scale for registered deformed solids.

**Why absent.** ccmcp has no deformed-solid registration and no display amplification. The platform keeps amplification strictly display-only -- the native sv_arch_compute_deformation_metrics returns physical deflection and twist separately, and amplification never enters those numbers. An agent asked about a deflection therefore reads the physical value from the native tool and never sees the exaggerated picture the expert used to find it. That asymmetry is deliberate and safe; it is only a problem if a question's wording describes what the amplified view looks like.

### `sv3d_set_mask_opacity` -- FOLDED

Set how opaque the mask overlay is.

**ccmcp:** `sv3d_toggle_mask`, `sv3d_set_opacity`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `opacity` | `sv3d_toggle_mask` | `alpha` -- the platform floors this at 0.12 so an expert cannot lose the overlay by dragging to zero; ccmcp allows 0.0 |
| `opacity` | `sv3d_set_opacity` | `layer='mask' + alpha` -- the same number, through a second ccmcp tool |

**Divergences.**

- The platform's 0.12 floor is a UI guard rail with no equivalent in ccmcp. An agent can set the overlay fully transparent, which the expert who authored the question could not have done.
- A ccmcp-internal ambiguity, recorded because a replay lands on it: mask alpha exists twice in Session -- MaskState.alpha (sv3d_toggle_mask) and Opacity.mask (sv3d_set_opacity) -- and the two do not track each other. Which one the renderer honours is not something this table should decide.

### `sv3d_set_mask_visibility` -- FOLDED

Show or hide the mask overlay.

**ccmcp:** `sv3d_toggle_mask`

| platform argument | ccmcp |
| --- | --- |
| `visible` | `visible` -- identical meaning |

### `sv3d_set_model_opacity` -- ABSENT

Fade one model in a multi-model coordination scene.

**Why absent.** Same root cause as sv3d_set_model_visibility. ccmcp's sv3d_set_opacity addresses layers of one asset (base / mask / clip_cap), not models of a scene, so it cannot carry a per-model opacity.

**Related:** `sv3d_open_multi_scene`

### `sv3d_set_model_visibility` -- ABSENT

Show or hide one model in a multi-model coordination scene.

**Why absent.** A ccmcp session is one asset, so there is no second model to hide. The nearest ccmcp mechanism, sv3d_isolate, hides parts of one structure by selection, not whole models -- a different operation under a superficially similar name, which is why this is ABSENT rather than PARTIAL.

**Related:** `sv3d_open_multi_scene`, `sv3d_fit_view`

### `sv3d_set_orthographic_view` -- PARTIAL

Switch to a true orthographic front, side or top elevation with parallel projection.

**ccmcp:** `sv3d_camera_set_view`

**Related:** `sv3d_set_view`

| platform argument | ccmcp |
| --- | --- |
| `view='front'` | `view='front'` -- the name matches |
| `view='top'` | `view='top'` -- the name matches |
| `view='side'` | `view='left'|'right'` -- the platform does not say which side; replaying it is a choice, and the wrong one mirrors the elevation |
| `(parallel projection)` | *not expressible* -- THE missing part: ccmcp's camera is perspective and offers no orthographic mode |

**Divergences.**

- Only the camera direction transfers; the projection does not. ccmcp frames with a perspective camera at distance = max(diag * 1.4, 1e-3), so parallel edges converge. The platform switches to parallel projection for exactly the reason this matters: an elevation is where lengths and angles are meant to be read off directly.
- Consequence for scoring: a length or angle an expert read off an orthographic elevation is not the length the agent measures in the same nominal view. The error grows with depth extent, so it is small on a flat facade and large on a deep massing.

### `sv3d_set_part_visibility` -- PARTIAL

Show or hide one CAD part by index, or restore all parts.

**ccmcp:** `sv3d_select`, `sv3d_isolate`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `index` | `sv3d_select` | *not expressible* -- ccmcp has no notion of an indexed CAD part. Its selection vocabulary is a molecular selection string (chain/residue/atom) and its volumetric handle is a mask label id, so 'part 7 of this assembly' is not addressable. |
| `visible=false` | `sv3d_isolate` | *not expressible* -- the pair can only isolate what IS selected; hiding one part while the rest stay visible is the inverse operation and has no shape |
| `all=true` | `sv3d_isolate` | `isolated=false` -- restoring everything is the one case that maps cleanly |

**Divergences.**

- select+isolate is show-one-hide-rest; the platform's tool is hide-one-keep-rest. Only the 'restore all' branch is faithful. An occlusion question that depends on hiding exactly one part in front cannot be replayed.

### `sv3d_set_reference_pan` -- ABSENT

Pan the active mechanical reference image by normalized offsets.

**Why absent.** No reference-image pane exists. See sv3d_set_reference_view.

**Related:** `sv3d_set_reference_view`

### `sv3d_set_reference_view` -- ABSENT

Show one indexed mechanical reference image beside the 3D model.

**Why absent.** This is the 2D engineering drawing, and it is the root of the whole reference cluster. A ccmcp session holds exactly one asset and renders exactly one scene; there is no second pane and no way to put a drawing in front of the agent alongside the model. So a mechanical question of the form 'does the solid agree with this drawing' is not merely harder for an agent -- half its evidence is unreachable. Such a question must be scoped out of an agent route, not scored on it.

### `sv3d_set_reference_zoom` -- ABSENT

Set the active mechanical reference image zoom as an absolute scale.

**Why absent.** No reference-image pane exists. See sv3d_set_reference_view.

**Related:** `sv3d_set_reference_view`

### `sv3d_set_render_angles` -- PARTIAL

Point the 3D render at an absolute azimuth and elevation.

**ccmcp:** `sv3d_camera_orbit`, `sv3d_camera_set_view`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `azimuth_deg` | `sv3d_camera_orbit` | `d_azimuth_deg` -- the platform's is ABSOLUTE (-180..180); ccmcp's is a delta, so reaching a named heading means knowing the current one |
| `elevation_deg` | `sv3d_camera_orbit` | `d_elevation_deg` -- as above (-89..89 on the platform) |
| `(both)` | `sv3d_camera_set_view` | `view` -- only the canonical headings -- front\|back\|left\|right\|top\|bottom\|iso\|reset -- are absolutely addressable |

**Divergences.**

- An arbitrary absolute angle pair is not settable on a live ccmcp session. The one place ccmcp takes absolute azimuth/elevation is the offline sv_render_mesh_thumbnail, which renders an image rather than moving a session camera.
- This is the same absolute-vs-relative gap as sv3d_orbit and sv3d_set_camera; the three entries share one root cause.

### `sv3d_set_render_mode` -- PARTIAL

Switch the CAD model between shaded and wireframe rendering.

**ccmcp:** `sv3d_set_render_mode`, `sv3d_set_representation`

| platform argument | ccmcp tool | ccmcp |
| --- | --- | --- |
| `mode='shaded'` | `sv3d_set_render_mode` | `mode='surface'` -- same picture, different word; 'shaded' is not in ccmcp's enum |
| `mode='wireframe'` | `sv3d_set_representation` | `representation='wireframe'` -- on ccmcp wireframe is a representation, not a render mode, so this value crosses to a different tool |
| `(none)` | `sv3d_set_render_mode` | `session_id` -- required, and the reason a replay cannot just retry the other tool on rejection: it has to already hold a session |
| `(none)` | `sv3d_set_representation` | `session_id` -- likewise |

**Divergences.**

- A FALSE FRIEND, and the most misleading row in this registry: both layers have a tool named sv3d_set_render_mode and their mode enums are DISJOINT. The platform's are shaded|wireframe; ccmcp's are surface|points|volume|mpr_axial|mpr_sagittal|mpr_coronal. Neither platform value is accepted under the shared name.
- The saving grace is that it fails loudly -- a replayed call is rejected by the enum rather than silently rendering something else. Any table that matched tools by name alone would have called this row aligned.

### `sv3d_set_representation` -- SAME_NAME

Set the draw style of the polymer, the ligand, or both.

**ccmcp:** `sv3d_set_representation`

**Note.** Aligned by commit 1b9a9fec, which taught the ccmcp schema the platform's {target, style} shape without retiring ccmcp's own {representation} form. target='all' with style='cartoon' expands to the polymer alone on both sides, because a ligand has no backbone to trace.

| platform argument | ccmcp |
| --- | --- |
| `target` | `target` -- polymer\|ligand\|all; identical enum |
| `style` | `style` -- cartoon\|ball-and-stick\|spacefill accepted verbatim; ccmcp's enum is a superset and also takes ball_and_stick, licorice, surface, lines, wireframe |
| `(none)` | `representation` -- ccmcp's own key, meaning the whole molecule; exactly one of representation\|style must be given |

**Divergences.**

- Re-applying the style already in effect: the platform throws a sentinel error so the human sees why the viewer did not move, ccmcp succeeds with changed=false. An error envelope would push an agent into a retry or out to Bash.
- ccmcp does not fail closed when the named component is absent from the loaded structure, and sv3d_get_state exposes no components[] list to check against first.

### `sv3d_set_slice` -- SAME_NAME

Move the crosshair to given sagittal / coronal / axial voxels.

**ccmcp:** `sv3d_set_slice`

**Note.** Aligned by commit 6e642463, which taught the ccmcp schema the platform's per-axis shape without retiring its own axis+index form. Out-of-range and non-integer indices are refused on both sides.

| platform argument | ccmcp |
| --- | --- |
| `sagittal` | `sagittal` -- voxel index, integer >= 0 |
| `coronal` | `coronal` -- voxel index, integer >= 0 |
| `axial` | `axial` -- voxel index, integer >= 0 |
| `(none)` | `axis + index|fraction` -- ccmcp's own form, which also selects the displayed plane; the two forms cannot be mixed in one call |
| `(none)` | `window / level` -- ccmcp folds viewer_window_level in here -- see that entry |

**Divergences.**

- The per-axis form deliberately does not change which plane is displayed; the platform's crosshair move and ccmcp's are the same, but ccmcp needs sv3d_set_render_mode to switch panes.
- The platform's schema declares minProperties: 1. ccmcp's declares only session_id as required and enforces the same rule in the handler instead, so a bare call is refused with an error naming both shapes -- same outcome, different layer.

### `sv3d_set_symmetry` -- ABSENT

Rebuild the scene as model / crystal contacts / unit cell / super cell.

**Why absent.** ccmcp never builds symmetry-expanded geometry: it holds the asymmetric unit and nothing else. That is load-bearing for sv3d_clash -- the platform must check the preset before scanning, ccmcp cannot be in any other state. Adding presets would mean adding that check too.

### `sv3d_set_view` -- RENAMED

Snap the camera to a named standard view.

**ccmcp:** `sv3d_camera_set_view`

| platform argument | ccmcp |
| --- | --- |
| `view='iso'` | `view='iso'` -- the one name both layers share |
| `view='plan'` | `view='top'` -- same view, architectural vs geometric naming |
| `view='side'` | `view='left'` -- the platform does not say WHICH side; ccmcp names left and right separately, so replaying 'side' is a choice |
| `view='front'` | `view='front'` -- identical |
| `(none)` | `view='back'|'bottom'|'right'|'reset'` -- ccmcp-only names; the platform's enum has no equivalent |

**Divergences.**

- The platform's 'side' is ambiguous between ccmcp's left and right. A trajectory replaying it has to pick one, and picking wrong mirrors the image -- which for a coordination question about left/right placement changes the answer.

### `sv3d_set_view_mode` -- RENAMED

Switch between the MPR panes and the 3D render.

**ccmcp:** `sv3d_set_render_mode`

| platform argument | ccmcp |
| --- | --- |
| `mode='axial'` | `mode='mpr_axial'` -- one displayed plane; also mpr_coronal / mpr_sagittal, which the platform's enum does not name |
| `mode='render'` | `mode='volume'` -- the 3D view |
| `mode='mpr'` | *not expressible* -- the platform's three-panes-at-once layout. Each ccmcp mpr_* mode names exactly one displayed plane, so 'all three at once' is not expressible |

**Divergences.**

- The platform's 'mpr' is a layout; ccmcp's mpr_* are a choice of plane. An agent reading three correlated planes uses sv3d_set_crosshair, which aligns all three and echoes each plane's index -- a different call with the same purpose.

### `sv3d_switch_structure` -- ABSENT

Switch which of several loaded structures is the active one.

**Why absent.** A ccmcp session is opened around exactly one asset -- sv3d_open_scene takes one qid or one asset_path -- so there is nothing to switch between. Multi-structure comparison would mean one session per structure.

### `sv3d_toggle_envelopes` -- ABSENT

Show or hide the clearance envelopes around visible candidates.

**Why absent.** ccmcp renders the asset and nothing else. A clearance envelope is derived geometry the platform generates and draws, so an expert judges clearance by seeing whether two envelopes intersect. The agent has no envelope, in the scene or as a number -- sv3d_clash reports van-der-Waals overlap for molecules and has no BIM equivalent. So a clearance question is answerable by inspection on the platform and not at all through ccmcp.

**Related:** `sv3d_open_multi_scene`

### `sv3d_toggle_ground` -- ABSENT

Show or hide the ground grid guide.

**Why absent.** A ground grid is a drawn reference plane, and ccmcp draws no scene furniture. Mostly this is cosmetic -- but not entirely: the grid is what makes a height or a level readable at a glance, and it also fixes which way is down. ccmcp frames the camera from the bounding box with no up-axis convention surfaced, so 'how far above ground' has no datum on the agent side.

### `sv3d_toggle_layer` -- ABSENT

Show or hide a named inspection or glTF scene-graph layer.

**Why absent.** ccmcp has no scene-graph layers. It loads one asset as one mesh (or one volume) and exposes visibility only as base/mask/clip_cap opacity layers -- a fixed triple, not the file's own named nodes. So 'hide the roof layer' has nothing to name. Note the platform's own description adds that layer ground truth is withheld in evaluation mode, so even there the layer names are not a free readout.

**Related:** `sv3d_set_part_visibility`

### `viewer_get_state` -- PARTIAL

Read the viewer's current camera and scene state.

**ccmcp:** `sv3d_get_state`

| platform argument | ccmcp |
| --- | --- |
| `(none)` | `session_id` -- the platform reads the one live viewer; ccmcp addresses a session by id |

**Divergences.**

- Different key sets on purpose -- each side reports the state its own layer actually holds. ccmcp returns camera position/target/up/fov_deg, mode, slice (including per-axis indices), clip_plane in both parameterizations, opacity, molecule, oblique_plane, masks_info and ttl_remaining_s.
- ccmcp reports no components[], no loaded-structure list and no symmetry preset, mirroring the three ABSENT entries for those capabilities.

### `viewer_pan` -- RENAMED

Slide the view sideways and up without rotating.

**ccmcp:** `sv3d_camera_pan`

| platform argument | ccmcp |
| --- | --- |
| `right_fraction` | `dx_frac` -- platform -1..1, ccmcp -2..2 |
| `up_fraction` | `dy_frac` -- platform -1..1, ccmcp -2..2 |
| `plane` | *not expressible* -- niivue.v1 pans within one named MPR plane; ccmcp pans the 3D camera in screen space and has no plane concept |

**Divergences.**

- niivue.v1 requires all three arguments and treats the fractions as an absolute offset within the named plane; ccmcp's are a relative screen-space delta applied to the camera.
- bim_coordination.v1 omits plane, so only that registry's pan maps cleanly onto ccmcp's.

### `viewer_pick_point` -- ABSENT

Return the world point under a normalized viewport position, or wait for the expert to click visible geometry.

**Why absent.** ccmcp has no viewport and no cursor. It renders offscreen on demand and returns a PNG; there is no persistent camera an expert is looking through, no screen-to-world transform exposed, and nobody to click. Every platform tool that consumes picked points therefore has no ccmcp counterpart -- and the agent has to choose its own measurement endpoints from the geometry, which is a different task from measuring between two points it was given. Read this entry first: the five that follow are consequences of it, not independent gaps. Note also the second call mode -- with no position given, the tool BLOCKS until a human clicks. An agent route has no such mode even in principle.

### `viewer_pick_vertex` -- ABSENT

Return the area-measurement vertex nearest a viewport position, to re-place it.

**Why absent.** Doubly absent: it needs picking, and it needs a persistent area measurement whose vertices can be edited. ccmcp keeps no measurement objects -- sv3d_measure_* compute and return, holding nothing to revise. The platform's editing loop (place, look, nudge a vertex, look again) is not a workflow an agent route can express.

**Related:** `viewer_pick_point`, `sv3d_measure_area`

### `viewer_reset` -- PARTIAL

Restore the initial camera, hierarchy and visibility.

**ccmcp:** `sv3d_camera_set_view`

| platform argument | ccmcp |
| --- | --- |
| `(none)` | `view='reset'` -- restores the camera only |

**Divergences.**

- The platform's reset also rebuilds the default structure hierarchy and discards representation / visibility / measurement changes. ccmcp's resets the camera and nothing else; the equivalent of a full reset is sv3d_close_scene followed by sv3d_open_scene.

### `viewer_window_level` -- PARTIAL

Set the intensity window width and level.

**ccmcp:** `sv3d_set_slice`

| platform argument | ccmcp |
| --- | --- |
| `window` | `window` -- both are a window WIDTH (high - low), not a (lo, hi) pair -- the argument shapes match exactly |
| `level` | `level` -- the window centre; identical meaning |

**Divergences.**

- The reason this is PARTIAL and not FOLDED: ccmcp's sv3d_set_slice refuses a call that names no slice, so {session_id, window, level} alone is an error. Windowing can only be changed while also moving the view, which the platform's tool never does. A caller that wants windowing alone has to re-state the slice it is already on.
- The platform requires both window and level together; ccmcp accepts either alone and keeps the other (width kept and re-centred, or centre kept and re-widened).
- ccmcp stores window internally as a (low, high) pair centred on level; the pair, not the width, is what sv3d_get_state reports.

### `viewer_zoom` -- RENAMED

Change how close the camera is.

**ccmcp:** `sv3d_camera_dolly`

| platform argument | ccmcp |
| --- | --- |
| `factor` | `factor` -- molstar.v1; relative, >1 moves closer -- the one exact match. Platform max 8, ccmcp range 0.05..20 |
| `zoom` | *not expressible* -- molstar.v2 / bim; an ABSOLUTE multiple of the fit distance (0.125..8). ccmcp has no absolute zoom |
| `scale` | *not expressible* -- niivue.v1; absolute scale 0..8. ccmcp has no absolute zoom |

**Divergences.**

- One platform tool name carries three argument names with two different meanings across four registries. ccmcp is relative only, so it reproduces molstar.v1 exactly and cannot express the absolute forms at all.
- An absolute platform zoom can only be approximated by sv3d_camera_set_view(fit_bbox=true) followed by a dolly factor.

## ccmcp tools with no platform counterpart

These are capabilities an agent has and the expert who authored the question did not. Live-session tools (category F) are listed one by one, because each is a real asymmetry.

| ccmcp tool | why the platform has no counterpart |
| --- | --- |
| `sv3d_close_scene` | Session lifecycle, like sv3d_open_scene. It is also ccmcp's only full reset, which is why viewer_reset maps onto ccmcp only partially. *(nearest: `viewer_reset`)* |
| `sv3d_render_canonical_views` | Six canonical views in one call, as one image sheet. A convenience that only makes sense when looking costs a tool call. *(nearest: `sv3d_set_view`)* |
| `sv3d_segment_2d` | Prompted segmentation on one slice, via the SAM service. No platform counterpart: the expert is the segmenter. |
| `sv3d_segment_3d_mesh` | Prompted mesh-part segmentation, as above. |
| `sv3d_segment_3d_volume` | Prompted volumetric segmentation, as above. |
| `sv3d_set_color_scheme` | Recolours a molecule (by element, chain, residue type...). molstar.v2 exposes representation but not colouring, so an agent can reach a view the expert could not. *(nearest: `sv3d_set_representation`)* |
| `sv3d_set_crosshair` | Moves all three MPR planes at once, addressed by world point or by mask_id. The platform's sv3d_set_slice takes voxel indices only, and its sv3d_mask_geometry(goto=...) jumps to the best axial slice rather than the centroid this centres on. *(nearest: `sv3d_mask_geometry`, `sv3d_set_slice`)* |
| `sv3d_set_oblique_plane` | An arbitrarily-oriented cut plane through a volume. NiiVue's registry exposes the three orthogonal planes and a clip plane, not an oblique reslice. *(nearest: `sv3d_set_clip_plane`)* |

Whole categories with no browser-side counterpart by construction:

- **A** -- Catalog and dataset lookup. The platform hands the expert one prepared asset; an agent has to find it first.
- **B** -- Viewer URL builders. These generate the links that open the platform's own pages, so a platform counterpart would be circular.
- **C** -- Offline file inspectors. They read an asset off disk without a viewer, which is the one thing a browser registry cannot do.
- **D** -- Offline renderers. They rasterize to a PNG the agent then looks at; the platform's equivalent is the expert's own eyes on the live canvas.
- **E** -- Pipeline and validation drivers. Build-time tooling for this repository, not viewer interaction.
