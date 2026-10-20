## ev2 three-way baseline — reward / content / process

| baseline | n | reward | content | process | tiers (rerun/rescored/degraded) |
|---|--:|--:|--:|--:|--:|
| Claude Code (plain, raw 3D file, no MCP) | 15 | 0.564 | 0.620 | 0.660 | 0/15/0 |
| Claude Code + 2D multiview (pre-rendered PNGs only) | 15 | 0.295 | 0.250 | 0.403 | 0/15/0 |
| Claude Code + MCP (raw 3D file + sci3d 3D tools) | 15 | 0.587 | 0.700 | 0.735 | 0/15/0 |

### per-domain mean reward

| domain | cc | cc2d | ccmcp |
|---|---|---|---|
| biology | 0.591 | 0.144 | 0.445 |
| chemistry | 0.664 | 0.273 | 0.690 |
| medical | 0.261 | 0.556 | 0.500 |

### per-task reward

| task | cc | cc2d | ccmcp |
|---|---|---|---|
| sv-biol-docking-pose-plausibility-01 | 0.847 | 0.130 | 0.710 |
| sv-biol-docking-pose-plausibility-02 | 0.421 | 0.130 | 0.036 |
| sv-biol-molecular-collision-check-03 | 0.847 | 0.200 | 0.746 |
| sv-biol-molecular-collision-check-04 | 0.248 | 0.117 | 0.286 |
| sv-chem-binding-pose-05 | 0.848 | 0.847 | 0.848 |
| sv-chem-chirality-07 | 0.827 | 0.120 | 0.739 |
| sv-chem-chirality-08 | 0.836 | 0.627 | 0.626 |
| sv-chem-conformation-01 | 0.844 | 0.100 | 0.836 |
| sv-chem-conformation-02 | 0.827 | 0.102 | 0.717 |
| sv-chem-crystal-packing-03 | 0.215 | 0.177 | 0.810 |
| sv-chem-crystal-packing-04 | 0.811 | 0.107 | 0.846 |
| sv-chem-optimization-vector-06 | 0.100 | 0.100 | 0.100 |
| sv-medi-anatomical-region-01 | 0.596 | 0.620 | 0.539 |
| sv-medi-lesion-3d-extent-03 | 0.085 | 0.211 | 0.200 |
| sv-medi-mri-arbitrary-plane-02 | 0.101 | 0.836 | 0.761 |

### per-task content

| task | cc | cc2d | ccmcp |
|---|---|---|---|
| sv-biol-docking-pose-plausibility-01 | 1.000 | 0.000 | 0.950 |
| sv-biol-docking-pose-plausibility-02 | 0.400 | 0.000 | 0.000 |
| sv-biol-molecular-collision-check-03 | 1.000 | 0.100 | 1.000 |
| sv-biol-molecular-collision-check-04 | 0.150 | 0.000 | 0.200 |
| sv-chem-binding-pose-05 | 1.000 | 1.000 | 1.000 |
| sv-chem-chirality-07 | 1.000 | 0.000 | 1.000 |
| sv-chem-chirality-08 | 1.000 | 0.750 | 0.700 |
| sv-chem-conformation-01 | 1.000 | 0.000 | 1.000 |
| sv-chem-conformation-02 | 1.000 | 0.000 | 1.000 |
| sv-chem-crystal-packing-03 | 0.100 | 0.100 | 0.950 |
| sv-chem-crystal-packing-04 | 0.950 | 0.000 | 1.000 |
| sv-chem-optimization-vector-06 | 0.000 | 0.000 | 0.000 |
| sv-medi-anatomical-region-01 | 0.650 | 0.700 | 0.650 |
| sv-medi-lesion-3d-extent-03 | 0.050 | 0.100 | 0.100 |
| sv-medi-mri-arbitrary-plane-02 | 0.000 | 1.000 | 0.950 |

### per-task process

| task | cc | cc2d | ccmcp |
|---|---|---|---|
| sv-biol-docking-pose-plausibility-01 | 0.940 | 0.600 | 0.900 |
| sv-biol-docking-pose-plausibility-02 | 0.820 | 0.600 | 0.720 |
| sv-biol-molecular-collision-check-03 | 0.940 | 0.600 | 0.920 |
| sv-biol-molecular-collision-check-04 | 0.860 | 0.350 | 0.920 |
| sv-chem-binding-pose-05 | 0.970 | 0.950 | 0.970 |
| sv-chem-chirality-07 | 0.550 | 0.400 | 0.780 |
| sv-chem-chirality-08 | 0.720 | 0.050 | 0.720 |
| sv-chem-conformation-01 | 0.880 | 0.000 | 0.720 |
| sv-chem-conformation-02 | 0.550 | 0.050 | 0.350 |
| sv-chem-crystal-packing-03 | 0.900 | 0.150 | 0.900 |
| sv-chem-crystal-packing-04 | 0.920 | 0.150 | 0.920 |
| sv-chem-optimization-vector-06 | 0.000 | 0.000 | 0.000 |
| sv-medi-anatomical-region-01 | 0.830 | 0.600 | 0.680 |
| sv-medi-lesion-3d-extent-03 | 0.000 | 0.820 | 0.600 |
| sv-medi-mri-arbitrary-plane-02 | 0.020 | 0.720 | 0.920 |
