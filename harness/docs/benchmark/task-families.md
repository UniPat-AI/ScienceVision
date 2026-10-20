# Task families

> Status: **draft / scaffold**. Bodies will be adapted from `docs/BabyVision-to-ScienceVision-Continuity.md` §3–§4 (internal narrative removed).

## V2 — cross-section / topology

Geometric reasoning about a 3D object inferred from one or more 2D views. Cannot be shortcut by code because the answer requires reasoning about the 3D structure.

## V3 — segmentation / referring

Identify or segment a target in a 3D scene given a referring expression. Pixel-level ground truth (SSIM / LPIPS) defends against LLM-as-judge.

## V4 — manipulation / counterfactual

Counterfactual reasoning: given an operation (rotate, slice, deform), predict the result. Geometry-grounded ground truth.

## V6 — multi-step reasoning

Multi-tool, multi-step trace. Evaluated on both final answer and trace fidelity.

## Interactive Physics

Force control, MPM fracture, MPM granular cascade, FEM cloth. P1/P2 difficulty. Genesis physics engine in the loop. Models discover task answers by acting on objects in the environment, not just viewing them.

## Spatial Vision

Medical imaging tasks (e.g. M3D, IXI brain) with geometric ground truth (distance, angle, topology count, volume).

---

Each family above will get a dedicated subsection with: sample task, evaluation metric, dataset pointer, baseline numbers (when available).
