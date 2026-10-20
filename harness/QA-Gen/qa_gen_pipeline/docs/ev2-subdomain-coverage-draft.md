# Draft: EV2 Subdomain Coverage

## Status and Boundary

This document records both the coverage analysis and the accepted local seed
checkpoint. It is not a knowledge approval, viewer attestation, provider-route
approval, plan approval, or release authorization. The accepted seed artifacts
remain local-only and are not production eligible.

Configured package root environment:
`$QAGEN_EV2_SOURCE_ROOT`

| Item | Observed value | Consequence |
| --- | --- | --- |
| Canonical source inventory | `sha256:063b0347fe3a72704ccb23aab81ccc0414e9d12bed3b3cba9eec901ee2f73e1a` over 141 regular files | Pinned by checked-in SourceSpec `sha256:908e570a73ba61aaa76538a78653e029fc216ea046038abc73843d4c5c6134b6` and bound to the accepted resolution receipt. |
| Package manifest | package ID and version resolve to `1.0.3` | Accepted as resolved version `1.0.3` for this exact inventory and SourceSpec. |
| Version note | README resolves to `1.0.2` | Remains a known label conflict; `user:sue` accepted it on 2026-08-26 in receipt `sha256:18a1d8826c0fc12a2abd3ad09f26a5ceed140253599982cc50497645bf7682cf`. |
| Taxonomy alias | `chemistry.chirality` -> `chemistry.stereochemistry_chirality`, lock `sha256:b8a180f785052e19e44e73a9df699c47970fd669a1c426bea1480a69a709c0bf` | Applied by the normalizer and recorded in inventory, catalog, cards, and lesson provenance. No other semantic near-match is aliased. |
| Frozen seed index | `sha256:e07bc402f604dd8fdcbf2000f07a46d8fe574551f3f423079b1c63f6260b8a57` | Eligible as a local seed registry input; still `local_only: true` and `production_eligible: false`. |
| Review distribution | 3 `verified`, 2 `minor_fix`, 6 `factual_error`, 4 `unscorable` | Only the three `verified` records may become positive lessons. Every other record is anti-pattern-only, even where a corrected reference exists. |
| Viewer and assets | Package ships a local browser viewer and raw asset files | This is not evidence of anonymous scenes, controlled sessions, least-privilege operation scopes, or a production viewer capability profile. |

The case inventory below deliberately omits original question text, answer
values, asset filenames/paths/hashes, reviewer correspondence, and source task
IDs. `B-*`, `C-*`, and `M-*` are local audit aliases only. They must not be
treated as seed-card IDs or as a durable mapping contract.

## Registry Naming Constraint

The seed normalizer applies only the checked-in, digest-bound chirality alias.
All other records retain `domain.raw_subdomain_slug`; semantic similarity alone
cannot make them satisfy a target leaf seed query.

| EV2 source taxonomy | Current normalized seed leaf | Target disposition |
| --- | --- | --- |
| biology / docking-pose-plausibility | `biology.docking_pose_plausibility` | Exact target leaf. |
| biology / molecular-collision-check | `biology.molecular_collision_check` | Exact target leaf. |
| chemistry / binding-pose | `chemistry.binding_pose` | No target leaf; closest biology docking leaf is cross-domain and not an executable match. |
| chemistry / chirality | `chemistry.stereochemistry_chirality` | Normalized by the locked alias contract; eligible for the target seed query. |
| chemistry / conformation | `chemistry.conformation` | Needs an approved taxonomy mapping to `chemistry.conformation_analysis`. |
| chemistry / crystal-packing | `chemistry.crystal_packing` | Needs an approved taxonomy mapping to `chemistry.crystallography_packing`. |
| chemistry / optimization-vector | `chemistry.optimization_vector` | At most a nearest-neighbor mapping to `chemistry.steric_accessibility`; not an executable match. |
| medical / anatomical-region | `medical.anatomical_region` | Exact target leaf. |
| medical / lesion-3d-extent | `medical.lesion_3d_extent` | Nearest target is `medical.lesion_localization`, but anatomical extent is not a lesion case. |
| medical / mri-arbitrary-plane | `medical.mri_arbitrary_plane` | No target leaf; this is primarily a viewer/reslice capability requirement. |

Any additional mapping must be a reviewed, digest-bound normalization
contract. It must not be introduced by prompt wording, an author worker, or a
loose subdomain string comparison.

## Per-Case Coverage

| Alias | Source type and target disposition | Review verdict | Scientific intent and visual skill | Scene/viewer requirement | Deidentified lesson use |
| --- | --- | --- | --- | --- | --- |
| B-DP-01 | biology docking -> exact docking leaf | `minor_fix` | Assess a ligand pose from local protein-ligand interactions; spatial interaction evidence. | Molecular complex; controlled inspect, isolate, rotate, and visible interaction evidence. | Anti-pattern only: do not imply a multi-pose comparison when the supplied scene has one pose; bind claims to visible atom-level evidence. |
| B-DP-02 | biology docking -> exact docking leaf | `unscorable` | Rank competing poses by geometric plausibility; multi-view pose comparison. | A complete, uniquely named pose ensemble plus controlled comparison views; shipped scene lacks the needed pose files. | Anti-pattern only: never ask for a ranking over absent poses or rely on non-existent labels/properties. |
| B-MC-03 | biology collision -> exact collision leaf | `verified` | Decide whether a ligand-receptor contact is a severe non-bonded collision; local distance/occlusion reasoning. | Molecular complex; controlled inspect, isolate, rotate, and a traceable local measurement or annotation. | Positive lesson in the eligible frozen seed registry: a negative collision judgment still needs visible evidence, not a metadata shortcut. |
| B-MC-04 | biology collision -> exact collision leaf | `verified` | Judge whether a buried polar group lacks compensating interactions; local chemical-environment reasoning. | Molecular complex; controlled inspect, isolate, rotate, and visible partner evidence. | Positive lesson in the eligible frozen seed registry: distinguish a buried but satisfied polar group from an unsatisfied one. |
| C-BP-05 | chemistry binding pose -> no target leaf | `unscorable` | Identify a covalent protein-ligand linkage; atom-level bond interpretation. | Molecular complex with inspect/rotate and a controlled atom/bond representation. | Anti-pattern only: chemical bond type and atom naming must be verified from the scene before scoring. |
| C-CHI-07 | chemistry chirality -> locked alias to stereochemistry | `factual_error` | Assign stereochemical configuration from displayed 3D geometry. | Small-molecule scene; controlled inspect and rotate with a defined stereochemical convention. | Anti-pattern lesson in the aliased registry: validate configuration against the exact 3D coordinates; do not substitute an enantiomer by name. |
| C-CHI-08 | chemistry chirality -> locked alias to stereochemistry | `verified` | Count and locate stereocenters from fused-ring 3D geometry. | Small-molecule scene; controlled inspect and rotate. | Positive lesson in the aliased eligible registry: count true stereocenters from geometry and explicitly exclude non-stereogenic sites. |
| C-CONF-01 | chemistry conformation -> semantic near-match to conformation analysis | `minor_fix` | Classify relative substituent arrangement around a rotatable bond; dihedral reasoning. | Conformer scene; controlled inspect, rotate, and native dihedral measurement with a trace. | Anti-pattern only: specify the atom quartet/substituent rule and use a calibrated dihedral tolerance. |
| C-CONF-02 | chemistry conformation -> semantic near-match to conformation analysis; weak near-match to reactive conformation | `factual_error` | Evaluate an intramolecular hydrogen bond and a conformational flip. | Conformer scene; controlled inspect, rotate, and likely native geometry measurement. | Anti-pattern only: donor/acceptor identities and the counterfactual flip must be chemically and visually grounded. |
| C-CP-03 | chemistry crystal-packing -> semantic near-match to crystallography | `factual_error` | Identify a periodic packing motif and participating groups. | Periodic crystal scene with symmetry expansion and solvent present in the controlled scene; current task policy denies the needed expansion route. | Anti-pattern only: a packing question is invalid unless the exact periodic expansion and all required co-crystallized components are visible and traceable. |
| C-CP-04 | chemistry crystal-packing -> semantic near-match to crystallography | `factual_error` | Classify packing dimensionality and dominant intermolecular interaction. | Same periodic/symmetry capability as C-CP-03, with a traceable expansion state. | Anti-pattern only: do not infer a periodic network from an asymmetric unit; the dominant interaction must be checked rather than assumed. |
| C-OV-06 | chemistry optimization vector -> nearest steric-accessibility leaf only | `unscorable` | Choose a substitution direction that reaches an available subpocket without clash; annotated spatial design reasoning. | Molecular complex plus a controlled, task-bound vector/overlay layer; the shipped scene has no candidate-vector annotation. | Anti-pattern only: choices named in the prompt must be visibly instantiated, and the claimed target pocket must be unoccupied. |
| M-AR-01 | medical anatomical-region -> exact anatomical leaf | `factual_error` | Locate a regional boundary and select the clearest view plane. | De-identified volume plus approved target annotation and controlled MPR/oblique reslice evidence. | Anti-pattern only: target labels, axis/plane convention, and boundary index must be attested rather than inferred from an unlabeled view. |
| M-LE-03 | medical lesion-3d-extent -> only a near-match to lesion localization | `factual_error` | Measure 3D target extent and axial span; volumetric extent reasoning. | De-identified volume/mask, calibrated extent measurement, and controlled axial/MPR trace. | Anti-pattern only: state target scope before measurement and do not claim an adjacent structure that is absent from the field of view. |
| M-MRI-02 | medical arbitrary plane -> no target leaf; viewer capability gap | `unscorable` | Choose an oblique plane that exposes a named anatomy; camera/slice-pose reasoning. | De-identified volume, controlled oblique reslice, World RAS coordinates, unit-normal validation, and an explicitly named target. | Anti-pattern only: a plane task needs a specified anatomy, coordinate system, normalization/sign convention, and angular tolerance. |

## Leaf-Level Unlock Assessment

`conditional` and `blocked` leaf configs cannot become compile eligible merely
because new data appears: the compiler adds `leaf_status_not_compile_eligible`
unless the leaf is deliberately updated to `sample_compile_eligible`, and the
current profiles also list those leaves as blocked. The matrix below therefore
answers the stronger question: could EV2 alone meet all current release gates?
The answer is no for every leaf.

| Target leaf | Current status | EV2 coverage | Registered seed contribution | Still required; cannot be supplied by EV2 alone |
| --- | --- | --- | --- | --- |
| `biology.docking_pose_plausibility` | fixture-only sample compile | B-DP-01/02 exact, neither verified | Anti-pattern lessons only. | A verified positive seed, a non-fixture anonymous asset binding, controlled viewer/session evidence, approved knowledge, profile and production approval. |
| `biology.molecular_collision_check` | conditional | B-MC-03/04 exact and verified | Two verified records and positive lessons are available through the frozen eligible seed index. | Anonymous asset binding, controlled viewer/session capability evidence, approved knowledge, explicit config/profile enablement, and production approval. |
| `chemistry.stereochemistry_chirality` | fixture-only sample compile | C-CHI-08 verified and C-CHI-07 anti-only; both normalize through the locked alias | One positive and one anti-pattern lesson are available through the frozen eligible seed index. | Non-fixture anonymous asset binding, controlled viewer/session evidence, approved knowledge, explicit workspace profile enablement, and production approval. |
| `chemistry.conformation_analysis` | conditional | C-CONF-01/02 are both anti-only and source leaf differs | Anti-pattern lessons on dihedral calibration and chemical validity. | A verified positive seed, mapping contract, anonymous asset, controlled native-dihedral evidence, approved knowledge, and explicit enablement. |
| `chemistry.steric_accessibility` | conditional | C-OV-06 is unscorable and only a nearest match | Anti-pattern on visible annotations and pocket occupancy. | A verified positive seed, reviewed semantic mapping, annotated anonymous scene, controlled overlay/collision capability, approved knowledge, and explicit enablement. |
| `chemistry.coordination_chemistry` | conditional | No EV2 case | None. | Verified seed, anonymous scene, controlled inspect/rotate capability, approved knowledge, and explicit enablement. |
| `chemistry.crystallography_packing` | blocked | C-CP-03/04 are anti-only and source leaf differs | Anti-patterns on periodic-scene premises. | Verified seed, reviewed mapping, anonymous periodic scene, controlled symmetry-expansion/periodic-context evidence, approved knowledge, resolving the policy answerability block, and explicit enablement. |
| `chemistry.reactive_conformation` | blocked | No exact EV2 case; C-CONF-02 is only a non-reactive near-match and anti-only | At most a generic anti-pattern about ungrounded flip claims. | A viewer-only visual-answerability protocol, verified reactive-conformation seed, anonymous scene, controlled dihedral evidence, approved knowledge, and explicit enablement. |
| `medical.anatomical_region` | blocked | M-AR-01 exact but factual-error | Anti-pattern on target/plane provenance. | Medical release gate, a repaired then re-expert-verified medical seed, approved knowledge, production review approval, de-identified anonymous scene, controlled viewer/session evidence, and explicit enablement. |
| `medical.lesion_localization` | blocked | M-LE-03 is factual-error and uses a non-lesion target | Anti-pattern on extent/target scope only. | Medical release gate, repaired verified lesion seed, de-identified lesion scene/mask, controlled calibrated viewer evidence, approved knowledge, and explicit enablement. |
| `medical.imaging_feature_assessment` | blocked | No exact case; M-MRI-02 is an unscorable viewer-plane case | Anti-pattern on coordinate and reslice specification. | Medical release gate, repaired verified imaging-feature seed, de-identified annotated scene, controlled reslice/measurement capability, approved knowledge, and explicit enablement. |

## Decision Inputs for the Main Control Plane

1. Treat the source acceptance as narrowly bound to inventory
   `sha256:063b0347fe3a72704ccb23aab81ccc0414e9d12bed3b3cba9eec901ee2f73e1a`,
   its SourceSpec digest, and resolution receipt
   `sha256:18a1d8826c0fc12a2abd3ad09f26a5ceed140253599982cc50497645bf7682cf`.
   It is not a knowledge, viewer, plan, or release approval.
2. Retain the strict split: only B-MC-03, B-MC-04, and C-CHI-08 are positive
   lessons. The remaining twelve records remain de-identified anti-pattern
   lessons.
3. The normalizer may use only the locked chirality alias. Define and approve
   a new digest-bound mapping before using any other chemistry or medical
   near-match.
4. Treat the packaged viewer as a useful reference implementation, not a
   controlled viewer integration. A real session service must attest anonymous
   scenes, the exact allowed operations, evidence traces, and denied raw-asset
   access before any leaf can be release eligible.

The checked-in `ev2_v103_seed_ready` profile pins the frozen seed index but no
knowledge index. A real CLI build against `.qagen/pilot-ev2-v1.0.3` produced
config `sha256:3681178a34421a32eab4163fa3cd817ecfa4de70deec2f797e5321df2163c885`
with status `blocked`, zero eligible leaves, and explicit missing-knowledge and
profile-blocked reasons. This is the intended boundary for the current stage.
