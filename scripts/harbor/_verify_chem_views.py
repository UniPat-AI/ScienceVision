"""Render all 10 EV2 chem assets; assert each PNG is non-blank."""
import base64, io, sys
from pathlib import Path
import numpy as np
from PIL import Image
from sci3d_agent.mcp.tools._render.molecule import handle_molecule_view_sheet

ASSETS = [
    ("mol",   "crawl_data/biology/small_molecules_derived/3672_ibuprofen.mol"),
    ("mol",   "crawl_data/biology/small_molecules_derived/5904_penicillin_g.mol"),
    ("xyz",   "crawl_data/biology/qm_conformers/qm9_99186.xyz"),
    ("xyz",   "crawl_data/biology/qm_conformers/qm9_99795.xyz"),
    ("cif",   "crawl_data/biology/cod_cif/cod_1501802.cif"),
    ("cif",   "crawl_data/biology/cod_cif/cod_1503705.cif"),
    ("mmcif", "crawl_data/biology/rcsb_pdb_mmcif/1HVR.cif"),
    ("mmcif", "crawl_data/biology/rcsb_pdb_mmcif/2RH1.cif"),
    ("mmcif", "crawl_data/biology/rcsb_pdb_mmcif/4EY7.cif"),
    ("mmcif", "crawl_data/biology/rcsb_pdb_mmcif/6LU7.cif"),
]

def nonblank(env) -> bool:
    b64 = env.get("image_png_base64")
    raw = base64.b64decode(b64) if b64 else Path(env["cache_path"]).read_bytes()
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("L"))
    return float(arr.std()) > 3.0     # not a flat fill

def main() -> int:
    bad = []
    for fmt, rel in ASSETS:
        env = handle_molecule_view_sheet({"path": rel, "mol_format": fmt, "size_per_tile": 256})
        ok = "error" not in env and nonblank(env)
        print(f"{'OK ' if ok else 'BAD'} {fmt:5} {rel}  {env.get('error','')}")
        if not ok:
            bad.append(rel)
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
