# QA-Gen Harbor run leaderboard

- runs: 45  (completed: 45)

## Mean by harness / model (reward · content · process)

| harness / model | reward | content | process |
|-----------------|--------|---------|---------|
| claude-code-mcp / claude-opus-4-8 | 0.587 | 0.700 | 0.735 |
| claude-code / claude-opus-4-8 | 0.564 | 0.620 | 0.660 |
| claude-code-2d / claude-opus-4-8 | 0.295 | 0.250 | 0.403 |

## Mean reward by harness / domain

| harness / domain | mean reward |
|------------------|-------------|
| claude-code / biology | 0.591 |
| claude-code / chemistry | 0.664 |
| claude-code / medical | 0.261 |
| claude-code-2d / biology | 0.144 |
| claude-code-2d / chemistry | 0.273 |
| claude-code-2d / medical | 0.556 |
| claude-code-mcp / biology | 0.445 |
| claude-code-mcp / chemistry | 0.690 |
| claude-code-mcp / medical | 0.500 |

## Per-task

| harness | model | task | domain | v_class | status | reward | exact | content | process |
|---------|-------|------|--------|---------|--------|--------|-------|---------|---------|
| claude-code | claude-opus-4-8 | sv-chem-binding-pose-05 | chemistry | V3 | ok | 0.848 | 0.000 | 1.000 | 0.970 |
| claude-code | claude-opus-4-8 | sv-biol-docking-pose-plausibility-01 | biology | V3 | ok | 0.847 | 0.000 | 1.000 | 0.940 |
| claude-code | claude-opus-4-8 | sv-biol-molecular-collision-check-03 | biology | V2 | ok | 0.847 | 0.000 | 1.000 | 0.940 |
| claude-code | claude-opus-4-8 | sv-chem-conformation-01 | chemistry | V2 | ok | 0.844 | 0.000 | 1.000 | 0.880 |
| claude-code | claude-opus-4-8 | sv-chem-chirality-08 | chemistry | V2 | ok | 0.836 | 0.000 | 1.000 | 0.720 |
| claude-code | claude-opus-4-8 | sv-chem-chirality-07 | chemistry | V2 | ok | 0.827 | 0.000 | 1.000 | 0.550 |
| claude-code | claude-opus-4-8 | sv-chem-conformation-02 | chemistry | V4 | ok | 0.827 | 0.000 | 1.000 | 0.550 |
| claude-code | claude-opus-4-8 | sv-chem-crystal-packing-04 | chemistry | V6 | ok | 0.811 | 0.000 | 0.950 | 0.920 |
| claude-code | claude-opus-4-8 | sv-medi-anatomical-region-01 | medical | V3 | ok | 0.596 | 0.000 | 0.650 | 0.830 |
| claude-code | claude-opus-4-8 | sv-biol-docking-pose-plausibility-02 | biology | V4 | ok | 0.421 | 0.000 | 0.400 | 0.820 |
| claude-code | claude-opus-4-8 | sv-biol-molecular-collision-check-04 | biology | V3 | ok | 0.248 | 0.000 | 0.150 | 0.860 |
| claude-code | claude-opus-4-8 | sv-chem-crystal-packing-03 | chemistry | V3 | ok | 0.215 | 0.000 | 0.100 | 0.900 |
| claude-code | claude-opus-4-8 | sv-medi-mri-arbitrary-plane-02 | medical | V1 | ok | 0.101 | 0.000 | 0.000 | 0.020 |
| claude-code | claude-opus-4-8 | sv-chem-optimization-vector-06 | chemistry | V4 | ok | 0.100 | 0.000 | 0.000 | 0.000 |
| claude-code | claude-opus-4-8 | sv-medi-lesion-3d-extent-03 | medical | V6 | ok | 0.085 | 0.000 | 0.050 | 0.000 |
| claude-code-2d | claude-opus-4-8 | sv-chem-binding-pose-05 | chemistry | V3 | ok | 0.847 | 0.000 | 1.000 | 0.950 |
| claude-code-2d | claude-opus-4-8 | sv-medi-mri-arbitrary-plane-02 | medical | V1 | ok | 0.836 | 0.000 | 1.000 | 0.720 |
| claude-code-2d | claude-opus-4-8 | sv-chem-chirality-08 | chemistry | V2 | ok | 0.627 | 0.000 | 0.750 | 0.050 |
| claude-code-2d | claude-opus-4-8 | sv-medi-anatomical-region-01 | medical | V3 | ok | 0.620 | 0.000 | 0.700 | 0.600 |
| claude-code-2d | claude-opus-4-8 | sv-medi-lesion-3d-extent-03 | medical | V6 | ok | 0.211 | 0.000 | 0.100 | 0.820 |
| claude-code-2d | claude-opus-4-8 | sv-biol-molecular-collision-check-03 | biology | V2 | ok | 0.200 | 0.000 | 0.100 | 0.600 |
| claude-code-2d | claude-opus-4-8 | sv-chem-crystal-packing-03 | chemistry | V3 | ok | 0.177 | 0.000 | 0.100 | 0.150 |
| claude-code-2d | claude-opus-4-8 | sv-biol-docking-pose-plausibility-02 | biology | V4 | ok | 0.130 | 0.000 | 0.000 | 0.600 |
| claude-code-2d | claude-opus-4-8 | sv-biol-docking-pose-plausibility-01 | biology | V3 | ok | 0.130 | 0.000 | 0.000 | 0.600 |
| claude-code-2d | claude-opus-4-8 | sv-chem-chirality-07 | chemistry | V2 | ok | 0.120 | 0.000 | 0.000 | 0.400 |
| claude-code-2d | claude-opus-4-8 | sv-biol-molecular-collision-check-04 | biology | V3 | ok | 0.117 | 0.000 | 0.000 | 0.350 |
| claude-code-2d | claude-opus-4-8 | sv-chem-crystal-packing-04 | chemistry | V6 | ok | 0.107 | 0.000 | 0.000 | 0.150 |
| claude-code-2d | claude-opus-4-8 | sv-chem-conformation-02 | chemistry | V4 | ok | 0.102 | 0.000 | 0.000 | 0.050 |
| claude-code-2d | claude-opus-4-8 | sv-chem-conformation-01 | chemistry | V2 | ok | 0.100 | 0.000 | 0.000 | 0.000 |
| claude-code-2d | claude-opus-4-8 | sv-chem-optimization-vector-06 | chemistry | V4 | ok | 0.100 | 0.000 | 0.000 | 0.000 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-binding-pose-05 | chemistry | V3 | ok | 0.848 | 0.000 | 1.000 | 0.970 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-crystal-packing-04 | chemistry | V6 | ok | 0.846 | 0.000 | 1.000 | 0.920 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-conformation-01 | chemistry | V2 | ok | 0.836 | 0.000 | 1.000 | 0.720 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-crystal-packing-03 | chemistry | V3 | ok | 0.810 | 0.000 | 0.950 | 0.900 |
| claude-code-mcp | claude-opus-4-8 | sv-medi-mri-arbitrary-plane-02 | medical | V1 | ok | 0.761 | 0.000 | 0.950 | 0.920 |
| claude-code-mcp | claude-opus-4-8 | sv-biol-molecular-collision-check-03 | biology | V2 | ok | 0.746 | 0.000 | 1.000 | 0.920 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-chirality-07 | chemistry | V2 | ok | 0.739 | 0.000 | 1.000 | 0.780 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-conformation-02 | chemistry | V4 | ok | 0.717 | 0.000 | 1.000 | 0.350 |
| claude-code-mcp | claude-opus-4-8 | sv-biol-docking-pose-plausibility-01 | biology | V3 | ok | 0.710 | 0.000 | 0.950 | 0.900 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-chirality-08 | chemistry | V2 | ok | 0.626 | 0.000 | 0.700 | 0.720 |
| claude-code-mcp | claude-opus-4-8 | sv-medi-anatomical-region-01 | medical | V3 | ok | 0.539 | 0.000 | 0.650 | 0.680 |
| claude-code-mcp | claude-opus-4-8 | sv-biol-molecular-collision-check-04 | biology | V3 | ok | 0.286 | 0.000 | 0.200 | 0.920 |
| claude-code-mcp | claude-opus-4-8 | sv-medi-lesion-3d-extent-03 | medical | V6 | ok | 0.200 | 0.000 | 0.100 | 0.600 |
| claude-code-mcp | claude-opus-4-8 | sv-chem-optimization-vector-06 | chemistry | V4 | ok | 0.100 | 0.000 | 0.000 | 0.000 |
| claude-code-mcp | claude-opus-4-8 | sv-biol-docking-pose-plausibility-02 | biology | V4 | ok | 0.036 | 0.000 | 0.000 | 0.720 |
