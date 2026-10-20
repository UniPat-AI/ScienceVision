# Chirality Knowledge Pilot

## Status and boundary

The `chemistry.stereochemistry_chirality` knowledge chain has been run through
local source sync, one bounded draft authorization, and deterministic draft
compilation. No `knowledge approve` command was run. There is no approved
snapshot or knowledge index in this workspace, and the fixture is used only as
a structural reference.

Knowledge draft workspace:
`.qagen/pilot-chirality-knowledge-draft`

The resolved v1.0.3 viewer artifacts are held separately in the local runtime
workspace `.qagen/private/ev2-pilot-workspace`. The checked-in workspace pilot
profile pins the following workspace-relative, private artifacts:

- SceneRegistry: `.qagen/private/viewer/scene-registries/registry_3e4fd505bd2583ca77976d67.json`
- SceneRegistry digest: `sha256:5751d9b50b03547ea5e84f56ba88e5ec55c6f3cd7cb85441d17eeab2927cf098`
- ViewerProfile: `.qagen/private/viewer-profiles/viewer-profile-b487b742c6b7-5574f13f2977-9107d8c88c9a56ad.json`
- ViewerProfile digest: `sha256:9107d8c88c9a56adfefdd1976cb6819b4540b07e7d331ea0ba409f4c89b08255`

Both artifacts are local-only and production-ineligible. Their real
render/rotate smoke and persisted receipt chain passed; this does not grant a
knowledge approval or release eligibility.

The workspace copies the already authorized EV2 seed registry rather than
resynchronizing or reinterpreting the package:

- frozen seed index: `.qagen/private/catalog/e07bc402f604dd8f.seed-index.json`
- seed index digest: `sha256:e07bc402f604dd8fdcbf2000f07a46d8fe574551f3f423079b1c63f6260b8a57`
- chirality alias lock: `sha256:b8a180f785052e19e44e73a9df699c47970fd669a1c426bea1480a69a709c0bf`
- chirality seed lesson digest: `sha256:32f15b66cf255e5a0d42f872e890a379c185a8751a5693b9021402b1ccc83819`

The seed lesson references are opaque and deidentified. They contain neither
original questions nor answers, asset identifiers, asset paths, or reviewer
conversation.

## Local source inputs

The draft uses two digest-locked local source documents:

- `configs/sources/knowledge/chemistry_stereochemistry_chirality.expert-draft.yaml`
  is an explicitly unapproved expert synthesis derived from the deidentified
  EV2 coverage analysis and the shortcut/evidence policy.
- `configs/sources/knowledge/chemistry_stereochemistry_chirality.visual-taxonomy.yaml`
  binds the approved V1-V6 visual taxonomy and the canonical question rubric,
  but does not claim production knowledge approval.

The existing
`knowledge/compiled/chemistry.stereochemistry_chirality.fixture.yaml` is bound
as `structure_only`, with `production_approval: false`. It is not promoted,
copied into an approved index, or represented as production evidence.

## Draft review artifacts

The current draft authorization expires at `2026-08-27T15:47:44+00:00` and is
already consumed, as required by the single-use contract.

- Snapshot draft:
  `.qagen/private/knowledge-drafts/chemistry__stereochemistry_chirality-028f8a2ef2bf72cb.draft.json`
- Draft digest:
  `sha256:028f8a2ef2bf72cb4d7d009c5ec6f74934f7d069854eeb080b378e4e9264d08a`
- Review packet:
  `.qagen/private/knowledge-drafts/chemistry__stereochemistry_chirality-028f8a2ef2bf72cb.review-packet.json`
- Review packet digest:
  `sha256:a96417f00c05292869306a489e7e2a730cf32928c0786b03e2c2371af4e8151e`

Both paths are relative to the pilot workspace. The packet binds the draft,
input, authorization, expert source, visual taxonomy source, fixture boundary,
seed lesson references, and pending human-review checklist. It includes the
scientific scope, valid and invalid patterns, allowed inference, forbidden
shortcuts, evidence rules, asset/viewer requirements, failure modes, and rubric
extensions without reproducing source task content or asset paths.

## Remaining gates

`config build` with
`configs/profiles/chirality_workspace_knowledge_pilot.yaml` and workspace
`.qagen/private/ev2-pilot-workspace` is blocked only by
`knowledge_human_approval_pending`. That is the expected knowledge human
approval gate: a draft cannot be inserted into a workspace registry.

Provider diagnosis is independently blocked until the external
`QAGEN_UNIV_BASE_URL`, `QAGEN_UNIV_ENDPOINT_APPROVAL_PATH`, and
`QAGEN_UNIV_API_KEY` inputs are present and the approval binds the exact HTTPS
endpoint and runtime-config digest; no network call is made while any input is
missing or invalid. Even after those two gates are resolved, the checked-in
profile remains a local provider pilot with release disabled. Production
requires a separately approved production profile, non-fixture asset and
viewer locks, and the normal plan/review/release approvals.
