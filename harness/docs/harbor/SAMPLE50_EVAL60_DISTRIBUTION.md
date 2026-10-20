# scale-v1 sample50 `cc` eval60 distribution

Source: `docs/harbor/SAMPLE50_REPORT.json` (`trials.cc` with non-null
`reward`) joined with
`QA-Gen/tasks/scale-v1-sample50/ScienceVision_scale-v1-sample50_final_tasks.jsonl`.

This is the distribution of the **60 scored trials** from the partial `cc`
baseline run. The run has 63 trial records total: 60 scored, plus 3
`AgentSetupTimeoutError` records with no reward.

## Answer

Yes, every sample50 sub-domain/task family was sampled in the 60 scored trials.
Coverage is shallow and uneven, so this should be treated as a partial-run
coverage check, not as a balanced sub-domain evaluation.

## By Domain

| domain | scored | share |
|---|---:|---:|
| medical | 24 | 40.0% |
| chemistry | 20 | 33.3% |
| biology | 16 | 26.7% |
| total | 60 | 100.0% |

The original sample50 pool was balanced at 50 per top-level domain. This eval
slice is not: medical is overrepresented and biology is underrepresented.

## By Sub-Domain / Task Family

| sub-domain | original sample50 quota | trial records | scored | scored / quota | agent timeouts | setup timeouts | mean reward |
|---|---:|---:|---:|---:|---:|---:|---:|
| `med_ct_lesion` | 35 | 17 | 16 | 45.7% | 16 | 1 | 0.110 |
| `med_mri_anatomy` | 15 | 8 | 8 | 53.3% | 2 | 0 | 0.666 |
| `bio_collision_check` | 20 | 8 | 8 | 40.0% | 4 | 0 | 0.393 |
| `bio_docking_plausibility` | 20 | 7 | 6 | 30.0% | 2 | 1 | 0.542 |
| `bio_multi_structure_compare` | 10 | 2 | 2 | 20.0% | 1 | 0 | 0.251 |
| `chem_binding_geometry` | 16 | 9 | 8 | 50.0% | 3 | 1 | 0.239 |
| `chem_conformation` | 15 | 6 | 6 | 40.0% | 1 | 0 | 0.250 |
| `chem_crystallography` | 19 | 6 | 6 | 31.6% | 0 | 0 | 0.810 |

Notes:

- `trial records` includes scored records and no-reward setup-timeout records.
- `agent timeouts` are scored records with timeout-floor rewards.
- `setup timeouts` are the 3 no-reward records excluded from the 60 scored
  denominator.

## By V-Class

| v_class | scored |
|---|---:|
| V1 | 16 |
| V2 | 8 |
| V3 | 14 |
| V4 | 2 |
| V6 | 20 |

V4 is barely represented in this partial slice.

## By Answer Kind

| answer kind | scored |
|---|---:|
| structured | 28 |
| categorical | 22 |
| binary | 8 |
| relation | 2 |

## Takeaways

1. All 8 sub-domains are present in the 60 scored trials.
2. The distribution is not balanced relative to the intended sample50 pool.
3. `med_ct_lesion` dominates the timeout profile: 16 scored records, all 16
   marked `AgentTimeoutError`, with mean reward 0.110.
4. `chem_crystallography` is the cleanest and strongest sub-domain in this
   partial run: 6/6 scored, no timeout records, mean reward 0.810.
5. `bio_multi_structure_compare` and V4 are too thinly represented to support a
   meaningful sub-domain conclusion.
