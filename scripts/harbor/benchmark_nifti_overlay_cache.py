"""Measure exact-output NIfTI overlay pair-cache speed on one source/mask pair.

Example:
    PYTHONPATH=. python -m scripts.harbor.benchmark_nifti_overlay_cache \
      --nifti-path /path/ct.nii.gz --mask-path /path/mask.nii.gz --index 61
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from sci3d_agent.mcp.tools._render import nifti


def _render(args: dict) -> bytes:
    out = nifti.handle_nifti_overlay(args)
    if "error" in out:
        raise RuntimeError(out["error"])
    return Path(out["cache_path"]).read_bytes()


def _measure(args: dict, repeats: int, cache_mib: str) -> tuple[list[float], list[str], dict[str, int]]:
    previous = os.environ.get("SV_RENDER_NIFTI_OVERLAY_CACHE_MIB")
    os.environ["SV_RENDER_NIFTI_OVERLAY_CACHE_MIB"] = cache_mib
    nifti.clear_overlay_nifti_pair_cache()
    durations: list[float] = []
    hashes: list[str] = []
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            png = _render(args)
            durations.append(time.perf_counter() - started)
            hashes.append(hashlib.sha256(png).hexdigest())
        return durations, hashes, nifti.overlay_nifti_pair_cache_stats()
    finally:
        nifti.clear_overlay_nifti_pair_cache()
        if previous is None:
            os.environ.pop("SV_RENDER_NIFTI_OVERLAY_CACHE_MIB", None)
        else:
            os.environ["SV_RENDER_NIFTI_OVERLAY_CACHE_MIB"] = previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nifti-path", required=True)
    parser.add_argument("--mask-path", required=True)
    parser.add_argument("--axis", choices=["sagittal", "coronal", "axial"], default="axial")
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--cache-mib", default="256")
    parsed = parser.parse_args()
    if parsed.repeats < 2:
        parser.error("--repeats must be at least 2")

    render_args = {
        "nifti_path": parsed.nifti_path,
        "mask_path": parsed.mask_path,
        "axis": parsed.axis,
        "index": parsed.index,
        "size": parsed.size,
        "roi_highlight": True,
    }
    uncached_times, uncached_hashes, uncached_stats = _measure(render_args, parsed.repeats, "0")
    cached_times, cached_hashes, cached_stats = _measure(render_args, parsed.repeats, parsed.cache_mib)
    if len(set(uncached_hashes + cached_hashes)) != 1:
        raise SystemExit("cached and uncached PNG bytes differ")
    report = {
        "repeats": parsed.repeats,
        "png_sha256": uncached_hashes[0],
        "uncached_total_seconds": round(sum(uncached_times), 6),
        "cached_total_seconds": round(sum(cached_times), 6),
        "cached_warm_mean_seconds": round(sum(cached_times[1:]) / (parsed.repeats - 1), 6),
        "speedup_total": round(sum(uncached_times) / max(sum(cached_times), 1e-12), 3),
        "uncached_cache_stats": uncached_stats,
        "cached_cache_stats": cached_stats,
    }
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
