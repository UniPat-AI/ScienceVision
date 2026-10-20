# ScienceVision scale-v2.3

A deterministic re-render of the committed `scale-v2.2` candidate set. Two things
change relative to v2.2 and nothing else does: the prompt is re-rendered as natural
language, and the subquestions that share one physical asset are also published as a
single question. `ground_truth`, `scorer`, `scorer_config`, `answer_schema` and both
oracle receipts are copied through byte-for-byte on the subquestion layer, and a build
gate asserts it. No LLM, no planner re-run and no asset re-closure are involved.

Every row in this directory carries `disposition: "candidate"`. This is a candidate
data drop, not a formal benchmark release.

## Where the question lives

Four generated files, all pinned by SHA256 in `scale-v2.3-build.json`:

| File | Rows | Unit |
|---|---|---|
| `ScienceVision_scale-v2.3_physical_asset_questions.jsonl` | 854 | one question per physical asset |
| `ScienceVision_scale-v2.3-medical_candidate_tasks.jsonl` | 605 | one question per measurement |
| `ScienceVision_scale-v2.3-chemistry_candidate_tasks.jsonl` | 401 | one question per measurement |
| `ScienceVision_scale-v2.3-biology_candidate_tasks.jsonl` | 327 | one question per measurement |

605 + 401 + 327 = 1333 subquestions, which the 854 asset questions reference exactly
once each — no duplication, no omission. `scale-v2.3-build.json` records both totals as
`subquestion_count` and `physical_asset_question_count`.

## Which field is the question

**`instruction`.** There is no `question` key in either layer.

```bash
D=QA-Gen/tasks/scale-v2.3

# the 854 asset questions, as an agent sees them
jq -r '.instruction' $D/ScienceVision_scale-v2.3_physical_asset_questions.jsonl

# one specific asset question
jq -r 'select(.task_id=="sv-asset-4f93928a2e521391").instruction' \
  $D/ScienceVision_scale-v2.3_physical_asset_questions.jsonl

# the 1333 subquestions
cat $D/ScienceVision_scale-v2.3-*_candidate_tasks.jsonl | jq -r '.instruction'
```

`instruction` is non-empty on all 854 and all 1333 rows, and every one ends with an
`**Answer format**` block holding the JSON skeleton. Asset-question prompts run
426–3663 characters; subquestion prompts run 355–1035.

`instruction` contains newlines, so counting distinct prompts with
`jq -r .instruction | sort -u | wc -l` counts lines, not questions. Use
`jq -r '.instruction | @base64'`.

Four neighbouring fields are routinely mistaken for the prompt and are not it:

- **`title`** — the question-family label, not the question. 66 distinct titles cover
  all 1333 subquestions; the 605 medical ones share 11. Grouping by `title` yields a
  family histogram, not a question list.
- **`source_instruction`** — v2.2's exact string, kept for diffing. v2.2's fallback
  renderer dropped all but the leading verb of each phrase and then printed the raw
  `snake_case` field key, so these read `measure distance_A` where v2.3 reads
  `measure the selected pair distance in angstroms`. Subquestion layer only.
- **`question_plan`** — the generator's plan: `intent_id`, `oracle_operation`,
  `viewer_operation`, `required_fields`, `asset_evidence`. It names the ground-truth
  derivation path. Do not show it to a solver or a reviewer.
- **`answer_schema` / `terminal_answer`** — the answer contract, not the prompt.

## Both layers are current

The 1333 flat subquestions remain the per-measurement scoring unit; the 854 asset
questions are an additional publication, not a replacement. Neither supersedes the
other, and both come out of the same build.

The reason the asset layer exists is cost, not correctness: v2.2 asks about `10BT.cif`
four separate times, so an agent pays the load-and-orient cost four times over. The
asset question asks the same measurements once.

Pick by what you are doing. Running an agent over the corpus — use the 854, so each
asset is opened once. Scoring or auditing an individual measurement — use the 1333,
where each row carries its own receipts, `question_plan` and `validation_record`.

## How an asset question differs from its subquestions

**When `question_count == 1` (607 of 854), the prose is byte-identical** to its single
subquestion. Only the answer skeleton changes — it gains a `targets` wrapper keyed by
subquestion `task_id`. Parent `sv-asset-4f93928a2e521391`, holding
`sv-medi-med_ct_geometry-0000`:

```diff
  {
+   "targets": {
+     "sv-medi-med_ct_geometry-0000": {
        "centroid_vox": <list>,
        "crosses_fov_midline": <boolean>,
        "longest_axes": <list>,
        "representative_axial_index": <integer>
+     }
+   }
  }
```

**When `question_count > 1` (247 of 854), the prompt is re-rendered as a numbered
list**, one item per subquestion, and `targets` carries one block per subquestion
`task_id`:

```
Answer every numbered question below. Scope that applies to one question only is stated with it.

1. This bound structure contains non-water components A1C4R, CL alongside polymer
   chains A, B, …. Measure the selected pair distance in angstroms, record the …
2. Structure 10BT covers non-water components A1C4R and CL, polymer chains …
```

Scope shared by every member is hoisted ahead of that sentence (20 of the 247 groups
have such a preamble); scope belonging to one member stays with its numbered item.

The `targets` wrapper is not cosmetic. **222 of the 247 multi-question groups have at
least two subquestions declaring the same `answer_schema` field name**, so a flat
merged object would silently drop answers.

Group sizes: 607 × 1, 80 × 2, 102 × 3, 65 × 4 = 1333.

## Scoring shape

| | Subquestion layer | Asset-question layer |
|---|---|---|
| `scorer.primary` | `structured_deterministic_rubric_v1` | `deterministic_structured_v2_1` |
| `scorer.checks` | the answer field names | always `["targets"]` |
| `scorer_config` | `fields`, `strict_json_object` | `rubric[]`, each item with `gt_path` = `targets.<subquestion_task_id>.<field>` |
| `scorer_config.schema_version` | `sciencevision.generic_deterministic_rubric.v1` | `sciencevision.grouped_deterministic_rubric.v1` |

`checks: ["targets"]` names the top-level answer key, not the depth of checking: the
per-field comparators and tolerances live in `scorer_config.rubric[]`, 5624 items across
the 854 rows. The asset rubric is compiled by calling the same scorer routine that grades
the flat subquestions, once per block with `path_prefix="targets.<task_id>."`, so the two
layers cannot drift apart.

## Fields only one layer has

Only on the asset-question layer: `kind` (always `physical_asset_question`), `parent_id`,
`question_count`, `subquestion_task_ids`, and the plural roll-ups `domains`,
`source_families`, `v_classes`.

`v_class` is not a ladder — V1–V6 name visual-operation primitives — so a group spanning
several primitives cannot honestly claim one. The asset row publishes the full set as
`v_classes` and fills the schema-required scalar with the modal member class.
`difficulty` inherits the strongest label in the group, because an agent answering the
whole group must clear the hardest member.

Only on the subquestion layer: `question_plan`, `asset_grounding`, `entity_selection`,
`target_selection`, `render_contract`, `oracle_receipt_id`,
`independent_oracle_receipt_id`, `independent_oracle_receipt_sha256`,
`validation_record`, `quality_gates`, `acceptance_info`, `source_task_id`,
`source_instruction`, `supersedes`, `expert_review_requirements`, `knowledge`,
`scientific_action`, `mask_id`, `instruction_contract`.

## Domain coverage

| Layer | Breakdown |
|---|---|
| 854 asset questions | medical 453, chemistry 270, mixed-domain 131 |
| 1333 subquestions | medical 605, chemistry 401, biology 327 |

No asset question is biology-only. All 327 biology subquestions sit in the 131
mixed-domain groups they share with chemistry.

## Provenance and verification

`source-pin.json` pins the input: `scale-v2.2` at ScienceVision commit
`43efeb47c6eaf6a74c4579f5c042145fadc5d457`, tree
`a1d0a1ee0f1bb306090eb0e02843b6c910728ffb`, `source_clean: true`, with a SHA256 for each
of the five source files.

`scale-v2.3-build.json` is authoritative for this drop — it carries the row count and
SHA256 of every generated file, plus the gates the build asserted (`carried_through`,
`rewritten`, `prose_is_natural`, `no_spelled_measurements`, `self_scores`).

```bash
python3 - <<'PY'
import hashlib, json, pathlib
d = pathlib.Path('QA-Gen/tasks/scale-v2.3')
man = json.loads((d / 'scale-v2.3-build.json').read_text())
for name, meta in sorted(man['outputs'].items()):
    blob = (d / name).read_bytes()
    rows = blob.count(b'\n')
    ok = hashlib.sha256(blob).hexdigest() == meta['sha256'] and rows == meta['rows']
    print(f"{'OK' if ok else 'MISMATCH':9} rows={rows}/{meta['rows']} {name}")
PY
```

The generator lives in ScienceVision (`scripts/qa_gen_scale_v2_3.py` and its
`scripts/_qa_gen_v2_3_*.py` helpers) and is not mirrored here — `.mirror-include` brings
across the built data, not the build. Nothing in this repository reads
`physical_asset_questions.jsonl` yet; the asset layer is published for consumers, not
wired into a runner here.

## Known limits

Read these before treating scale-v2.3 as an evaluation result:

- All 854 asset questions declare `screenshot` in `required_evidence` (131 also declare
  `annotation`, 65 also `variant_screenshots`), but **0 of the 5624 rubric items
  reference a screenshot**. Scoring is JSON-only; the visual evidence is declared and not
  checked. Tracked as [#94](https://github.com/UniPat-AI/BabyVision-v2/issues/94).
- The open validity, contamination, and distribution findings against this drop are
  [#92](https://github.com/UniPat-AI/BabyVision-v2/issues/92),
  [#94](https://github.com/UniPat-AI/BabyVision-v2/issues/94)–[#101](https://github.com/UniPat-AI/BabyVision-v2/issues/101)
  and [#103](https://github.com/UniPat-AI/BabyVision-v2/issues/103).
