# Evaluation protocol

> Status: **draft / scaffold**. The full protocol will codify the rules used inside ScienceVision for `_matched/` whitelist and judge defenses.

## Layered defense against LLM-as-judge

BabyVision-v2 follows the BabyVision v1 methodology: **executable ground truth first, LLM judge only as a tiebreaker.**

1. **Numeric / geometric truth** (preferred): distances, angles, topology counts, volumes, interference flags. Exact match or epsilon-tolerance.
2. **Pixel truth**: SSIM and LPIPS thresholds for rendered/segmented outputs.
3. **Structured-answer truth**: `\boxed{Answer}` format with strict parsing.
4. **LLM judge** (last resort): only invoked when the above three are inapplicable. Always logged for audit.

## Trace verification

For `profile: coding` and `profile: fusion`, the `tool_trace` is parsed and replayed against the MCP server to verify the agent's claimed tool calls are real and consistent with its final answer.

## Reproducibility

Every submission must include enough metadata for a third party to replay:
- Model version / commit
- Sampling parameters (temperature, top_p, seed)
- Compute environment (GPU, OS) for renderers
- Pinned dependency versions
