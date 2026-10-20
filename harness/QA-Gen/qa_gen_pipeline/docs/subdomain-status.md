# Sub-domain status

This catalog records three separate decisions. `config_ready` means the leaf has
a valid, reviewable contract. `fixture_only` means the checked-in synthetic
chain can exercise compilation, never production generation. `eligible` is
reserved for a complete verified seed, asset, viewer, and approved knowledge
snapshot. Release is a separate gate and is blocked for every leaf below.

| Domain | Canonical leaf | Config | Generation | Release |
|---|---|---|---|---|
| biology | `bio_multi_structure_compare` | `config_ready` | `blocked` | `blocked` |
| biology | `docking_pose_plausibility` | `config_ready` | `fixture_only` | `blocked` |
| biology | `molecular_collision_check` | `config_ready` | `blocked` | `blocked` |
| chemistry | `stereochemistry_chirality` | `config_ready` | `fixture_only` | `blocked` |
| chemistry | `conformation_analysis` | `config_ready` | `blocked` | `blocked` |
| chemistry | `steric_accessibility` | `config_ready` | `blocked` | `blocked` |
| chemistry | `coordination_chemistry` | `config_ready` | `blocked` | `blocked` |
| chemistry | `crystallography_packing` | `config_ready` | `blocked` | `blocked` |
| chemistry | `reactive_conformation` | `config_ready` | `blocked` | `blocked` |
| medical | `lesion_localization` | `config_ready` | `blocked` | `blocked` |
| medical | `anatomical_region` | `config_ready` | `blocked` | `blocked` |
| medical | `imaging_feature_assessment` | `config_ready` | `blocked` | `blocked` |
| architecture | `concrete_void_localization` | `config_ready` | `blocked` | `blocked` |
| architecture | `bridge_damage_localization` | `config_ready` | `blocked` | `blocked` |
| architecture | `main_girder_deformation` | `config_ready` | `blocked` | `blocked` |
| architecture | `historic_facade_damage_repair` | `config_ready` | `blocked` | `blocked` |
| architecture | `historic_component_style_classification` | `config_ready` | `blocked` | `blocked` |
| architecture | `drawing_model_consistency` | `config_ready` | `blocked` | `blocked` |
| architecture | `utility_network_clash_detection` | `config_ready` | `blocked` | `blocked` |
| architecture | `cross_floor_alignment_validation` | `config_ready` | `blocked` | `blocked` |
| architecture | `stair_ramp_spatial_validity` | `config_ready` | `blocked` | `blocked` |
| architecture | `component_geometry_measurement` | `config_ready` | `blocked` | `blocked` |
| architecture | `section_elevation_reasoning` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `assembly_interference_clearance` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `assembly_bom_relation` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `manufacturability_draft_undercut` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `drawing_section_consistency` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `drawing_feature_to_3d_region_mapping` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `tool_accessibility` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `gdt_validation` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `motion_envelope` | `config_ready` | `blocked` | `blocked` |
| mechanical CAD | `pointcloud_assembly_qc` | `config_ready` | `blocked` | `blocked` |

The architecture and mechanical CAD profiles are catalog-only in effect: they
enable no leaves. CAD has zero approved local knowledge snapshots. Its revision
9 expert synthesis reference is `source_only`, so it provides provenance but
cannot satisfy a generation or release gate. Architecture is held to the same
rule. Legacy owner slugs are recorded in each canonical leaf's
`legacy_aliases`; they do not create duplicate catalog entries.

All six chemistry leaves point to their exact section in the revision 298
expert-knowledge master. These citations are also `source_only`. Only
`biology.docking_pose_plausibility` and
`chemistry.stereochemistry_chirality` have complete checked-in fixture chains,
and those chains remain local and non-production.

The local EV2 source checkpoint changed on 2026-08-26. SourceSpec pins inventory
`sha256:063b0347fe3a72704ccb23aab81ccc0414e9d12bed3b3cba9eec901ee2f73e1a`.
The manifest/package `1.0.3` versus README `1.0.2` conflict was accepted by
`user:sue` in receipt
`sha256:18a1d8826c0fc12a2abd3ad09f26a5ceed140253599982cc50497645bf7682cf`.
The resulting eligible, local-only seed index is
`sha256:e07bc402f604dd8fdcbf2000f07a46d8fe574551f3f423079b1c63f6260b8a57`.
Its chirality records normalize to `chemistry.stereochemistry_chirality`
through alias lock
`sha256:b8a180f785052e19e44e73a9df699c47970fd669a1c426bea1480a69a709c0bf`.

This checkpoint authorizes only that frozen seed input. The
`ev2_v103_seed_ready` profile enables no leaves because approved knowledge,
controlled viewer evidence, provider routes, and plan/release approval remain
absent. Missing inputs are represented by `required_inputs`; they are never
replaced with invented digests.
