"""Mirror the vendor JS/CSS our viewers import from public CDNs.

Run once at planning time (and again whenever VENDOR_SPEC changes) to
populate viewer/shared/vendor/ so that nginx (with U3 immutable cache)
can serve them with zero CDN dependency.

Idempotent: re-running skips files already on disk. No version detection,
no auto-upgrade — every URL is pinned by version in the path.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]

# (lib, version, url, local_filename)
VENDOR_SPEC: list[tuple[str, str, str, str]] = [
    # three.js — used by gltf, urdf, pointcloud, occt, saturn, seed3d_pair
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/build/three.module.js", "build/three.module.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js", "examples/jsm/controls/OrbitControls.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/controls/TrackballControls.js", "examples/jsm/controls/TrackballControls.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/environments/RoomEnvironment.js", "examples/jsm/environments/RoomEnvironment.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/loaders/GLTFLoader.js", "examples/jsm/loaders/GLTFLoader.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/loaders/PLYLoader.js", "examples/jsm/loaders/PLYLoader.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/loaders/STLLoader.js", "examples/jsm/loaders/STLLoader.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/loaders/OBJLoader.js", "examples/jsm/loaders/OBJLoader.js"),
    ("three", "0.160.0", "https://unpkg.com/three@0.160.0/examples/jsm/loaders/MTLLoader.js", "examples/jsm/loaders/MTLLoader.js"),

    # molstar — molstar.html
    ("molstar", "4.5.0", "https://unpkg.com/molstar@4.5.0/build/viewer/molstar.css", "build/viewer/molstar.css"),
    ("molstar", "4.5.0", "https://unpkg.com/molstar@4.5.0/build/viewer/molstar.js", "build/viewer/molstar.js"),

    # prism.js — mpbctl, usda
    ("prismjs", "1.29.0", "https://unpkg.com/prismjs@1.29.0/themes/prism-tomorrow.css", "themes/prism-tomorrow.css"),
    ("prismjs", "1.29.0", "https://unpkg.com/prismjs@1.29.0/components/prism-core.min.js", "components/prism-core.min.js"),
    ("prismjs", "1.29.0", "https://unpkg.com/prismjs@1.29.0/components/prism-scheme.min.js", "components/prism-scheme.min.js"),
    ("prismjs", "1.29.0", "https://unpkg.com/prismjs@1.29.0/components/prism-python.min.js", "components/prism-python.min.js"),

    # cornerstone stack — cornerstone.html
    ("cornerstone-core", "2.6.1", "https://unpkg.com/cornerstone-core@2.6.1/dist/cornerstone.min.js", "dist/cornerstone.min.js"),
    ("cornerstone-math", "0.1.10", "https://unpkg.com/cornerstone-math@0.1.10/dist/cornerstoneMath.min.js", "dist/cornerstoneMath.min.js"),
    ("dicom-parser", "1.8.21", "https://unpkg.com/dicom-parser@1.8.21/dist/dicomParser.min.js", "dist/dicomParser.min.js"),
    ("cornerstone-wado-image-loader", "4.13.2", "https://unpkg.com/cornerstone-wado-image-loader@4.13.2/dist/cornerstoneWADOImageLoader.bundle.min.js", "dist/cornerstoneWADOImageLoader.bundle.min.js"),

    # niivue — niivue.html
    ("@niivue/niivue", "0.45.0", "https://unpkg.com/@niivue/niivue@0.45.0/dist/niivue.umd.js", "dist/niivue.umd.js"),

    # jszip — usdz.html
    ("jszip", "3.10.1", "https://unpkg.com/jszip@3.10.1/dist/jszip.min.js", "dist/jszip.min.js"),

    # occt-import-js — occt.html
    ("occt-import-js", "0.0.23", "https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/occt-import-js.js", "dist/occt-import-js.js"),
    ("occt-import-js", "0.0.23", "https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/occt-import-js.wasm", "dist/occt-import-js.wasm"),
]


def local_path_for(lib: str, version: str, name: str, *, root: Path = ROOT) -> Path:
    return root / "viewer" / "shared" / "vendor" / lib / version / name


def download_if_missing(url: str, target: Path) -> str:
    if target.exists() and target.stat().st_size > 0:
        return "skip"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f"{target.name}.tmp")
    req = Request(url, headers={"User-Agent": "sciencevision-sync-vendor/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            temp_target.write_bytes(resp.read())
        temp_target.replace(target)
    except Exception:
        try:
            temp_target.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise
    return "downloaded"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Mirror viewer vendor JS/CSS")
    p.add_argument("--check", action="store_true", help="exit nonzero if any file missing")
    args = p.parse_args(argv)

    missing = 0
    downloaded = 0
    skipped = 0
    for lib, ver, url, name in VENDOR_SPEC:
        target = local_path_for(lib, ver, name)
        if args.check:
            if not target.exists() or target.stat().st_size == 0:
                print(f"MISSING {target.relative_to(ROOT)}")
                missing += 1
            continue
        action = download_if_missing(url, target)
        if action == "downloaded":
            print(f"  +  {target.relative_to(ROOT)}")
            downloaded += 1
        else:
            skipped += 1
    if args.check:
        print(f"{missing} missing")
        return 1 if missing else 0
    print(f"{downloaded} downloaded, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
