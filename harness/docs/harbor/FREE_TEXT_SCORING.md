# Free-text scoring: which value shape takes which comparator

**Coordinates.** Read this if you have to decide how a structured answer field
is graded — authoring or reviewing a question, explaining a failing verdict, or
proposing a fix for a task that no answer can reach 1.0 on. When you are done
you can name the comparator that will judge a given `(field, ground truth)`
pair without running the scorer (§2), tell which claims about the 2026-10-14
normalisation change are measured and which are not (§3), and say whether a
failing verdict is a scorer limitation or a task defect plus what each remedy
costs (§4).

**Acceptance for this document.** You can point at a field in the v11-250
delivery and say how it will be graded; every "no movement" claim you repeat
carries its denominator; and any proposal you make to change the normalisation
names the replay diff and its `regressions` list (§5).

**Where the evidence lives.** The examples are real pairs from the delivery
(`/Users/sue/Research/SV-data/delivery/v11-250-20260929`, read-only), from
`~/sv-freetext/exact_fail_scan.json`, or from a committed test; the §2 and §4
tables say which in their lead-in, the other tables name it in the row. The
counts come from a replay of that delivery, which **you can regenerate with the
§5 recipe** — `~/sv-freetext/normalisation_delta.json` is that replay's output
on one machine, not a record of authority. Two facts rest on the scorer's own
source and say so where they appear: the prose thresholds (`_PROSE_WORDS`,
`_PROSE_CJK`) and the ±0.005 band, which `scripts/harbor/_tolerance_rules.py`
derives at tree-build time. Where a fact cites `task-1-report.md` /
`task-2-report.md` or the design spec, those are the change's **working notes**,
not versioned artefacts of this repository; each such fact also names the
committed test or tool that carries its substance.

**Scope.** This describes the scorer on branch
`feat/deterministic-free-text-scoring`. The pinned release candidate
`server-20260928b-h20` was built from `d41b21bc`, which predates the
normalisation change in §1, so on that image both pairs in §1 still compare
unequal. A rebuilt candidate is required before the semantics here take effect;
see `docs/docker-image-release/SCORER_RELEASE.md`.

**Which copy is authoritative.** This scorer exists **twice**, and the two are
not the same file. The copy on this branch
(`sci3d_bench/scorers/deterministic_answer.py`) is the source of truth for
everything in this document — every rule, comparator and routing line described
here is that file's behaviour, and its tests are the ones named throughout. The
other copy sits in the ScienceVision checkout, where
`harbor_adapters/sci3d/_scorer_registry.py` and `harbor_adapters/sci3d/_score_core.py`
import it, and it **predates all of this**: it has no `numeric_literal_equal`,
no `canonical_string_in_set`, no `_canonical_structure_string`, and none of §2's
name heuristics, so a re-score run from that checkout silently grades with the
old normalisation — no error, no warning, just the pre-change answers. Do not
edit that copy to match this one as a side effect of a change here, and do not
read its behaviour as this document's. Reconciling the two is its own piece of
work, and until it is done the ScienceVision copy is the older lineage rather
than a mirror.

## Vocabulary

| term | meaning in this document |
|---|---|
| **ground truth (GT)** | the expected value for one field, as the task's contract declares it. It is a *value*, not a question — values are what comparators see. |
| **comparator** | the function that decides whether an answer value equals the GT value for one field. It is chosen per field, before the answer is read. |
| **verdict** | one comparator's pass/fail for one field of one trial. A task's score is the weighted share of its verdicts that passed. |
| **prose field** | a field whose GT is a sentence (≥8 words, or ≥16 CJK characters — `_PROSE_WORDS` / `_PROSE_CJK` in the scorer) *and* whose name is on the explanation-field list. Prose fields are graded like any other field when they stay in the rubric (§4). |

## 1. What the change did, in four real verdicts

The 2026-10-14 change adds two normalisation rules to the string route. It moved
exactly four verdicts out of 968, in two tasks, both arms of each:

| task | field | arms | ground truth | answer | before | after |
|---|---|---|---|---|---|---|
| `provider-author-run-54736b43eb55-255f7b7b7b-svscene-med-0019` | `selected_render` | cc, ccmcp | `second_render` | `second render` | unequal | **equal** |
| `provider-author-run-54736b43eb55-0fd322e60d-svscene-med-0093` | `answer` | cc, ccmcp | `the upper-abdominal level in the first render` | `upper-abdominal level in the first render` | unequal | **equal** |

These four entries are the `moved` list of the replay document (§5 regenerates
it). Read both questions and both answers in full before proposing anything
about these two tasks: the question is in the delivery's own
`<domain>/<task>/task_and_trajectory.json` (key `question`), the answer is the
one the §5 replay recovers from that trial's
`runs/<arm>/agent/trajectory.json`, and `task-2-report.md` §3.1–§3.2 pastes both
with the by-eye reading (working notes, see above).

Both answers are correct to a human reader: the questions are two-way choices,
and each answer names the right option and differs from the GT only in how the
option is spelled. `med-0019` joins two words with `_`; `med-0093` drops a
leading `the`. Neither is a different answer — that distinction is the whole
point of §4.

### The rule, as the scorer now applies it

**Which routes take it, and which deliberately do not.** The rule reaches every
string **value** a scalar string comparator compares, and the string leaves
inside a list or an object. It stops at a dict **key**. Two live routes walk a
dict and both normalise keys with `strip` + casefold and nothing else:
`_canonical_leaf`'s dict branch (`sci3d_bench/scorers/deterministic_answer.py`,
which serves the two list comparators) and `_canonical_atom_object`'s key loop
(the atom-identity route, `canonical_atom_id_equal`). Neither is a hole in the
change: a key here is a **schema identifier** — `chain`, `residue_name`,
`label` — not prose a model wrote. The field is *declared* under that spelling
in the contract, and a key that an article-dropping, underscore-swapping rule
had rewritten would stop being the identifier its value is answered under. It
is the same category error as feeding these rules into the three structure
parsers (§3.1), arrived at from the other side. The exclusion is deliberate and
is pinned, so a later reader does not "finish the job" by pointing the keys at
this normaliser:
`tests/sci3d/scorers/test_string_normalisation.py::test_the_value_rule_stops_at_a_dict_key`
and `...::test_the_value_rule_stops_at_an_atom_identity_key`.

Both excluded routes have **non-zero denominators** in this corpus, which is
what makes the scope worth stating rather than assuming: all **6**
`ordered_string_vector_equal` verdicts compare `list[dict]`, and **12 of the
16** `canonical_atom_id_equal` verdicts compare dicts (the other four compare
integers, which take the numeric branch and no key at all).

For any string value, the canonical form is produced in this order:

1. strip, casefold, collapse runs of whitespace — this step predates the change;
2. if the result has **≥3 tokens**, drop **one** leading `a ` / `an ` / `the `;
3. replace every `_` with a space, then collapse again;
4. if that leaves an empty string, fall back to step 1's result.

Steps 2 and 3 are the change. **The order is fixed and load-bearing**: the
article is judged on the string *as written*, before the underscore swap.
Swapping first would manufacture a token boundary out of one identifier and
then delete a letter that was never an article — `a_b_c` would collapse onto
`b c`. That ordering trap is pinned by
`tests/sci3d/scorers/test_string_normalisation.py::test_the_article_rule_judges_the_original_token_count`.

### The floor: what does not become equal

The two rules are deliberately narrow. Each of these pairs is pinned by a test
in the same file, and each is a rule a reader might otherwise assume:

| pair | why it stays unequal |
|---|---|
| `2_6` vs `26` | `_` is **swapped for a space, never deleted** — `2_6` becomes `2 6` |
| `2.6` vs `26` | no punctuation is removed |
| `X-axis` vs `X` | no dash handling; the remedy is an `accepted` alias (§4, *the same requirement written two ways*) — a remedy with no carrier on a delivered contract yet, see *The alias remedy has no vehicle today* — not a normalisation rule |
| `a b` vs `b` | two tokens is an identifier pair; the article rule needs three |
| `A` vs `` (empty) | a single letter is a label, not an article |
| `___` vs `` (empty) | a value that reduces to nothing is not the empty answer |
| `a_b_c` vs `b c` | the ordering rule above |
| `the_a_b` vs `a b` | the ordering rule above |
| `C-terminus` vs `N-terminus` | the opposite answer, one letter apart — see §4 |
| `mC>` vs `mC` | a ground-truth entry defect; the comparator deliberately does not paper over it |

Two properties bound how far "the answer is correct" can be pushed.
**A non-string value has no canonical form at all**: the string comparators
return false when either side is not a string, so two *identical integers* fail
a string comparison. And **the two rules can only merge values, never split
them**, on this route — the precise scope of that claim, and the places where it
is *not* what holds, is §3.1.

One trade is recorded rather than hidden: an earlier draft of the rule put the
article step last, and that draft made `the_second_render` equal to
`the second render`. The final order makes them unequal again. The pair was
unequal before the change too, so nothing that passed stopped passing — but no
normaliser can hold both that and `a_b_c` ≠ `b c`, and the fixed order chooses
the conservative side: a missed widening leaves a correct answer graded wrong,
while a false positive grades a wrong answer right. There is no GT spelling
`the_second_render` in the bundle, so the pair has no instance in this corpus
either way (`task-2-report.md` §4, "the predicted non-movements").

## 2. The routing table

Read the decision list top-down: **the first line that matches is the
comparator**, and the lines below it never run. The input is the GT's shape and
the field's name, never the answer — so you can decide how a field is graded
before anyone has answered it.

Type of the ground truth and the field name, in order:

1. GT is a list **and** the field is on the **set-equal allow-list** → `canonical_set_equal`, order-insensitive, multiplicity counted.
2. GT is a list **and** the field is on the **positional allow-list** and the list is not all numbers → `ordered_string_vector_equal`, position counts.
3. GT is not a number, and the name mentions a space group → `canonical_space_group_equal`.
4. GT is not a number, and the name contains `residue` → `canonical_residue_equal`.
5. GT is not a number, and the name contains `asset` or `structure` → `canonical_asset_id_equal`.
6. The name contains `atom` and either ends in `_id` or the GT is an object → `canonical_atom_id_equal`.
7. The name contains `dihedral` or ends in `_angle_deg` → `circular_tolerance` (band plus periodic modulus).
8. GT is a string that parses **in full** as a number → `numeric_literal_equal`.
9. GT is a list → `vector_abs_tolerance`.
10. The contract declares a tolerance for this field → `abs_tolerance`.
11. GT is a boolean, or the name starts with `has_` or `crosses_` → `boolean_equal`.
12. GT is an integer → `integer_equal` (type-sensitive).
13. **GT is a string** → `canonical_string_equal` — unless the item declares `accepted` values (the comparator supports them; no delivered contract carries one — *The alias remedy has no vehicle today* in §4) or the GT yields a mechanically-derived alias, in which case the comparator becomes `canonical_string_in_set` with that list. Only the derived half is live in this corpus.
14. Nothing above matched → `exact`, an identical-type identical-value test.

The two allow-lists are explicit lists in the code (`SET_EQUAL_FIELDS`,
`ORDERED_STRING_VECTOR_FIELDS` in `sci3d_bench/scorers/deterministic_answer.py`),
not shape tests, so no field is loosened by accident. Adding a list field to a
question is therefore a routing decision, not a formatting one, and the two ways
it can go wrong are not equally loud:

- **On neither list, no declared band** → line 9 gives it `vector_abs_tolerance`,
  and validation fails with `missing_tolerance:<field>`, voiding the whole task
  as `invalid_task_contract`. **Loud**, which is what a missing contract wants
  to be — and it is the error the twelve skipped biology trials in this corpus
  recorded (see the `canonical_set_equal` row), for field names today's
  allow-lists accept.
- **On neither list, band declared** → validation passes and the task is
  `eligible`, then **every** comparison fails, because
  `_compare_vector_tolerance` requires numeric leaves and returns false on a
  label. A string list with a band is a silent zero where the contract's
  fail-closed promise does not reach. Numeric lists are unaffected: a band is
  exactly what they need.

What validation reads is the item's declared band, never the element type:
`[1, 2]` and `["a", "b"]` are equally invalid without one, and — with one —
equally `eligible`, though only the first can ever pass.

Where free text lands: lines 3–6 (named structure fields), line 13 (everything
else that is a string). Lines 8 and 12 are what keep numbers from arriving
there — a number is never a name, and a numeric *string* on a content field is
graded as the number it spells. A contract that carries an explicit `rubric`
array bypasses this whole list: the comparator is then written in the contract,
one item at a time.

One consequence of the order is easy to miss when authoring: a **float GT with
no declared band** reaches none of lines 8–12 and lands on line 14, `exact`,
where the answer must be the same type and the same value. A measurement field
therefore needs its tolerance written into the contract; without it the task is
not "strict", it is unpassable by any agent that rounds. The band is derived at
tree-build time, so this is a tree question rather than a comparator one.

### One real pair per comparator, from v11-250

A row with a count is a pair the delivery actually graded; a row with `0` says
why there is no such pair. The comparator is the one under which that verdict
was decided in the re-scored rubric (`tools/replay_scoring.py --rules new`),
which is the same rubric the census counts; a trial's own stored audit is at
`<domain>/<task>/runs/<arm>/verifier/sci3d_scores.json` when you want to see it.
Two rows
are marked **F1** / **F3**: their stored trial recorded `canonical_string_equal`
and the current routing moves them — a numeric string is graded as the number it
spells (F1), and a multiple-choice letter is an accepted alias of the full
option (F3).

The counts are the census over **968 compared verdicts**, drawn from **480
scored trials out of the delivery's 500**: 20 rows are skipped and contribute no
verdict, so a comparator's `0` can mean "never reached" rather than "unused".
Twelve of the twenty are the six tasks of the `canonical_set_equal` row below,
skipped by the contract's tolerance validation; the other **eight** are skipped
for a different reason the census does not otherwise state — `rubric id is not a
ground-truth key` — in `sv-chem-chem_conformation-0028` and `-0050`,
`gear-Model07-QA03`, and
`provider-author-run-54736b43eb55-2772a93605-svscene-med-0118`, both arms each:
the trial's own rubric names an item the contract's ground truth does not carry,
so there is nothing to compare and no list here can recover a verdict from it
(two of the four tasks are recorded as per-task items in the rework proposal,
§6.1). Read the `canonical_set_equal` row first: it has a zero denominator with a
written reason, and that reason belongs to the run rather than to the
comparator.

| comparator | count | real pair (task / field / arm) |
|---|---:|---|
| `canonical_string_equal` | 248 | `provider-author-run-54736b43eb55-255f7b7b7b-svscene-med-0019` / `selected_render` / cc — GT `second_render` vs answer `second render`: unequal → equal (§1). Floor, same comparator: `sv-arch-exp-02` / `dominant_axis` / ccmcp — GT `X` vs answer `x` passes (casefold, pre-dating the change), while `X` vs `X-axis` fails (`~/sv-freetext/exact_fail_scan.json`). |
| `canonical_string_in_set` | 6 | **F3** — `sv-industrial-cadasm-vqa-vqcb-031-10-copy` / `answer` — GT `D. rotates counter-clockwise by 90 degrees` vs answer `D`: passes, because the option letter is a mechanically-derived alias of the GT. |
| `canonical_atom_id_equal` | 16 | `sv-chem-chem_conformation-0002` / `partially_occluded_heavy_atom_id` — GT `2` vs answer `2`: passes. Same comparator, a real failure: `nearly_fully_occluded_atom_id` / ccmcp — GT `21` vs answer `11` fails. The integer branch is pinned by `tests/sci3d/scorers/test_atom_id_comparators.py::test_an_integer_atom_id_matches_itself`. |
| `canonical_set_equal` | 0 | **No verdict — the zero records one run, not a live gap.** Six ground-truth leaves route here (`interaction_types` ×2, `atom_labels` ×3, `residue_numbers` ×1, in `sv-biol-bio_collision_check-0006/0010/0019/0024/0034` and `sv-biol-bio_docking_plausibility-0055`), and all twelve of their trials are **skipped** in this delivery, each trial's own audit reading `invalid_task_contract: missing_tolerance:<field>`. The routing belongs to the *scorer*, not to the contract: it is compiled at score time, and the allow-list that sends these fields here entered the shipped code in `d41b21bc` (2026-09-28 01:47 −0400) — about thirteen hours after the 2026-09-27 run that produced these twelve trials. A tree cannot declare that allow-list, because it is a constant of the scorer, so these trees carry the same fix the way they can — as a staged code patch (`tests/_set_equal_patch.py`, imported by `tests/score.py`, replacing `_default_comparator` for exactly these field names), added the next morning in both arms. So a run scored from this branch does not void them, and the six tasks are not a delivery defect. The comparator is exercised in-process by `tests/sci3d/scorers/test_list_comparators.py` (`test_a_label_list_scores_order_insensitively`, `test_a_label_list_still_counts_multiplicity`, `test_coordinating_atoms_is_a_set_with_multiplicity`) and by `test_named_identity_fields.py::test_a_real_residue_list_keeps_the_set_comparator`. |
| `ordered_string_vector_equal` | 6 | `sv-chem-chem_binding_geometry-0100` / `waters` — GT starts `residue_number` 592, 627; the answer gives 515, 521: fails, position and content both. Positionality is pinned by `tests/sci3d/scorers/test_list_comparators.py::test_extent_vox_is_positional_and_a_permutation_fails`. |
| `canonical_residue_equal` | 0 | **No verdict — but four stored pairs route here under today's dispatch.** The count is the comparator each stored trial's own rubric carries, and the four audits below record `canonical_string_equal`, because the checkout that scored them routes on value shape alone and has none of lines 3–6's name heuristics (see *Which copy is authoritative*). Re-scored from this branch the field name sends them here instead: `sv-biol-bio_collision_check-0130` / `terminus_nearer_noncanonical_residue` and `sv-biol-bio_docking_plausibility-0130` / `terminus_nearer_chain_A_residue_010_C2`, both arms each, ground truth and answer both `N-terminus` — so they pass under the stored route and this one alike, before and after the change. §3.3 has the reading. In-process evidence: `tests/sci3d/scorers/test_named_identity_fields.py`. |
| `canonical_space_group_equal` | 0 | **No instance.** See §3.3. Its narrowing pairs are in `tests/sci3d/scorers/test_string_normalisation.py` (`MONOTONE`). |
| `canonical_asset_id_equal` | 0 | **No instance.** See §3.3. Its narrowing pair is in the same `MONOTONE` table. |
| `integer_equal` | 306 | `arch-anzj-clash-a03` / `true_interference_count` / cc — GT `0` vs answer `0`: passes. Real failure, same task: `bbox_false_alarm_count` — GT `9` vs answer `6`. |
| `numeric_literal_equal` | 22 | **F1** — `arch-anzj-align-a07` / `shortest_continuous_extent_mm` / cc — GT `"12000"` (a string) vs answer `12000` (a number): passes, because the GT string parses in full as a number. |
| `abs_tolerance` | 80 | `asset-c2c2a012842b-cross-view-identity-matching` / `parapet_height` / cc — GT `1.6` vs answer `1.6`: passes, inside the ±0.005 band (`scripts/harbor/_tolerance_rules.py`, derived at tree-build time). |
| `vector_abs_tolerance` | 132 | `asset-5f4015c66d24-chem-conformation-explicit-dihedral` / `initial_occluded_atom_ids` / cc — GT `[3]` vs answer `[3, 16]`: fails, the lengths differ. |
| `circular_tolerance` | 62 | `sv-chem-chem_conformation-0093` / `dihedral_deg` / cc — GT `6.4` vs answer `6.4`: passes, modulo 360. |
| `boolean_equal` | 62 | `arch-anzj-stair-b01-headroom` / `meets_the_stated_clearance` / cc — GT `true` vs answer `true`: passes. |
| `exact` | 28 | `asset-0a88c276750f-cross-floor-member-alignment` / `spatial_overlap_between_1F_and_3F` / cc — GT `{"overlap_status": "no_overlap"}` vs answer `{"overlap_status": "not_overlapping"}`: fails; nothing above `exact` claims an object. |

`canonical_enum_equal` and `canonical_atom_identity_equal` are aliases in the
registry for comparators already listed; `voxel_member_of_set` is the
bit-packed region test used by the voxel contracts and does not appear in this
corpus either.

## 3. Four facts to read before you rely on this table

None of these is what the first reading suggests, and they are not all the same
kind of claim: §3.1 is a proof obligation discharged by construction and a pin,
**not** a measurement, while §3.2–§3.4 are measurements and carry their
denominators. Each is stated with its own evidence, so nothing below requires
the working notes to be readable. Where a fact still cites one, the citation is to the
change's lab record — `task-1-report.md` (the rules) and `task-2-report.md`
(the measurement) under
`.claude/worktrees/sv-release-consolidation/.superpowers/sdd/2026-10-14-free-text-string-normalisation/`
in the ScienceVision checkout, and the design spec
`docs/superpowers/specs/2026-10-14-free-text-deterministic-scoring-design.md`
in that same repository. **Those notes are not versioned with this document**:
the `sdd` directory is gitignored, and the spec belongs to the other repository.
The durable evidence for each fact is the committed test, tool or replay recipe
named inside it.

### 3.1 "Only widens" is a theorem on one route and a pin on another

On the plain string route the new output is a pure function of the old output
(`new = F(old)`, with `F` the article rule, the underscore swap and the empty
fallback), so two values that were equal before cannot separate now — for **any**
pair of strings, not just the ones in this corpus. That is a **theorem**, and
the test that asserts it is
`tests/sci3d/scorers/test_string_normalisation.py::test_no_pre_f5_equivalence_class_is_split_by_f5`
(a precondition-asserting class-split check over a fixed probe list).

It is NOT a theorem for the three structure parsers. `canonical_residue_equal`,
`canonical_space_group_equal` and `canonical_asset_id_equal` each end in a
fallback that **discards delimiter positions**, so they can call two strings
equal while their canonical forms differ; feeding the new rules into that step
split pairs that used to pass. Four such pairs were reproduced:

| comparator | GT | answer |
|---|---|---|
| `canonical_residue_equal` | `Aba2` | `A b a 2` |
| `canonical_residue_equal` | `ab c` | `a b c` |
| `canonical_space_group_equal` | `Aba2` | `A b a 2` |
| `canonical_asset_id_equal` | `dir/an a b.stp` | `an a b.stp` |

These four are also the pairs the monotonicity test drives (`MONOTONE` in the
same test file), with the space-group one doubled by `a b c` vs `abc`: `Aba2` is
space group No. 41 and gemmi resolves it, so the delimiter-insensitive fallback
is never reached on this machine, and the doubling is the pair that reaches it.

The three parsers are now **pinned to the pre-change normalisation** and no
longer call the string normaliser at all (`_canonical_structure_string` in
`sci3d_bench/scorers/deterministic_answer.py`); over the corpus's distinct
values their outputs are back to what they were before the change, and the
property that matters — none of the four pairs they used to call equal may
start failing — is a committed guard, `test_monotone_over_the_corpus` in
`tests/sci3d/scorers/test_string_normalisation.py`, run against all three. So
the right sentence is: **the plain string route widens by construction; the
three structure parsers are held safe by a pin, and that safety is not
something this corpus measured.** Source: `task-1-report.md` R2.1–R2.3.

> **Do not quote half of this.** "The change only widens" is true of the plain
> string route as a theorem, and true of the three parsers only because they
> were pinned back afterwards. Quoting the first half alone is how a
> cross-check gets a free pass it did not earn.

### 3.2 The only evidence with an answer side is the 500-trial replay

The bundle holds contracts (question + ground truth) and no answers, so a
comparison between two GT values cannot be a pass → fail: a widening is only
observable where a GT meets an *answer*. The 500-trial replay is the only
evidence in this change with an answer side: its `regressions` list being empty
is the acceptance criterion, and the pair-level check over its own rows — the
compared (GT, answer) string pairs, 4 newly equal and 0 newly unequal — is the
strongest statement this corpus can make. The verdict-level count comes straight
out of the §5 recipe. The pair-level one does not: it is a one-off script, which
checks each (GT, answer) string pair under both roots' own normaliser instead of
trusting the verdicts. If you need it regenerated, that script is the thing to
write, and the replay rows alone are not enough to write it from: they carry
verdicts, weights and scores, **no values**. Its inputs are
the same three the replay itself reads — the bundle (the ground truths), each
trial's `runs/<arm>/agent/trajectory.json` through the harness's own reader (the
answers), and one imported module per root to canonicalise with.
(`task-2-report.md` §4(a), §8.5, and R6c of the design spec named in §3.)

**Do not quote its denominator as exact — it is 205 by one scope and 207 by
another.** The unit is "a rubric item id whose ground-truth leaf and answer leaf
are both strings, over the 480 scored rows"; on that scope the count is **205**
(and **200** if the five trials that carry a parse-error note are dropped as
unusable). Walking every ground-truth field instead of the rubric items gives
**207** on the same 480 rows, or **202** without those five — which is also the
reading someone reproducing this will more likely reach, because the script
itself is not committed. Both scopes give the same answer to the question that
matters: **4 widened, 0 narrowed**, the four being §1's moved pairs. The
verdict-level counts elsewhere in this document do not carry this caveat: they
come from the §5 recipe, which is a committed tool whose output is on this
machine at `~/sv-freetext/normalisation_delta.json`.

A zero over GT-vs-GT is a different quantity and does not substitute for it.

### 3.3 Three comparators show "zero movement" on a denominator of zero

`canonical_residue_equal`, `canonical_space_group_equal` and
`canonical_asset_id_equal` appear in **none** of the 968 compared verdicts, so
on that denominator this run neither confirms nor refutes their behaviour. Two
of the three are never reached in any reading: no ground-truth leaf in the
bundle routes to `canonical_space_group_equal` or `canonical_asset_id_equal`,
and no stored rubric names them either. **`canonical_residue_equal` is the
exception**, and it is the one a reader is most likely to get wrong: no
*verdict* reaches it in the census, but under today's dispatch four stored
pairs do (§2's row — the two `-0130` tasks, both arms), and they pass both
before and after the change. So for that comparator the corpus is not silent —
it holds four live pairs to drive in-process — while the pin in §3.1, not the
replay, is what guarantees them. The contrast that makes the general reading
concrete: `canonical_atom_id_equal` is reached **16** times and moved **0** —
that one is a real measurement. The same reading applies to
`canonical_set_equal`, whose six routing targets were skipped rather than scored
(§2): a zero verdict count is a statement about what reached the comparator, and
you need the denominator — and the skip reasons — before you can say which.
Source: `task-2-report.md` §4(d); the counts come back from the §5 replay, and
the four residue pairs come back from the delivery's own audits plus the
routing in §2.

### 3.4 The replay tool's `old` column is not frozen against the change

`old_comparators()` expands the comparator table of **the checkout it loaded**,
so under a post-change root the "old" column inherits the change too. The
symptom is the reproduction count: **465 of 480** with a pre-change root versus
**461 of 480** with the post-change root, and the four rows that stop
reproducing are exactly the four moved rows. The measurement in §1 is not
affected (both runs used `--rules new`), but a true pre-change column requires
two roots:

> **Use `--scorer-root` for the before-side.** A single checkout cannot produce
> a before-and-after: the tool refuses it on purpose (exit 2, no document
> written) rather than hand back a `moved=0` that means "nothing was measured".

The code behind the symptom is `old_comparators()` in the committed
`tools/replay_scoring.py`, which expands the loaded root's table rather than a
frozen copy; the counts come back from the §5 replay. Source:
`task-2-report.md` §5 finding 2, §8.4, §9.1.

## 4. What this scorer cannot judge, and the remedy for each

Four shapes fail for reasons no normalisation can reach. Each is a deliberate
limit with a named remedy; the per-item decisions, and the cost in trials, belong
to the rework proposal,
`_ops/review-v11-20260929/freetext_rework_proposal.md` in the ScienceVision
worktree §3 names, which needs owner sign-off before anything is applied to
a delivered contract. All four pairs below are entries of
`~/sv-freetext/exact_fail_scan.json`, whose records are
`{task, arm, field, gt, ans, f1}`: the pair plus an F1 similarity score over the
two strings, with no trial count and no verdict.

| shape | why it cannot be judged | remedy |
|---|---|---|
| **The same requirement written two ways.** GT `Article 7.1.8 of the Code for Fire Protection Design of Buildings GB50016-2014 (2018 Edition)` vs answer `GB 50016-2014 (2018 edition), clause 7.1.8(1)` (cc, `arch-rhl-access-gas-m06-fire-lane-entrance` / `cited_clause`). Second instance of the shape: GT `ZN 201` vs answer `201` (`provider-author-run-afc34cd1be7a-af6b4b4974-svscene-bio-0028` / `closest_zinc`). | The two strings share no spelling beyond the code number and the clause number; equality of *meaning* is not a string property, and every rule that would reach it (strip punctuation, drop stopwords) also merges genuinely different values. | Declare the alternative as an `accepted` value on the rubric item — an alias asserts that a *different* answer is also correct, which is a claim about the question and must be made by the question's owner, not by the comparator. **No delivered contract can carry one today**: read *The alias remedy has no vehicle today* below before you sign this row, and do not confuse it with the aliases the scorer derives on its own (routing line 13). |
| **The opposite answer.** GT `C-terminus` vs answer `N-terminus` (`sv-biol-bio_collision_check-0076` fields `terminus_nearer_P_centre_H` / `_L`, and `-0122` field `terminus_nearer_chain_centre`, both arms). | They differ by one letter at the start. Any rule loose enough to merge them merges the two termini of every protein question there is. | None. It stays a fail: this is the control case that keeps a loosening rule from becoming a licence. The answer is wrong. |
| **An explanation where nothing else is left to score.** GT `The compact stick-rendered cluster sits directly between the orange ribbon region …` vs an answer that says the same thing in different words (`provider-author-run-afc34cd1be7a-8da80353a2-svscene-bio-0063` / `visual_basis`, both arms). | Explanatory fields are dropped from the rubric only when they are prose *and* something else survives; the drop refuses to leave ≤1 item, because a task scored on one prose item is passable on a token. What remains is graded by literal equality, and no answer reproduces a paragraph. | Either rewrite the question so the field has a controlled vocabulary, or accept that the task's ceiling is below 1.0 and record that ceiling. |
| **A ground-truth entry defect.** GT `mC>` vs answer `mC` (`sv-chem-crystal-0014` / `crystal_system_and_bravais_lattice`, both arms). | The GT is the malformed one. Every rule that rescues `mC>` also makes `-5` equal `5` and `50%` equal `50`. | Fix the ground truth. That is a question fix, not a scoring fix — the comparator deliberately does not compensate. |

**The alias remedy has no vehicle today.** `accepted` is read from the harness's
task spec (`sci3d_bench/task_schema.py`), not from a delivered contract: the 250
bundle `task.json` files carry ten keys — `question`, `ground_truth`,
`answer_shape`, `assets` and the rest — and none of them is an alias list, while
the field-level `field_match` the design spec proposes is implemented nowhere.
So "add an alias" is two jobs, the contract data and the path that carries it
into the scorer, and the second one has no owner yet. That finding is written up
in §7.3 of `freetext_rework_proposal.md` —
`_ops/review-v11-20260929/freetext_rework_proposal.md` in the ScienceVision
worktree §3 names — which is also where the per-item actions and their trial
costs live; a row of that table that says *alias* does not land until someone
owns the carrier.

The distinction that keeps this from stopping work that is possible: the scorer
already serves a second kind of alias, **derived mechanically from the ground
truth's own spelling** (`derive_aliases`, routing line 13) — a multiple-choice
letter for the full option text, and a trailing parenthetical dropped when one
token remains. Those need no contract change, they are what §2's
`canonical_string_in_set` row counts, and they are not what this table's alias
remedy refers to. A *declared* alternative for a value no rule can derive is
the remedy here, and it is the one still waiting for its vehicle.

The file groups its entries under `WRONG`, `NEAR` and `NORM`: the clause
citations and `ZN 201` sit in `WRONG`, the reversed terminus, `X`/`X-axis` and
the prose answers in `NEAR`, and the two spelling pairs in `NORM` — together
with the `mC>` ground-truth defect, which is a question fix wearing a spelling
rule's clothes. Read your task's bucket before proposing a rule; the proposal
table that assigns each entry an action (alias, question rewrite, or an accepted
ceiling below 1.0) is written separately, it is the artifact that requires a
signature, and it is where the per-item trial counts come from.

## 5. Changing the normalisation: what you may do, and what you must show

**You may change the rules.** Nothing here freezes `_canonical_string`, and a
future change is expected — the floor in §1 is a list of decisions, not a law of
nature. What must not vary is the evidence that accompanies it.

**Before any change to the normalisation lands, show all four:**

1. Two replays over the delivery, one per checkout, and a diff of them:

   ```bash
   git worktree add /tmp/sv-preF5 <revision-before-the-change>

   .venv/bin/python tools/replay_scoring.py --bundle ~/sv-freetext/bundle \
     --runs /Users/sue/Research/SV-data/delivery/v11-250-20260929 \
     --scorer-root /tmp/sv-preF5 --out /tmp/replay-preF5.jsonl
   .venv/bin/python tools/replay_scoring.py --bundle ~/sv-freetext/bundle \
     --runs /Users/sue/Research/SV-data/delivery/v11-250-20260929 \
     --scorer-root . --out /tmp/replay-postF5.jsonl
   .venv/bin/python tools/diff_replay_rows.py --old /tmp/replay-preF5.jsonl \
     --new /tmp/replay-postF5.jsonl --out ~/sv-freetext/normalisation_delta.json
   ```

   Why the two roots: §3.4. `--scorer-root` is resolved against your current
   directory, so `--scorer-root .` means "the checkout I am standing in". The
   diff exits 0 on a clean change, 1 on a regression or a rubric-membership
   change, and 2 when it refuses to produce a result at all (missing input, a
   write inside the delivery, one checkout on both sides, or two *deliveries* on
   the two sides) — a refusal, not a verdict. The last two are named on purpose
   and the guard reads the rows' own provenance: one checkout on both sides is a
   switch that never moved, and two deliveries on the two sides is a comparison
   whose verdict keys are equal while the answers behind them are not. Each has
   an explicit opt-out (`--allow-same-root`, `--allow-different-runs`) for the
   case where that is what you meant — including the deliberately mixed case
   where only one side records a delivery, which is refused rather than assumed
   to match.

2. `regressions` empty, and every entry of `moved` read by eye against its
   question and its ground truth. The delivery is read-only; both tools refuse
   to write inside it.

3. A pair-level check over the compared values, not just the verdicts: a
   verdict can hide a narrowing behind a field that was already failing.

4. For any rule that touches a comparator with a **zero denominator** in this
   corpus (§3.3), the replay diff will report `moved=0` and prove nothing —
   say so, and carry the class-split test instead. The same applies to a rule
   whose surface is a shape the corpus does not contain. Read §3.3 per
   comparator before taking that as "the corpus never reaches this one": it is
   true of `canonical_space_group_equal` and `canonical_asset_id_equal`, and
   false of `canonical_residue_equal`, which four stored pairs reach once the
   current dispatch is applied to them. For those four the evidence is the pair
   itself — drive it through the comparator in-process, as §3.1's parser pairs
   are driven — not the replay: the replay scores each trial under the
   comparator its stored rubric carries, so it cannot see a change to this
   comparator at all.

**What you may do differently, and what you must then justify.** You may pin a
comparator to the old normalisation instead of teaching it the new rules (that
is what the three structure parsers do, §3.1) — then justify it with the
narrowing pairs it would otherwise split, as that pin does. You may also leave
a pair unequal on purpose when no rule can hold it and the alternative
trade is worse (§1's `the_second_render` case) — then name the pair and say
which side you chose, because the cost is a correct answer graded wrong.

**What you may not do.** Change a delivered ground truth. That is a question
fix; it changes what the task asks and needs its owner's signature, which is why
§4 ends in a proposal table rather than a patch.
