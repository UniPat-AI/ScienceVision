"""End-to-end generation for the claude-code-2d (baseline ①) env, enforcing the
hard constraint: only PNG views reach the container, never the raw 3D file, and
the instruction never leaks an asset path. Renderer is faked (no GL needed).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test environment
    import tomli as tomllib

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402

def _png_b64():
    from PIL import Image

    image = Image.new("RGB", (16, 16), "black")
    image.putpixel((8, 8), (255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_PNG_B64 = _png_b64()


def _task(tmp_path):
    p = tmp_path / "ct.nii.gz"
    p.write_bytes(b"\x00fake")
    return {
        "task_id": "sv-medical-k3-2d-0001",
        "domain": "medical",
        "v_class": "K3",
        "instruction": "Which lobe contains the lesion?",
        "asset_path": str(p),
        "asset_format": "nifti",
        "answer_schema": {"kind": "freeform"},
        "scorer": {"primary": "llm_judge", "secondary": []},
        "allowed_tools": [], "forbidden_tools": [], "ground_truth": None,
    }


def test_2d_task_stages_views_not_asset(tmp_path, monkeypatch):
    task = _task(tmp_path)
    source_sha256 = hashlib.sha256(Path(task["asset_path"]).read_bytes()).hexdigest()
    monkeypatch.setattr(
        gen,
        "_lookup_render_handler",
        lambda name: (lambda args: {
            "image_png_base64": _PNG_B64,
            "source_sha256": source_sha256,
        }),
    )
    monkeypatch.setattr(gen, "_resolve_best_slices", lambda task: None)
    monkeypatch.setattr(gen, "_nifti_display_contract", lambda envelope, image_info: {
        "renderer_identity_pass": True,
        "orientation_pass": True,
        "physical_fov_pass": True,
        "target_grid_identity_pass": True,
        "source_fov_coverage_pass": True,
    })
    task_dir = gen.build_one(task, tmp_path / "out",
                             containerized=True, agent_env="claude-code-2d")

    df = (task_dir / "environment" / "Dockerfile").read_text()
    assert "FROM sci3d-claude-code-2d:latest" in df
    assert "COPY views/ /data/" in df
    # views present, raw asset absent
    assert (task_dir / "environment" / "views" / "view_00.png").exists()
    assert not (task_dir / "environment" / "asset").exists()

    instr = (task_dir / "instruction.md").read_text()
    assert "Which lobe contains the lesion?" in instr
    # CONSTRAINT: no raw-file path leaks anywhere in the instruction
    assert "Asset:" not in instr
    assert str(tmp_path / "ct.nii.gz") not in instr
    assert "ct.nii.gz" not in instr
    block = re.search(r"```sci3d\s*\n(.*?)```", instr, re.DOTALL)
    payload = json.loads(block.group(1).strip())
    assert payload["no_asset_access"] is True
    assert "asset_path" not in payload
    # default n_views is now 6 (3 NIfTI axes x {0.35, 0.65} fractions).
    assert payload["views"] == [f"/data/view_{i:02d}.png" for i in range(6)]


def test_2d_task_fails_closed_when_render_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: None)
    monkeypatch.setattr(gen, "_resolve_best_slices", lambda task: None)
    task = _task(tmp_path)
    with pytest.raises(gen.RenderVisibilityError, match="renderer_unavailable"):
        gen.build_one(task, tmp_path / "out",
                      containerized=True, agent_env="claude-code-2d")
    assert not (tmp_path / "out" / task["task_id"]).exists()


def _safe_metadata_task(source, mask, *, instruction):
    return {
        "task_id": "sv-medical-cc2d-safe-metadata",
        "domain": "medical",
        "v_class": "V1",
        "instruction": instruction,
        "asset_path": str(source),
        "asset_format": "nii.gz",
        "mask_path": str(mask),
        "assets": [{
            "asset_id": "medical:safe-case",
            "asset_path": str(source),
            "asset_format": "nii.gz",
        }],
        "answer_schema": {"kind": "categorical", "fields": ["laterality"]},
        "scorer": {"primary": "exact_match", "secondary": []},
        "allowed_tools": [],
        "forbidden_tools": [],
        "ground_truth": {"value": {"laterality": "left"}},
        "evaluation_eligible": True,
        "n_views": 1,
    }


def _install_safe_metadata_stager(monkeypatch, source, mask):
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    mask_sha256 = hashlib.sha256(mask.read_bytes()).hexdigest()

    def fake_stage(_task, env_dir):
        views_dir = env_dir / "views"
        views_dir.mkdir(parents=True)
        (views_dir / "view_00.png").write_bytes(b"png")
        manifest_path = env_dir / "render_manifest.json"
        manifest_path.write_text(json.dumps({
            "sources": [{
                "asset_id": "medical:safe-case",
                "format": "nii.gz",
                "sha256": source_sha256,
                "mask_sha256": mask_sha256,
            }],
            "views": [{
                "asset_id": "medical:safe-case",
                "view_index": 0,
                "file": "view_00.png",
                "container_path": "/data/view_00.png",
            }],
        }))
        return {
            "view_container_paths": ["/data/view_00.png"],
            "asset_views": [{
                "asset_id": "medical:safe-case",
                "views": ["/data/view_00.png"],
            }],
            "modality": "nii.gz",
            "render_manifest": str(manifest_path),
            "visibility_status": "technical_pass",
        }

    current = gen.AGENT_ENVS["claude-code-2d"]
    monkeypatch.setitem(
        gen.AGENT_ENVS,
        "claude-code-2d",
        gen.AgentEnv(
            current.name,
            current.dockerfile,
            fake_stage,
            current.instruction_rewriter,
        ),
    )
    return source_sha256, mask_sha256


def _install_safe_multi_asset_metadata_stager(monkeypatch, assets):
    source_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in assets]

    def fake_stage(_task, env_dir):
        views_dir = env_dir / "views"
        views_dir.mkdir(parents=True)
        sources = []
        views = []
        asset_views = []
        container_paths = []
        for index, source_sha256 in enumerate(source_hashes):
            asset_id = f"biology:safe-{index}"
            filename = f"asset_{index:02d}_view_00.png"
            (views_dir / filename).write_bytes(b"png")
            sources.append({
                "asset_id": asset_id,
                "format": "mmcif",
                "sha256": source_sha256,
            })
            container_path = f"/data/{filename}"
            views.append({
                "asset_id": asset_id,
                "view_index": 0,
                "file": filename,
                "container_path": container_path,
            })
            asset_views.append({"asset_id": asset_id, "views": [container_path]})
            container_paths.append(container_path)
        manifest_path = env_dir / "render_manifest.json"
        manifest_path.write_text(json.dumps({"sources": sources, "views": views}))
        return {
            "view_container_paths": container_paths,
            "asset_views": asset_views,
            "modality": "mmcif",
            "render_manifest": str(manifest_path),
            "visibility_status": "technical_pass",
        }

    current = gen.AGENT_ENVS["claude-code-2d"]
    monkeypatch.setitem(
        gen.AGENT_ENVS,
        "claude-code-2d",
        gen.AgentEnv(
            current.name,
            current.dockerfile,
            fake_stage,
            current.instruction_rewriter,
        ),
    )
    return source_hashes


def test_2d_task_serialized_metadata_contains_no_raw_asset_paths(tmp_path, monkeypatch):
    private_dir = tmp_path / "private-inputs"
    private_dir.mkdir()
    source = private_dir / "patient-volume.nii.gz"
    mask = private_dir / "patient-mask.nii.gz"
    source.write_bytes(b"source-volume")
    mask.write_bytes(b"source-mask")
    source_sha256, mask_sha256 = _install_safe_metadata_stager(
        monkeypatch, source, mask,
    )
    task = _safe_metadata_task(
        source,
        mask,
        instruction="Which side contains the highlighted lesion?",
    )
    task_dir = gen.build_one(
        task,
        tmp_path / "out",
        containerized=True,
        agent_env="claude-code-2d",
    )

    serialized_paths = [
        path for path in task_dir.rglob("*")
        if path.is_file() and path.suffix in {".toml", ".md", ".sh", ".py", ".json"}
    ]
    serialized_paths.append(task_dir / "environment" / "Dockerfile")
    forbidden = {
        str(source),
        str(mask),
        source.name,
        mask.name,
        f"private-inputs/{source.name}",
        f"private-inputs/{mask.name}",
        f"/data/{source.name}",
        f"/data/{mask.name}",
    }
    for artifact in serialized_paths:
        text = artifact.read_text()
        for value in forbidden:
            assert value not in text, f"{value!r} leaked through {artifact}"

    metadata = tomllib.loads((task_dir / "task.toml").read_text())["metadata"]
    assert "asset_path" not in metadata
    # task.toml is agent-visible; it carries only the public execution
    # boundary, not the verifier's rendered-input provenance or answer key.
    assert "assets" not in metadata
    assert "ground_truth" not in metadata
    assert "scorer" not in metadata
    test_sh = (task_dir / "tests" / "test.sh").read_text()
    match = re.search(r"^export SCI3D_METADATA='(.*)'$", test_sh, re.MULTILINE)
    assert match is not None
    test_metadata = json.loads(match.group(1).replace("'\\''", "'"))
    assert test_metadata["assets"] == [{
        "asset_id": "medical:safe-case",
        "asset_format": "nii.gz",
        "sha256": source_sha256,
        "rendered_view_files": ["views/view_00.png"],
        "mask_sha256": mask_sha256,
    }]
    assert test_metadata["ground_truth"] == {"value": {"laterality": "left"}}
    assert test_metadata["scorer"]["primary"] == "exact_match"
    assert source_sha256 in test_sh
    assert mask_sha256 in test_sh
    assert "views/view_00.png" in test_sh


def test_2d_task_safe_metadata_drops_rich_multi_asset_hash_paths(tmp_path, monkeypatch):
    private_dir = tmp_path / "private-inputs"
    private_dir.mkdir()
    source_a = private_dir / "first.mmcif"
    source_b = private_dir / "second.mmcif"
    source_a.write_bytes(b"first-structure")
    source_b.write_bytes(b"second-structure")
    source_hashes = _install_safe_multi_asset_metadata_stager(
        monkeypatch, [source_a, source_b],
    )
    task = {
        "task_id": "sv-biology-cc2d-rich-safe-metadata",
        "domain": "biology",
        "v_class": "V2",
        "instruction": "Compare the two rendered structures.",
        "asset_path": str(source_a),
        "asset_format": "mmcif",
        "asset": {
            "asset_id": "biology:safe-0",
            "path": str(source_a),
            "repo_path": f"private-inputs/{source_a.name}",
        },
        "assets": [
            {
                "asset_id": "biology:safe-0",
                "asset_path": str(source_a),
                "asset_format": "mmcif",
                "repo_path": f"private-inputs/{source_a.name}",
            },
            {
                "asset_id": "biology:safe-1",
                "asset_path": str(source_b),
                "asset_format": "mmcif",
                "repo_path": f"private-inputs/{source_b.name}",
            },
        ],
        "asset_hashes": [
            {
                "repo_path": f"private-inputs/{source_a.name}",
                "sha256": source_hashes[0],
            },
            {
                "repo_path": f"private-inputs/{source_b.name}",
                "sha256": source_hashes[1],
            },
        ],
        "answer_schema": {"kind": "structured", "fields": ["comparison"]},
        "scorer": {"primary": "exact_match", "secondary": []},
        "allowed_tools": [],
        "forbidden_tools": [],
        "ground_truth": {"value": {"comparison": "different"}},
        "evaluation_eligible": True,
        "n_views": 1,
    }
    task_dir = gen.build_one(
        task,
        tmp_path / "out",
        containerized=True,
        agent_env="claude-code-2d",
    )

    task_toml_text = (task_dir / "task.toml").read_text()
    metadata = tomllib.loads(task_toml_text)["metadata"]
    test_sh = (task_dir / "tests" / "test.sh").read_text()
    match = re.search(r"^export SCI3D_METADATA='(.*)'$", test_sh, re.MULTILINE)
    assert match is not None
    test_metadata = json.loads(match.group(1).replace("'\\''", "'"))
    assert "asset_hashes" not in metadata
    assert "assets" not in metadata
    assert "ground_truth" not in metadata
    assert "scorer" not in metadata
    assert test_metadata["assets"] == [
        {
            "asset_id": "biology:safe-0",
            "asset_format": "mmcif",
            "sha256": source_hashes[0],
            "rendered_view_files": ["views/asset_00_view_00.png"],
        },
        {
            "asset_id": "biology:safe-1",
            "asset_format": "mmcif",
            "sha256": source_hashes[1],
            "rendered_view_files": ["views/asset_01_view_00.png"],
        },
    ]
    assert test_metadata["ground_truth"] == {"value": {"comparison": "different"}}
    assert test_metadata["scorer"]["primary"] == "exact_match"
    forbidden = {
        str(source_a),
        str(source_b),
        source_a.name,
        source_b.name,
        f"private-inputs/{source_a.name}",
        f"private-inputs/{source_b.name}",
        "asset_hashes",
        "repo_path",
    }
    for text in (task_toml_text, test_sh, json.dumps(metadata, sort_keys=True)):
        for value in forbidden:
            assert value not in text


def test_2d_task_rejects_raw_path_in_instruction(tmp_path, monkeypatch):
    private_dir = tmp_path / "private-inputs"
    private_dir.mkdir()
    source = private_dir / "patient-volume.nii.gz"
    mask = private_dir / "patient-mask.nii.gz"
    source.write_bytes(b"source-volume")
    mask.write_bytes(b"source-mask")
    _install_safe_metadata_stager(monkeypatch, source, mask)
    task = _safe_metadata_task(
        source,
        mask,
        instruction=f"Inspect {source.name} and report the lesion side.",
    )

    with pytest.raises(gen.RenderVisibilityError, match="cc2d_sensitive_path_leakage"):
        gen.build_one(
            task,
            tmp_path / "out",
            containerized=True,
            agent_env="claude-code-2d",
        )
    assert not (tmp_path / "out" / task["task_id"]).exists()


def test_2d_task_rejects_raw_path_in_nested_metadata(tmp_path, monkeypatch):
    private_dir = tmp_path / "private-inputs"
    private_dir.mkdir()
    source = private_dir / "patient-volume.nii.gz"
    mask = private_dir / "patient-mask.nii.gz"
    source.write_bytes(b"source-volume")
    mask.write_bytes(b"source-mask")
    _install_safe_metadata_stager(monkeypatch, source, mask)
    task = _safe_metadata_task(
        source,
        mask,
        instruction="Which side contains the highlighted lesion?",
    )
    task["ground_truth"]["notes"] = {
        "provenance": f"derived from private-inputs/{source.name}",
    }

    with pytest.raises(gen.RenderVisibilityError, match="cc2d_sensitive_path_leakage"):
        gen.build_one(
            task,
            tmp_path / "out",
            containerized=True,
            agent_env="claude-code-2d",
        )
    assert not (tmp_path / "out" / task["task_id"]).exists()
