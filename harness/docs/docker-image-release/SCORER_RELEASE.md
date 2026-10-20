# ScienceVision scorer image release

This is the release contract for the isolated formal scorer image. It is
separate from every model-facing agent image.

## Artifact identity

**Current candidate** — carries the free-text string-normalisation rules.

| Field | Value |
| --- | --- |
| Docker Hub repository | `suermars/sci3d-scorer` |
| Candidate tag | `server-20261014-h20` |
| Bundle version | `0.1.0` |
| Source revision | `b1e0135b821a05bedce115dcc994220875ed74f8` (the `feat/deterministic-free-text-scoring` merge into `eval-baseline`) |
| Scorer manifest SHA-256 | `f5159e428833b36e6a1dd32eef0bcff8ed44612b2c057c008defbd33042feb7a` |
| TaskSpec schema SHA-256 | `e01fab4bd9602c7395c7ce9f3dd99a60bd604558a620547ca9703a948f98da5b` |
| Comparator registry SHA-256 | `2368b069d9a6c7f931ccc0eb1ca34b2c35ee44a40ee7f55a4c34ba204df48ad4` |
| Source tree SHA-256 | `8d21553a04a2084ed8bfc0e8d2b920dcffe93fe3cd0d1b46a17bbf0da0d97dd0` |
| Local image ID | `sha256:73f671ff818a13a2b98efdc5e1a1a79f7d0bcb0e9f9c517cf68f4ca3c71dec14` |
| Hub digest | `sha256:6dffe8ec204e203f0c374b311446a3d3c858b92f2d896cb0b99a09071e74449b` |

Build provenance: staged at the branch worktree with
`scorer_bundle/build_context.py` (`git_dirty=false`, 7 bundled source files),
archived as `scorer-context-20261014.tar.gz` (39,052 bytes, sha256
`f532e8bc3b273ab0755d859573d5834c08e033e0af4a43adb03aad1fe9a1d1f8`), unpacked
at `/home/matianyi/sv-scorer-ctx-20261014/scorer-context-20261014` after
re-hashing the manifest and the scorer on H20, and built there with
`docker build --platform linux/amd64 --network=none` and the two build args the
bundle requires. The image was pulled back **by digest** after the push and
resolved to the same image ID, so the registry content — not just the local
cache — is what was verified.

Pre-push checks, all passing: manifest label equals the authoritative manifest;
`user 10001:10001`; the image holds 18 files, all inside the allow-list; a
forbidden-content probe over the whole filesystem returns zero hits for
`sci3d_agent`, `mcp`, `dataset`, `credential`, `codex` and `claude` (the single
`viewer` hit is Python's own `idlelib/stackviewer.py` in the base image).

Smoke: **16** `--network=none` cases, run against the image and compared with
the reviewed source rather than against hand-written expectations, so the claim
is that the image and the source that passed the gate agree. It covers the
nine earlier cases (membership hit/miss, the ±0.005 edge inside and outside, the
mod-360 seam on and off the boundary, a permuted label list passing and a wrong
one failing, a positional list rejecting a permutation, the `integer_equal` type
trap, the null round trip, and an empty string scoring partial) plus the rules
this candidate adds: `second_render` vs `second render` now scores 1.0 and
`the upper level…` vs `upper level…` now scores 1.0, while `C-terminus` vs
`N-terminus` and `X` vs `X-axis` still score 0.0. 16/16 agree.

**Superseded candidate** — stays published; do not delete, older task trees
pin it.

| Field | Value |
| --- | --- |
| Candidate tag | `server-20260928b-h20` (itself supersedes the earlier same-day `server-20260928-h20`) |
| Source revision | `d41b21bc7e8d69fd39ccb685e6339d743fda6f6d` |
| Scorer manifest SHA-256 | `d0c3ee1b66e50d5fa7cc15e4fd35e3d6dfb64a2c0a587395e915154446ede561` |
| Local image ID | `sha256:6e8879605f05d1bd8c90f2ed4219830552f0ec849c6272395d3176ef92b04b05` |
| Hub digest | `sha256:7e5471e1149ad8d84f15b4eab98d8d34d0070ee5fec15b470d2f001be6b9aea1` |

That image predates both free-text rules, so it still grades a ground truth of
`second_render` against an answer of `second render` as wrong — and it stays
that way for every task tree that names it. Task trees that should pick up the
new rules must be rebuilt against `server-20261014-h20`; `docker_image` in
`task.toml` is what selects the grader, and publishing a tag changes no tree
and no tree builder's default.

The manifest is generated during staging and verified by the CLI before it
emits a score. It is the receipt for the exact staged files. Do not use a
mutable `latest` tag for a benchmark release.

## Bundle boundary

`scorer_bundle/build_context.py` creates an allow-listed Docker context. The
image contains only the deterministic scorer, `TaskSpec` schema, and the
Harbor metadata-to-schema adapter needed for formal scoring.

It must never be layered into an agent image. It must not contain
`sci3d_agent`, an MCP client/server, model credentials, model clients, raw
datasets, viewers, task assets, or private masks. It runs as UID `10001`, has a
fixed CLI entrypoint, and must run with Docker networking disabled.

## Build candidate

From the eval-baseline release checkout:

```sh
./scorer_bundle/build_image.sh suermars/sci3d-scorer:server-20260928b-h20
```

The script stages the allow-list, writes `scorer_manifest.json`, builds
`linux/amd64`, and prints `SCI3D_SCORER_IMAGE_ID`, a local repo digest when
available, and the manifest SHA-256. It does not push.

Before release, run the targeted regression suite:

```sh
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/sci3d-pycache \
  /Users/sue/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/harbor_adapters/test_route_score_core.py \
  tests/harbor_adapters/test_aggregate_weighted.py \
  tests/harbor_adapters/test_mri_extent_scorer.py \
  tests/scorer_bundle/ \
  tests/sci3d/scorers/
```

The candidate recorded here passed `297` targeted tests.

The set is the three `harbor_adapters` files that collect without the `harbor`
package installed, plus the whole scorer suite and the scorer bundle. Coverage
is by directory rather than by file so that a new rule cannot ship with its
test outside the gate: the earlier `58`-test form named six files, and the
free-text branch added seven test modules that the named list never ran.
`tests/harbor_adapters/` also holds four modules that import `harbor` at
collection time (`test_sci3d_verifier`, `test_sci3d_agent`,
`test_containerized_route`, `test_rescore_rerun`); they are not part of this
gate and error out as `ModuleNotFoundError: No module named 'harbor'` on a
machine without it. That is expected here, not a regression.

## Formal scoring semantics

The scorer is fail-closed. `evaluation_eligible = true` must be explicit in
immutable task metadata before a standalone formal score is produced.

| State | Formal behavior | Aggregate behavior |
| --- | --- | --- |
| `eligible` | Deterministic `answer_score` is emitted. | Included. |
| Explicit `evaluation_eligible = false`, or missing eligibility in standalone CLI | Blocked; no `answer_score`. | Excluded. |
| `infra_invalid` (missing/bad health receipt, catalog/schema mismatch, or route infrastructure fault) | No `answer_score`; an answer diagnostic is retained only as `diagnostic_answer_score`. | Excluded. |
| Healthy `agent_noncompliant` (a required MCP call/render did not succeed) | Formal `answer_score = 0`; the original answer diagnostic is retained separately. | Included as a comparable zero. |

`aggregate_weighted` reads the same-trial verifier audit and honors
`aggregate_excluded`; it must not turn an infrastructure incident into a model
zero.

## Supported structured contracts

Supported primary contracts are:

- `deterministic_structured_v2_1` and explicit deterministic aliases;
- `structured_deterministic_rubric_v1`; and
- `extent_voxel_tolerance_v1`.

The comparator registry includes `exact`, canonical string/enum/asset/residue/
space-group comparisons, boolean equality, `integer_equal`, scalar absolute and
circular tolerances, `vector_abs_tolerance`, canonical atom identity,
`voxel_member_of_set` (a voxel index judged against a bit-packed ground-truth
region shipped inside the rubric item), and the two list comparators:
`canonical_set_equal` for the label/object allow-list (order-insensitive,
multiplicity counted) and `ordered_string_vector_equal` for the string-range
form of `extent_vox` (positional — a permutation is a wrong answer). The
numeric triplet form of `extent_vox` keeps its voxel tolerance, and any list
outside the allow-lists still fails closed as `missing_tolerance`.
`integer_equal` is type-sensitive; `vector_abs_tolerance` compares ordered
components, at any nesting depth. The NIfTI extent contract requires its
declared three-axis order and fixed tolerance configuration.

### String normalisation

These rules are in the current candidate `server-20261014-h20`, built from the
`feat/deterministic-free-text-scoring` merge at `b1e0135b`. The superseded
`server-20260928b-h20` predates them and still grades a ground truth of
`second_render` against an answer of `second render` as unequal.

Both sides of a string **value** comparison pass through one canonicaliser
before equality is decided:

- strip, case-fold, and collapse runs of whitespace to one space;
- **if the value as written carries at least three space-separated tokens**,
  remove one leading `a ` / `an ` / `the `;
- replace every `_` with a space — a swap, never a deletion: `2_6` is `2 6`,
  not `26`;
- a value that reduces to nothing falls back to the first step, so `___` is
  not the empty answer.

The article is judged **before** the underscore swap, on the string as written.
The reverse order would let `a_b_c` pass for `b c`: it would first
manufacture two token boundaries and then delete a letter that was never an
article. Both the order and the floor are pinned in
`tests/sci3d/scorers/test_string_normalisation.py`.

Dict **keys** are out of scope on purpose. A key is a schema identifier naming
a slot in the contract's shape (`chain`, `residue_name`, `label`), not a value a
model spelled, so the two routes that walk a dict — the list comparators'
`_canonical_leaf` and the atom-identity route's `_canonical_atom_object` — fold
case and surrounding whitespace on the key and stop there. No comparator
applies the two rules above to a key. The exclusion is pinned by
`test_the_value_rule_stops_at_a_dict_key` and
`test_the_value_rule_stops_at_an_atom_identity_key` in the same file; the
routes and their denominators are in
[`docs/harbor/FREE_TEXT_SCORING.md`](../harbor/FREE_TEXT_SCORING.md) §1.

"Only widens" is a theorem where it is claimed as one, and nowhere else. For
the plain string comparators the new canonical form is a pure function of the
old one, so two values that compared equal before cannot compare unequal now —
this holds for every string, and the test above asserts it as an
equivalence-class split check. The three structure parsers
(`canonical_residue_equal`, `canonical_space_group_equal`,
`canonical_asset_id_equal`) do **not** inherit it: each ends in a step that
discards the positions of delimiters, so sharing this normaliser pushed them
into narrowing, and they are now pinned to the pre-change form instead. Their
safety is structural, not measured. The shape-to-comparator table, with a real
pair behind each row, is
[`docs/harbor/FREE_TEXT_SCORING.md`](../harbor/FREE_TEXT_SCORING.md).

Ground truths may carry a null. TOML has no null, so the tree renderer writes
the `__sv_null__` sentinel and `task_metadata_to_taskspec` restores it to
`None` recursively; a string that merely contains the sentinel is untouched.
Trees built before this sentinel landed still carry `""` and grade a null
answer as wrong until they are rebuilt.

Measurement bands follow the closed-loop agreement with the expert side
(2026-09-23): exact match with the third decimal place free, implemented as
±0.005 for every float measurement, whatever precision it is written with;
angle fields keep `periodic_modulus=360`. The band is derived at tree-build
time (`scripts/harbor/_tolerance_rules.py`), so the image executes whatever
band a tree records. A membership item is judged in-image: an out-of-bounds
answer is a wrong answer, and a malformed region is `invalid_member_set` —
fail closed, never a quiet zero.

Prose/LLM judges, mask-Dice, unknown scorer contracts, malformed metadata, and
malformed terminal answers are never repaired or auto-graded by this image.
They remain blocked/fail-closed rather than receiving a fabricated score.

## v9 coverage receipt

The machine-readable report is
[`scorer_bundle/reports/v9_coverage.json`](../../scorer_bundle/reports/v9_coverage.json).
Its v9 task-directory source hash is
`177f937e7c4c719fbaa1aa3a4fb57be191f3f4d504ef08d04fb25e909ced60dc`.

| Coverage category | Tasks |
| --- | ---: |
| `evaluation_ready` | 104 |
| `structured_blocked` | 94 |
| `prose_blocked` | 50 |
| `malformed_blocked` | 2 |
| Total | 250 |

Ready-task domain counts are architecture 34, biology 34, medical 25,
chemistry 10, and mechanical-CAD 1. The historical `med63` / `28 mask`
statement belongs to the v7 review bundle (medical 63; `med_imaging_geometry`
and `role=mask` each 28). It is neither a v9 statistic nor a public-input
authorization.

## Docker Hub release and verification

Only an authorized release owner should authenticate and push the explicit
candidate tag. Never put a token or password in a command history, file, image,
or build argument.

```sh
docker login
docker push suermars/sci3d-scorer:server-20260928b-h20
docker buildx imagetools inspect suermars/sci3d-scorer:server-20260928b-h20
docker pull suermars/sci3d-scorer:server-20260928b-h20
docker image inspect suermars/sci3d-scorer:server-20260928b-h20 \
  --format '{{.Id}} {{index .RepoDigests 0}}'
```

Verify the pushed image is non-root and offline at runtime:

```sh
docker image inspect suermars/sci3d-scorer:server-20260928b-h20 \
  --format '{{.Config.User}} {{json .Config.Entrypoint}}'

docker run --rm --network=none --read-only --cap-drop=ALL \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -v "$PWD/task.toml:/work/task.toml:ro" \
  -v "$PWD/answer.json:/work/answer.json:ro" \
  suermars/sci3d-scorer:server-20260928b-h20 \
  --task /work/task.toml --answer /work/answer.json
```

Archive the manifest SHA-256, image ID, immutable Hub digest, and test output
in the release receipt. A pushed digest that does not match the reviewed
manifest is a new candidate, not this release.
