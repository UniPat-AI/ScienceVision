#!/usr/bin/env bash
# Fetch the medical_spatial demo NIfTI volume + mask used by the viewer.
#
# Preferred source (stage 1.5+): public ModelScope mirror
#   tianyi3unipat/BabyVision-v2-Datasets
#   files: medical/assets/{image,mask}.nii.gz
# Fallback (stage 1): sibling ScienceVision checkout containing
#   datasets/_derived/m3d-seg-0005/0/{image,mask}.nii.gz
#
# The demo content is M3D-RefSeg case s0132 — an abdominal CT volume with a
# segmentation mask (label 1 = mild fatty liver, 1.5 mm isotropic).
#
# Env knobs:
#   SCIENCEVISION_DIR=/path/to/checkout   -- override fallback root
#   MODELSCOPE_TOKEN=<token>              -- only needed if the dataset goes private
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MS_REPO="tianyi3unipat/BabyVision-v2-Datasets"
MS_IMG_REL="medical/assets/image.nii.gz"
MS_MASK_REL="medical/assets/mask.nii.gz"

DEFAULT_NO_HYPHEN="$HERE/../../../../ScienceVision"
DEFAULT_HYPHEN="$HERE/../../../../Science-Vision"

fetch_via_modelscope() {
    command -v python3 >/dev/null 2>&1 || return 1
    python3 - "$MS_REPO" "$MS_IMG_REL" "$MS_MASK_REL" "$HERE" <<'PY'
import os, sys, shutil
repo, img_rel, mask_rel, dest = sys.argv[1:]
try:
    from modelscope.hub.snapshot_download import snapshot_download
except ImportError:
    sys.stderr.write("modelscope SDK not installed (pip install modelscope)\n")
    sys.exit(2)
tok = os.environ.get("MODELSCOPE_TOKEN") or os.environ.get("MODELSCOPE_API_TOKEN")
kwargs = dict(repo_type="dataset", allow_patterns=[img_rel, mask_rel])
if tok:
    os.environ.setdefault("MODELSCOPE_API_TOKEN", tok)
snap = snapshot_download(repo, **kwargs)
for rel, out in ((img_rel, "image.nii.gz"), (mask_rel, "mask.nii.gz")):
    src = os.path.join(snap, rel)
    if not os.path.exists(src):
        sys.stderr.write(f"snapshot missing {rel}\n"); sys.exit(3)
    shutil.copyfile(src, os.path.join(dest, out))
print("[fetch] ok via modelscope")
PY
}

fetch_via_sciencevision() {
    local src_root="${SCIENCEVISION_DIR:-}"
    if [[ -z "$src_root" ]]; then
        if   [[ -d "$DEFAULT_NO_HYPHEN/datasets/_derived/m3d-seg-0005/0" ]]; then src_root="$DEFAULT_NO_HYPHEN"
        elif [[ -d "$DEFAULT_HYPHEN/datasets/_derived/m3d-seg-0005/0"   ]]; then src_root="$DEFAULT_HYPHEN"
        else return 1
        fi
    fi
    local case_dir="$src_root/datasets/_derived/m3d-seg-0005/0"
    [[ -f "$case_dir/image.nii.gz" && -f "$case_dir/mask.nii.gz" ]] || return 1
    cp "$case_dir/image.nii.gz" "$HERE/image.nii.gz"
    cp "$case_dir/mask.nii.gz"  "$HERE/mask.nii.gz"
    echo "[fetch] ok via ScienceVision checkout at $case_dir"
}

if fetch_via_modelscope; then :
elif fetch_via_sciencevision; then :
else
    echo "error: could not fetch demo data." >&2
    echo "       Either install modelscope (pip install modelscope) so this script can pull" >&2
    echo "       from $MS_REPO," >&2
    echo "       or clone SueMarsR/Science-Vision next to this repo (as ScienceVision/ or" >&2
    echo "       Science-Vision/), or set SCIENCEVISION_DIR=/path/to/your/checkout." >&2
    exit 2
fi

ls -lh "$HERE"/*.nii.gz
echo "[fetch] done"
