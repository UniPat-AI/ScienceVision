#!/usr/bin/env python3
"""Export approved ScienceVision tasks as self-contained showcase folders.

One folder per task, named by task_id:

    <out>/<task_id>/
        题目与轨迹.json     official approved-export document + showcase additions
        README.md           field-by-field explanation for downstream agents
        index.html          static flip-book viewer (open directly, file:// works)
        data.js             the same JSON wrapped as `window.SV_CASE = ...`
        screenshots/操作NNN.png   one PNG per sealed trajectory step
        lib/                i18n.js + qc-action-label.js copied from the repo

3D assets (cif / glb / nii.gz ...) are deliberately NOT exported; only their
metadata (path, sha256, byte size) is.

Reads a local snapshot of the manager DB (never the live prod file) and pulls
archived keyframe PNGs from OSS read-only with sha256 verification.

Usage:
    SV_OSS_AK=... SV_OSS_SK=... python3 export_showcase.py \
        --repo /path/to/BabyVision-v2 --db /tmp/prod-snap/manager.db \
        --out ~/Desktop/ScienceVision-展示题目 \
        sv-biol-bio_collision_check-0012 arch-jdc-clash-c01 ...
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path

from deidentify import scrub_document

HERE = Path(__file__).resolve().parent

DOMAIN_LABELS = {
    "biology": "生物学", "chemistry": "化学", "medical": "医学",
    "architecture": "建筑", "mechanical": "机械", "physics": "物理",
}
VIEWER_LABELS = {
    "molstar": {"label": "Mol*（分子结构查看器）", "engine": "Mol* 4.5.0"},
    "niivue": {"label": "NiiVue（医学影像查看器）", "engine": "NiiVue 0.45.0"},
    "gltf": {"label": "glTF 三维模型查看器", "engine": "three.js glTF"},
    "occt": {"label": "OCCT（CAD / STEP 查看器）", "engine": "OpenCascade"},
    "bim_coordination": {"label": "BIM 管线协调查看器", "engine": "three.js"},
    "architecture_inspection": {"label": "建筑巡检查看器", "engine": "three.js"},
}


class SignedOssReader:
    """Minimal read-only OSS client (signature V1) so oss2 is not required."""

    def __init__(self, bucket: str, endpoint: str, ak: str, sk: str) -> None:
        self.bucket, self.endpoint, self.ak, self.sk = bucket, endpoint, ak, sk

    def get_keyframe_png(self, key: str, *, expected_sha256=None, expected_byte_size=None) -> bytes:
        date = formatdate(usegmt=True)
        string_to_sign = f"GET\n\n\n{date}\n/{self.bucket}/{key}"
        sig = base64.b64encode(hmac.new(self.sk.encode(), string_to_sign.encode(), hashlib.sha1).digest()).decode()
        req = urllib.request.Request(
            f"https://{self.bucket}.{self.endpoint}/{urllib.parse.quote(key)}",
            headers={"Date": date, "Authorization": f"OSS {self.ak}:{sig}"},
        )
        payload = urllib.request.urlopen(req, timeout=120).read()
        if expected_byte_size is not None and len(payload) != expected_byte_size:
            raise RuntimeError(f"byte size mismatch for {key}")
        if expected_sha256 and hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RuntimeError(f"sha256 mismatch for {key}")
        return payload


def load_i18n_glosses(i18n_path: Path) -> dict[str, dict[str, str]]:
    """Pull the zh-CN `viewer.tool.*` / `viewer.help.*` / `viewer.arg.*` tables out of i18n.js."""
    text = i18n_path.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {"tool": {}, "help": {}, "arg": {}, "enum": {}}
    for kind, name, value in re.findall(r"'viewer\.(tool|help|arg|enum)\.([^']+)':\s*'((?:[^'\\]|\\.)*)'", text):
        out[kind].setdefault(name, value.replace("\\'", "'"))  # first hit is zh-CN (base table)
    return out


def load_registries(reg_dir: Path) -> dict[str, dict[str, dict]]:
    """viewer -> tool name -> registry entry (highest version wins)."""
    by_viewer: dict[str, dict[str, dict]] = {}
    for path in sorted(reg_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        viewer = doc.get("viewer") or path.stem.split(".")[0]
        tools = by_viewer.setdefault(viewer, {})
        for tool in doc.get("tools", []):
            tools[tool["name"]] = tool
    return by_viewer


def iso_to_seconds(a: str, b: str) -> float:
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    def parse(s: str) -> datetime:
        s = s.replace("+00:00", "Z")
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return (parse(b) - parse(a)).total_seconds()


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    w = int.from_bytes(data[16:20], "big"); h = int.from_bytes(data[20:24], "big")
    return (w, h)


def resolve_task_dir(out_root: Path, task_id: str) -> Path:
    """Task folders may carry a human prefix (e.g. `生物001-sv-biol-…`). Reuse an
    existing folder whose name is `<task_id>` or ends with `-<task_id>`; otherwise
    create `<task_id>`. Keeps re-exports from spawning a second copy."""
    if out_root.exists():
        for child in sorted(out_root.iterdir()):
            if child.is_dir() and (child.name == task_id or child.name.endswith("-" + task_id)):
                return child
    return out_root / task_id


def build_task(store, task_id: str, *, sink, registries, glosses, repo: Path, out_root: Path) -> dict:
    from tools.offline_task_manager.approved_export import _build_one_task, _selected_rows

    row = store.connection.execute(
        "SELECT assignment_id FROM assignments WHERE task_id=? AND status='approved' ORDER BY status_changed_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"{task_id}: no approved assignment")
    items = _selected_rows(store, [row["assignment_id"]])
    document, files = _build_one_task(store, items[0], image_sink=sink, staging_root=None)
    # The production row carries the annotator's real name; keep only the account slug.
    document = scrub_document(document)

    task = store.connection.execute(
        "SELECT payload_json FROM tasks WHERE task_id=? AND batch_id=?", (task_id, document["batch_id"])
    ).fetchone()
    payload = json.loads(task["payload_json"]) if task else {}
    primary = payload.get("asset") or {}
    viewer = primary.get("viewer") or payload.get("viewer") or "gltf"
    assets = []
    for asset in payload.get("assets", []):
        assets.append({
            "name": str(asset.get("path", "")).rsplit("/", 1)[-1],
            "path": asset.get("path"),
            "role": asset.get("role"),
            "type": asset.get("type"),
            "sha256": asset.get("sha256"),
            "byte_size": asset.get("bytes") or asset.get("byte_size"),
            "mask_id": asset.get("mask_id"),
            "included": False,
        })

    ops = document["trajectory"]["operations"]
    tools_used: dict[str, int] = {}
    for op in ops:
        name = op["function"]["name"]
        tools_used[name] = tools_used.get(name, 0) + 1
    glossary = {}
    reg = registries.get(viewer, {})
    for name in tools_used:
        entry = reg.get(name) or next((r[name] for r in registries.values() if name in r), {})
        glossary[name] = {
            "label_zh": glosses["tool"].get(name, name),
            "help_zh": glosses["help"].get(name, ""),
            "description_en": entry.get("description", ""),
            "input_schema": entry.get("inputSchema", {}),
        }

    task_dir = resolve_task_dir(out_root, task_id)
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "screenshots").mkdir(parents=True)
    (task_dir / "lib").mkdir()
    shots = []
    for op in ops:
        kf = op.get("keyframe")
        if not kf or "screenshot_file" not in kf:
            continue
        name = kf["screenshot_file"]
        data = files[name]
        rel = f"screenshots/{name}"
        (task_dir / rel).write_bytes(data)
        kf["screenshot_file"] = rel
        w, h = png_size(data)
        shots.append({"operation_seq": op["operation_seq"], "file": rel, "width": w, "height": h, "byte_size": len(data), "sha256": kf["screenshot"]["sha256"]})

    document["task_context"] = {
        "viewer": viewer,
        "viewer_label": VIEWER_LABELS.get(viewer, {}).get("label", viewer),
        "engine": VIEWER_LABELS.get(viewer, {}).get("engine", ""),
        "domain_label": DOMAIN_LABELS.get(document["meta"]["domain"], document["meta"]["domain"]),
        "answer_shape": payload.get("answer_shape"),
        "assets": assets,
        "assets_note": "3D 素材文件未随包导出，只保留引用元数据（路径 / sha256 / 大小）。",
    }
    document["tool_glossary"] = glossary

    # --- 原始信息：题目刚打开时专家看到的东西，以及质检前的原始题面 ---
    translation = store.connection.execute(
        "SELECT title_zh, instruction_zh FROM task_surface_translations WHERE task_id=? AND batch_id=? ORDER BY translated_at DESC LIMIT 1",
        (task_id, document["batch_id"]),
    ).fetchone()
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    document["original_task"] = {
        "title_en": payload.get("title"),
        "title_zh": translation["title_zh"] if translation else None,
        "question_en_original": payload.get("question"),
        "question_zh_original": translation["instruction_zh"] if translation else None,
        "question_id": payload.get("question_id"),
        "vision_class": payload.get("vision_class"),
        "vision_class_code": payload.get("vision_class_code"),
        "vision_class_group": payload.get("vision_class_group"),
        "source_domain": payload.get("source_domain") or payload.get("domain"),
        "source_sub_domain": payload.get("source_sub_domain") or payload.get("sub_domain"),
        "forbidden_strategies": payload.get("forbidden_strategies"),
        "trace_requirements": payload.get("trace_requirements"),
        "provenance": {k: provenance.get(k) for k in ("dataset_version", "difficulty", "generator", "access_track") if k in provenance},
        "initial_view_note": "平台没有记录第 0 帧截图；题目打开时查看器的确切状态见 trajectory.initial_state（每次回放都从这个状态 viewer_reset 开始）。",
    }
    review = store.connection.execute(
        "SELECT * FROM question_reviews WHERE assignment_id=?", (document["assignment_id"],)
    ).fetchone()
    document["question_review"] = (
        {
            "decision": review["decision"],
            "submitted_at": review["submitted_at"],
            "decided_at": review["decided_at"],
            "decided_by": review["decided_by"],
            "expert_validation_feedback": review["validation_feedback"],
            "expert_question_edit_zh": review["question_edit_zh"],
            "expert_question_edit_en": review["question_edit_en"],
            "sub_domain_edit": review["sub_domain_edit"],
            "question_changed": bool(
                (review["effective_question_zh"] or "") != ((translation["instruction_zh"] if translation else "") or "")
            ),
        }
        if review
        else None
    )
    document["timeline"] = [
        {"at": ev["occurred_at"], "event": ev["event_type"], "actor": ev["actor_id"], "actor_role": ev["actor_role"]}
        for ev in store.connection.execute(
            "SELECT occurred_at, event_type, actor_id, actor_role FROM audit_events WHERE assignment_id=? ORDER BY occurred_at, event_id",
            (document["assignment_id"],),
        )
    ]
    document["showcase"] = {
        "schema": "sciencevision.showcase_task.v1",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "生产库 manager.db 只读快照 + OSS 归档截图（sha256 已校验）",
        "screenshots": shots,
        "duration_seconds": round(iso_to_seconds(ops[0]["timing"]["started_at"], ops[-1]["timing"]["completed_at"]), 1) if ops else 0,
        "tools_used": tools_used,
        "arg_glossary_zh": {k: glosses["arg"][k] for k in sorted({key for op in ops for key in (op.get("replay_input") or {}).keys()}) if k in glosses["arg"]},
    }

    document["showcase"]["folder"] = task_dir.name
    apply_overrides(document)
    (task_dir / "题目与轨迹.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    (task_dir / "data.js").write_text("window.SV_CASE = " + json.dumps(document, ensure_ascii=False) + ";\n", encoding="utf-8")
    shutil.copy2(repo / "tools/offline_task_manager/online_app/i18n.js", task_dir / "lib/i18n.js")
    shutil.copy2(repo / "tools/offline_task_manager/manager_app/qc-action-label.js", task_dir / "lib/qc-action-label.js")
    shutil.copy2(HERE / "template_index.html", task_dir / "index.html")
    (task_dir / "README.md").write_text(render_task_readme(document), encoding="utf-8")
    return document


def apply_overrides(document: dict) -> None:
    """Apply hand-made text fixes from _tools/overrides.json (typos, punctuation).

    Only touches expert-written prose (reasoning / expert_note); the original
    text is kept under showcase.text_overrides so nothing is silently lost.
    """
    path = HERE / "overrides.json"
    if not path.exists():
        return
    rules = json.loads(path.read_text(encoding="utf-8")).get(document["task_id"])
    if not rules:
        return
    applied = []

    def fix(text):
        if not isinstance(text, str):
            return text
        out = text
        for old, new in rules.get("replacements", []):
            if old in out:
                out = out.replace(old, new)
                applied.append({"from": old, "to": new})
        return out

    document["answer"]["reasoning"] = fix(document["answer"].get("reasoning"))
    fr = document["trajectory"].get("final_result") or {}
    if "reasoning" in fr:
        fr["reasoning"] = fix(fr["reasoning"])
    for op in document["trajectory"]["operations"]:
        kf = op.get("keyframe")
        if kf and "expert_note" in kf:
            kf["expert_note"] = fix(kf["expert_note"])
    if applied:
        document["showcase"]["text_overrides"] = applied


def render_task_readme(doc: dict) -> str:
    m, tc, tr, sc = doc["meta"], doc["task_context"], doc["trajectory"], doc["showcase"]
    ops = tr["operations"]
    minutes, seconds = divmod(int(sc["duration_seconds"]), 60)
    step_rows = "\n".join(
        f"| {k + 1} | {op['operation_seq']} | `{op['function']['name']}` | {doc['tool_glossary'][op['function']['name']]['label_zh']} | `{json.dumps(op['replay_input'], ensure_ascii=False)[:90]}` | {(op.get('keyframe') or {}).get('expert_note') or '（专家未写）'} |"
        for k, op in enumerate(ops)
    )
    tool_rows = "\n".join(
        f"| `{name}` | {g['label_zh']} | {g['help_zh'] or g['description_en']} |" for name, g in doc["tool_glossary"].items()
    )
    asset_rows = "\n".join(
        f"| `{a['name']}` | {a['role'] or ''} | {a['byte_size'] or ''} | `{(a['sha256'] or '')[:16]}…` |" for a in tc["assets"]
    )
    field_doc = (HERE / "FIELDS.md").read_text(encoding="utf-8")
    ot, qr = doc["original_task"], doc.get("question_review") or {}
    timeline_rows = "\n".join(f"| {e['at']} | `{e['event']}` | {e['actor']}（{e['actor_role']}） |" for e in doc["timeline"])
    initial_json = json.dumps(tr["initial_state"], ensure_ascii=False, indent=2)
    changed = "是（以 `question.zh/en` 为定稿）" if qr.get("question_changed") else "否（与原始题面一致）"
    original_block = f"""## 题目打开时的原始信息

**第 0 帧截图：平台没有记录**。关键帧从第 1 步操作后才开始拍；题目刚打开时查看器的确切状态记录在 `trajectory.initial_state`，每次回放都从它 `viewer_reset` 开始。截图目录里最早的一张（步号 {ops[0]['operation_seq']}）就是初始画面经过第一步操作之后的样子。

原始标题：**{ot.get('title_zh') or '—'}** / {ot.get('title_en') or '—'}
视觉能力类别：{ot.get('vision_class') or '—'}（{ot.get('vision_class_code') or '—'} · {ot.get('vision_class_group') or '—'}）
题目质检：{qr.get('decision') or '—'}，题面是否被质检修改：{changed}

初始查看器状态（`trajectory.initial_state`）：

```json
{initial_json}
```

## 质检时间线（UTC）

| 时间 | 事件 | 操作者 |
|---|---|---|
{timeline_rows}
"""
    return f"""# {doc['task_id']}

> {tc['domain_label']} · {m['sub_domain_label']} · 查看器 {tc['viewer_label']} · 专家 {m['expert_id']} · 答案质检通过 {m['approved_at']}

本文件夹是**一道已通过答案质检的题目**的完整宣传素材（不含 3D 素材文件）。所有内容来自生产库封存的有效轨迹：只包含专家最终提交、且被质检通过的那一条操作血统，已撤销 / 重做掉的分支不在其中。

## 文件夹内容

| 文件 | 作用 |
|---|---|
| `题目与轨迹.json` | 机器可读的完整数据：题面、答案、推理、逐步 MCP 操作轨迹、每步截图引用、元数据。字段说明见下文。 |
| `screenshots/操作NNN.png` | 每一步操作后的画面截图，`NNN` = `operation_seq`（轨迹里的真实步号），可直接放进 PPT。 |
| `index.html` | 双击即可看的静态翻页页（题目 / 推理 / 答案 + 逐步截图 + 每步工具调用与专家说明）。依赖同目录的 `data.js`、`lib/`、`screenshots/`。 |
| `data.js` | 与 `题目与轨迹.json` 内容相同，仅包成 `window.SV_CASE = …` 供 `index.html` 加载。 |
| `lib/` | 从平台复制来的两份前端文件：`i18n.js`（工具名 / 参数中英文词表）和 `qc-action-label.js`（把一次工具调用格式化成一句中文，与质检页展示一致）。 |

## 一眼概览

| 项 | 值 |
|---|---|
| 题号 `task_id` | `{doc['task_id']}` |
| 领域 / 子领域 | {tc['domain_label']} / {m['sub_domain_label']} |
| 查看器 | {tc['viewer_label']}（`{tc['viewer']}`） |
| 3D 素材（未随包） | {', '.join(a['name'] for a in tc['assets']) or '—'} |
| 操作步数 / 截图数 | {tr['operation_count']} / {len(sc['screenshots'])} |
| 专家用时（首步到末步） | {minutes} 分 {seconds} 秒 |
| 最终答案 | `{doc['answer']['answer'].replace(chr(10), ' ')}` |

{original_block}
## 操作轨迹速览

| 第几步 | 步号 `operation_seq`（= 截图编号） | 工具 | 中文名 | 参数（replay_input，截断） | 专家说明（expert_note） |
|---|---|---|---|---|---|
{step_rows}

## 本题用到的 MCP 工具

| 工具名 | 中文名 | 说明 |
|---|---|---|
{tool_rows}

## 引用的 3D 素材（仅元数据）

| 文件 | 角色 | 字节 | sha256 |
|---|---|---|---|
{asset_rows}

{field_doc}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path, help="local snapshot of manager.db (+ -wal next to it)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bucket", default="science-vision")
    ap.add_argument("--endpoint", default="oss-ap-southeast-1.aliyuncs.com")
    ap.add_argument("task_ids", nargs="+")
    args = ap.parse_args()

    ak, sk = os.environ.get("SV_OSS_AK"), os.environ.get("SV_OSS_SK")
    if not ak or not sk:
        raise SystemExit("set SV_OSS_AK / SV_OSS_SK (read-only OSS credentials)")
    sys.path.insert(0, str(args.repo))
    from tools.offline_task_manager.store import ManagerStore

    store = ManagerStore(args.db)
    sink = SignedOssReader(args.bucket, args.endpoint, ak, sk)
    registries = load_registries(args.repo / "tools/offline_task_manager/expert_app/mcp/registries")
    glosses = load_i18n_glosses(args.repo / "tools/offline_task_manager/online_app/i18n.js")
    args.out.mkdir(parents=True, exist_ok=True)

    index_lines = []
    for raw in args.task_ids:
        task_id = raw.replace("‑", "-").replace("‐", "-").strip()
        doc = build_task(store, task_id, sink=sink, registries=registries, glosses=glosses, repo=args.repo, out_root=args.out)
        index_lines.append(json.dumps({
            "task_id": task_id,
            "folder": doc["showcase"]["folder"],
            "domain": doc["meta"]["domain"],
            "domain_label": doc["task_context"]["domain_label"],
            "sub_domain_label": doc["meta"]["sub_domain_label"],
            "viewer": doc["task_context"]["viewer"],
            "operation_count": doc["trajectory"]["operation_count"],
            "screenshot_count": len(doc["showcase"]["screenshots"]),
            "duration_seconds": doc["showcase"]["duration_seconds"],
            "answer": doc["answer"]["answer"],
            "question_zh_head": doc["question"]["zh"][:120],
            "approved_at": doc["meta"]["approved_at"],
        }, ensure_ascii=False))
        print(f"ok  {task_id}  {doc['trajectory']['operation_count']} steps  {doc['task_context']['viewer']}")
    (args.out / "index.jsonl").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    # Fill the task table in the top-level README (template marker or an existing table).
    readme = args.out / "README.md"
    if readme.exists():
        rows = []
        for line in index_lines:
            e = json.loads(line)
            mm, ss = divmod(int(e["duration_seconds"]), 60)
            rows.append(f"| [`{e['task_id']}`]({e['folder']}/README.md) | {e['domain_label']} / {e['sub_domain_label']} | `{e['viewer']}` | {e['operation_count']} | {mm} 分 {ss} 秒 | `{e['answer'].replace(chr(10), ' ')}` |")
        text = readme.read_text(encoding="utf-8")
        table = "\n".join(rows)
        if "{{TABLE}}" in text:
            text = text.replace("{{TABLE}}", table)
        else:
            text = re.sub(r"(\| 题号 \|[^\n]*\n\|[-| ]+\|\n)(?:\|[^\n]*\n)*", lambda mt: mt.group(1) + table + "\n", text)
        readme.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
