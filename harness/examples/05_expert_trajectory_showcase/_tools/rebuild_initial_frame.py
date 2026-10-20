#!/usr/bin/env python3
"""Reconstruct the missing "frame 0" (viewer state when the task was first opened).

The platform only captured keyframes after each operation, so frame 0 was never
recorded. This loads the same viewer page the expert saw (served locally by
showcase_viewer_server.py with production's MCP injection), with the same asset
and URL parameters the expert page builds, at the same canvas size as the
expert's first recorded screenshot, then:

  1. waits for the MCP + screenshot halves to be ready,
  2. reads `viewer_get_state` and compares it with the sealed
     `trajectory.initial_state`,
  3. captures the canvas through the production screenshot API,
  4. writes  screenshots/操作000-初始状态-重建.png  and records the
     comparison in 题目与轨迹.json -> showcase.initial_frame.

The output is a RECONSTRUCTION, not an original record; it is labelled so in
the file name and in the JSON.

    python3 rebuild_initial_frame.py --out <showcase dir> --server http://127.0.0.1:4181 [task_id ...]
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

FRAME0_NAME = "操作000-初始状态-重建.png"
MOLSTAR_SUFFIX = {".mmcif": "mmcif", ".bcif": "bcif", ".cif": "cif", ".xyz": "xyz", ".mol2": "mol2", ".mol": "mol", ".sdf": "sdf", ".pdb": "pdb"}


def viewer_url(server: str, doc: dict) -> str:
    tc = doc["task_context"]
    viewer = tc["viewer"]
    assets = tc["assets"]
    primary = next((a for a in assets if a.get("role") in (None, "primary")), assets[0])
    mask = next((a for a in assets if a.get("role") == "mask"), None)
    q = {}
    q["file"] = "/" + primary["path"].lstrip("/")
    if viewer == "molstar":
        low = primary["path"].lower()
        fmt = next((v for k, v in MOLSTAR_SUFFIX.items() if low.endswith(k)), None)
        if fmt:
            q["type"] = fmt
        q["embed"] = "expert"
    if viewer == "niivue" and mask:
        q["overlay"] = "/" + mask["path"].lstrip("/")
        mask_id = doc.get("_mask_id", mask.get("mask_id"))
        if mask_id is not None:
            q["mask_id"] = str(mask_id)
    q["sv_interaction"] = "mcp_only"
    return f"{server}/viewer/{viewer}.html?{urllib.parse.urlencode(q)}"


def stable(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def close(a, b, tol=0.02) -> bool:
    """Structural equality with a small tolerance on numbers (camera readouts)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(close(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return a == b


def diff_keys(a: dict, b: dict) -> list[str]:
    return [k for k in sorted(set(a) | set(b)) if not close(a.get(k), b.get(k))]


BENIGN_ERRORS = ("molstarvolseg", "Failed to fetch")


def rebuild(task_dir: Path, server: str, page) -> dict:
    doc_path = task_dir / "题目与轨迹.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    first = doc["showcase"]["screenshots"][0]
    width, height = int(first["width"]), int(first["height"])
    url = viewer_url(server, doc)

    page.set_viewport_size({"width": width, "height": height})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="load", timeout=120_000)
    page.wait_for_function(
        "() => window.ScienceVisionViewerMCP && window.ScienceVisionViewerScreenshot", timeout=60_000)
    page.evaluate("() => window.ScienceVisionViewerMCP.whenReady()")
    page.evaluate("() => window.ScienceVisionViewerScreenshot.whenReady()")
    # Let the engine settle its first frame (Mol* cartoons, NiiVue volume upload).
    page.wait_for_timeout(4000)
    # The viewer page wraps its canvas in a bordered stage, so the drawing
    # surface comes out a few px smaller than the window. Grow the window by the
    # delta so the canvas equals the expert's recorded keyframe size exactly
    # (Mol*'s zoom readout depends on the fitted radius, which depends on this).
    measured = page.evaluate("() => { const c = document.querySelector('canvas'); const r = c ? c.getBoundingClientRect() : null; return r ? [Math.round(r.width), Math.round(r.height)] : null; }")
    if measured and (measured[0], measured[1]) != (width, height):
        page.set_viewport_size({"width": width + (width - measured[0]), "height": height + (height - measured[1])})
        page.wait_for_timeout(1500)
    def get_state():
        raw = page.evaluate("async () => await window.ScienceVisionViewerMCP.callTool('viewer_get_state', {})")
        st = raw
        for key in ("state", "result"):
            if isinstance(st, dict) and isinstance(st.get(key), dict) and "viewer" not in st:
                st = st[key]
        return st if isinstance(st, dict) else {}

    state = get_state()
    expected = doc["trajectory"]["initial_state"]
    # --- Align the camera to the RECORDED initial state. The viewers' "fitted
    # view" formula changed after these tasks were recorded, so a fresh load can
    # sit nearer/farther than the expert's first frame. The sealed initial_state
    # carries the zoom (and, for glTF, the camera position), so one absolute
    # viewer_zoom call reproduces the recorded distance. This is the same
    # operation replay uses; nothing is invented.
    applied = []
    exp_cam, obs_cam = expected.get("camera") or {}, state.get("camera") or {}
    if isinstance(exp_cam, dict) and exp_cam.get("zoom") is not None and isinstance(obs_cam, dict):
        target_zoom = float(exp_cam["zoom"])
        if exp_cam.get("position") and exp_cam.get("target") and obs_cam.get("position") and obs_cam.get("target"):
            import math
            ratio = math.dist(obs_cam["position"], obs_cam["target"]) / max(1e-9, math.dist(exp_cam["position"], exp_cam["target"]))
            target_zoom = float(obs_cam.get("zoom") or 1.0) * ratio
        needs = not close(obs_cam.get("zoom"), target_zoom) or (
            exp_cam.get("position") and not close(exp_cam.get("position"), obs_cam.get("position"))
        )
        if needs:
            page.evaluate("async z => await window.ScienceVisionViewerMCP.callTool('viewer_zoom', {zoom: z}, {replay: true})", round(target_zoom, 6))
            applied.append({"tool": "viewer_zoom", "input": {"zoom": round(target_zoom, 6)}, "reason": "对齐到记录的 initial_state.camera"})
            page.wait_for_timeout(1500)
            state = get_state()
    data_url = page.evaluate(
        """async () => {
            const blob = await window.ScienceVisionViewerScreenshot.captureScreenshot();
            const buf = await blob.arrayBuffer();
            let s = ''; const bytes = new Uint8Array(buf);
            for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
            return 'data:image/png;base64,' + btoa(s);
        }"""
    )
    png = base64.b64decode(data_url.split(",", 1)[1])
    rel = f"screenshots/{FRAME0_NAME}"
    (task_dir / rel).write_bytes(png)
    w = int.from_bytes(png[16:20], "big"); h = int.from_bytes(png[20:24], "big")

    mismatched = diff_keys(expected, state or {}) if isinstance(state, dict) else ["<no state>"]
    obs_cam_final = state.get("camera") or {}
    if expected.get("camera") is None:
        camera_match = None
    elif isinstance(exp_cam, dict) and exp_cam.get("position"):
        # glTF reports an absolute camera. Judge by where the camera actually is
        # (position/target/up + angles); the `zoom` readout is relative to a
        # "fitted view" baseline that changed after these tasks were recorded.
        camera_match = all(
            close(exp_cam.get(k), obs_cam_final.get(k), tol=0.03)
            for k in ("position", "target", "up", "azimuth_deg", "elevation_deg", "pan_right", "pan_up")
        )
    else:
        camera_match = close(expected.get("camera"), obs_cam_final)
    doc["showcase"]["initial_frame"] = {
        "file": rel,
        "kind": "reconstruction",
        "note": "平台未记录第 0 帧。此图由同一查看器引擎 + 同一素材 + 同一 URL 参数在本地重新渲染并用平台截图 API 捕获，画幅取专家第一张截图的尺寸。它不是原始记录。",
        "rebuilt_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "viewer_url_params": url.split("?", 1)[1],
        "canvas": {"width": w, "height": h, "matched_first_keyframe": (w, h) == (width, height)},
        "alignment_applied": applied,
        "state_check": {
            "expected": "trajectory.initial_state",
            "observed": state,
            "camera_match": camera_match,
            "other_differences": [k for k in mismatched if k != "camera"],
            "other_differences_note": "camera 以外的差异来自查看器 adapter 版本演进（新版本多报了 chains / mask_id 等字段，旧版本对 .xyz 少报了 structures；glTF 的 zoom 读数基准在记录之后改过，但相机位置已按记录对齐），不代表画面不同。",
            "verdict": "camera_match" if camera_match else ("no_camera_in_state" if camera_match is None else "camera_differs"),
        },
        "page_errors": [e for e in errors if not any(b in e for b in BENIGN_ERRORS)][:5],
    }
    doc_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (task_dir / "data.js").write_text("window.SV_CASE = " + json.dumps(doc, ensure_ascii=False) + ";\n", encoding="utf-8")
    # Tell the README the frame now exists (as a reconstruction).
    readme = task_dir / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        verdict = doc["showcase"]["initial_frame"]["state_check"]["verdict"]
        note = (
            f"**第 0 帧截图：平台没有记录，已用同一引擎重建** → `{rel}`。"
            f"重建方法：同一查看器 + 同一素材 + 同一 URL 参数在本地重新渲染，画幅取专家第一张截图的尺寸，"
            f"相机按 `trajectory.initial_state.camera` 对齐（校验结果 `{verdict}`），再用平台截图 API 捕获。"
            f"它是重建图不是原始记录，对外使用时请标注。关键帧从第 1 步操作后才开始拍；"
        )
        text = re.sub(r"\*\*第 0 帧截图：平台没有记录\*\*。关键帧从第 1 步操作后才开始拍；", note, text, count=1)
        text = re.sub(r"（校验结果 `[a-z_]+`）", f"（校验结果 `{verdict}`）", text)
        if rel not in text:
          text = text.replace(
            "| `screenshots/操作NNN.png` | 每一步操作后的画面截图",
            f"| `{rel}` | **重建的**第 0 帧（题目刚打开时的画面），见下文说明。 |\n| `screenshots/操作NNN.png` | 每一步操作后的画面截图",
            1,
        )
        readme.write_text(text, encoding="utf-8")
    return doc["showcase"]["initial_frame"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--server", default="http://127.0.0.1:4181")
    ap.add_argument("--mask-ids", default="{}", help='JSON {task_id: mask_id} for niivue tasks')
    ap.add_argument("task_ids", nargs="*")
    args = ap.parse_args()
    mask_ids = json.loads(args.mask_ids)
    tasks = args.task_ids or [json.loads(l)["task_id"] for l in (args.out / "index.jsonl").read_text().splitlines() if l.strip()]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        def resolve(task_id: str) -> Path:
            for child in sorted(args.out.iterdir()):
                if child.is_dir() and (child.name == task_id or child.name.endswith("-" + task_id)):
                    return child
            return args.out / task_id

        for task_id in tasks:
            task_dir = resolve(task_id)
            doc = json.loads((task_dir / "题目与轨迹.json").read_text(encoding="utf-8"))
            if task_id in mask_ids:
                doc["_mask_id"] = mask_ids[task_id]
                (task_dir / "题目与轨迹.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            ctx = browser.new_context(device_scale_factor=1)
            page = ctx.new_page()
            try:
                info = rebuild(task_dir, args.server, page)
                sc = info["state_check"]
                print(f"{task_id}: {sc['verdict']}  aligned={[a['input'] for a in info['alignment_applied']]}  other_diffs={sc['other_differences']}  canvas={info['canvas']['width']}x{info['canvas']['height']}  errors={info['page_errors'][:1]}")
            finally:
                ctx.close()
        browser.close()
    # strip helper key
    for task_id in tasks:
        task_dir = resolve(task_id)
        path = task_dir / "题目与轨迹.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.pop("_mask_id", None) is not None:
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            (task_dir / "data.js").write_text("window.SV_CASE = " + json.dumps(doc, ensure_ascii=False) + ";\n", encoding="utf-8")


if __name__ == "__main__":
    main()
