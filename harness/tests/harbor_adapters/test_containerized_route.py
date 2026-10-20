"""Tests for the sci3d containerized Harbor route.

The containerized route's whole point is *zero duplication*: tests/score.py and
the host-run Sci3dVerifier both call harbor_adapters.sci3d._score_core. These
tests pin (1) the shared core produces a sane reward dict, (2) the generator
emits a valid containerized task (separate verifier env + image + score.py +
fully-substituted test.sh), and (3) host and container rewards are byte-for-byte
identical for the same inputs — the anti-drift guarantee.

Run in the py3.12 harbor venv:
    .venv-harbor/bin/python -m pytest tests/harbor_adapters/test_containerized_route.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("SCI3D_USE_FAKE_LLM", "1")

from harbor_adapters.sci3d._score_core import (  # noqa: E402
    ANSWER_FILE,
    TRAJECTORY_FILE,
    score_rewards,
)
from scripts.harbor import build_harbor_tasks as gen  # noqa: E402

_META = {
    "domain": "biology",
    "v_class": "V1",
    "asset_path": "/data/1F88.cif",
    "asset_format": "mmcif",
    "answer_schema": {"kind": "camera_pose", "fields": ["azimuth"]},
    "ground_truth": {"value": {"azimuth": 45}},
    "scorer": {"primary": "exact_match", "secondary": []},
    "allowed_tools": ["sv3d_open_scene", "sv3d_render", "finish"],
    "forbidden_tools": ["sv_cheat"],
    "weights": {"answer": 1.0, "process": 0.0},
}

_TASK = {
    "task_id": "sv-biology-v1-0001",
    "domain": "biology",
    "v_class": "V1",
    "asset_path": "/data/1F88.cif",
    "asset_format": "mmcif",
    "answer_schema": _META["answer_schema"],
    "scorer": {"primary": "exact_match", "secondary": []},
    "ground_truth": _META["ground_truth"],
    "allowed_tools": _META["allowed_tools"],
    "forbidden_tools": _META["forbidden_tools"],
    "instruction": "Open 1F88 and frame the retinal pocket. It's tricky.",
}


def _write_run(agent_dir: Path) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / ANSWER_FILE).write_text(json.dumps({"answer": {"azimuth": 45}}))
    (agent_dir / TRAJECTORY_FILE).write_text(
        json.dumps(
            {
                "steps": [
                    {"tool_calls": [{"function_name": "sv3d_open_scene"}]},
                    {"tool_calls": [{"function_name": "sv3d_render"}]},
                    {"tool_calls": [{"function_name": "finish"}]},
                ]
            }
        )
    )


# ── _score_core ─────────────────────────────────────────────────────────── #
def test_score_rewards_shape(tmp_path):
    _write_run(tmp_path / "agent")
    rewards, audit = score_rewards(
        _META, task_id="t1", instruction="Open 1F88", agent_dir=tmp_path / "agent"
    )
    assert rewards["process_opened_scene"] == 1.0
    assert rewards["process_rendered"] == 1.0
    assert rewards["process_finished"] == 1.0
    assert 0.0 <= rewards["reward"] <= 1.0
    assert rewards["answer_score"] == 1.0
    assert "llm_judge" not in rewards
    assert audit["domain"] == "biology"
    assert audit["tool_names"] == ["sv3d_open_scene", "sv3d_render", "finish"]


def test_score_rewards_empty_run_degrades(tmp_path):
    # No answer / trajectory files: must degrade, not raise.
    rewards, audit = score_rewards(
        _META, task_id="t1", instruction="x", agent_dir=tmp_path / "missing"
    )
    assert rewards["process_opened_scene"] == 0.0
    assert 0.0 <= rewards["reward"] <= 1.0


# ── generator --containerized ───────────────────────────────────────────── #
def test_generator_containerized_layout(tmp_path):
    task_dir = gen.build_one(_TASK, tmp_path, containerized=True)
    toml = (task_dir / "task.toml").read_text()
    config = tomllib.loads(toml)
    assert config["verifier"]["environment_mode"] == "separate"
    verifier_env = config["verifier"]["environment"]
    assert verifier_env["workdir"] == "/tests"
    assert str(PurePosixPath(verifier_env["workdir"]) / "test.sh") == "/tests/test.sh"
    assert verifier_env["docker_image"] == gen._SCORER_IMAGE
    image_tag = verifier_env["docker_image"].rsplit(":", 1)[-1]
    assert image_tag and image_tag != "latest"
    # score.py copied verbatim; test.sh fully substituted.
    assert (task_dir / "tests" / "score.py").exists()
    assert (task_dir / "tests" / "test.sh").is_file()
    test_sh = (task_dir / "tests" / "test.sh").read_text()
    assert "{{" not in test_sh  # all placeholders filled
    assert "sci3d/sv-biology-v1-0001" in test_sh
    assert '"domain": "biology"' in test_sh


def test_generator_host_route_has_no_container_bits(tmp_path):
    task_dir = gen.build_one(_TASK, tmp_path, containerized=False)
    toml = (task_dir / "task.toml").read_text()
    assert "environment_mode" not in toml
    assert "[verifier.environment]" not in toml
    assert not (task_dir / "tests" / "score.py").exists()


def test_v21_scorer_override_matches_generated_task(tmp_path):
    """A registry change must not break the staged-tree scorer replacement."""
    from scripts.harbor.build_v21_ev2_trees import patch_scorer

    task_dir = gen.build_one(_TASK, tmp_path, containerized=True)
    scorer = "registry.example/sciencevision-scorer@sha256:" + "a" * 64
    patch_scorer(task_dir, scorer)
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["verifier"]["environment"]["docker_image"] == scorer


def _scorer_build_fixture(tmp_path):
    repo = tmp_path / "repo"
    templates = repo / "scripts" / "harbor" / "templates"
    templates.mkdir(parents=True)
    for name in ("build_image.sh", "Dockerfile"):
        shutil.copy2(_ROOT / "scripts" / "harbor" / "templates" / name, templates / name)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ScienceVision Test",
            "-c",
            "user.email=test@sciencevision.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    return repo, templates / "build_image.sh", revision


def _fake_docker(tmp_path, *, revision, image_exists):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "image-exists"
    if image_exists:
        state.touch()
    log = tmp_path / "docker-build-args"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
state = Path(os.environ["FAKE_DOCKER_STATE"])
if args[:2] == ["image", "inspect"]:
    if not state.exists():
        raise SystemExit(1)
    if "--format" in args:
        template = args[args.index("--format") + 1]
        if "revision" in template:
            print(os.environ["FAKE_DOCKER_REVISION"])
        elif ".Id" in template:
            print(os.environ["FAKE_DOCKER_IMAGE_ID"])
    raise SystemExit(0)
if args and args[0] == "build":
    Path(os.environ["FAKE_DOCKER_LOG"]).write_text("\\n".join(args))
    iidfile = Path(args[args.index("--iidfile") + 1])
    iidfile.write_text(os.environ["FAKE_DOCKER_IMAGE_ID"])
    state.touch()
    raise SystemExit(0)
raise SystemExit(90)
"""
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "FAKE_DOCKER_STATE": str(state),
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_DOCKER_REVISION": revision,
            "FAKE_DOCKER_IMAGE_ID": "sha256:scorer-test-image",
        }
    )
    return env, log


def test_scorer_build_rejects_latest_tag(tmp_path):
    repo, build_script, _ = _scorer_build_fixture(tmp_path)
    result = subprocess.run(
        ["bash", str(build_script), "sci3d-scorer:latest"],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "Refusing unversioned scorer image tag" in result.stderr


def test_scorer_dockerfile_labels_source_revision():
    dockerfile = (
        _ROOT / "scripts" / "harbor" / "templates" / "Dockerfile"
    ).read_text()
    assert "ARG SCI3D_SOURCE_REVISION" in dockerfile
    assert (
        'LABEL org.opencontainers.image.revision="${SCI3D_SOURCE_REVISION}"'
        in dockerfile
    )


def test_scorer_build_reuses_only_matching_revision(tmp_path):
    repo, build_script, revision = _scorer_build_fixture(tmp_path)
    env, log = _fake_docker(tmp_path, revision=revision, image_exists=True)
    result = subprocess.run(
        ["bash", str(build_script)], cwd=repo, env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert "SCI3D_SCORER_IMAGE_ID=sha256:scorer-test-image" in result.stdout
    assert f"SCI3D_SCORER_SOURCE_REVISION={revision}" in result.stdout
    assert not log.exists()

    env["FAKE_DOCKER_REVISION"] = "deadbeef"
    mismatch = subprocess.run(
        ["bash", str(build_script)], cwd=repo, env=env, text=True, capture_output=True
    )
    assert mismatch.returncode == 2
    assert "mismatched source revision" in mismatch.stderr


def test_scorer_build_verifies_new_image_provenance(tmp_path):
    repo, build_script, revision = _scorer_build_fixture(tmp_path)
    env, log = _fake_docker(tmp_path, revision=revision, image_exists=False)
    result = subprocess.run(
        ["bash", str(build_script)], cwd=repo, env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    build_args = log.read_text().splitlines()
    assert "--build-arg" in build_args
    assert f"SCI3D_SOURCE_REVISION={revision}" in build_args
    assert "--iidfile" in build_args
    assert "SCI3D_SCORER_IMAGE_ID=sha256:scorer-test-image" in result.stdout
    assert f"SCI3D_SCORER_SOURCE_REVISION={revision}" in result.stdout


def test_test_sh_metadata_roundtrips_to_json(tmp_path):
    """The inlined SCI3D_METADATA must be valid JSON the scorer can parse."""
    task_dir = gen.build_one(_TASK, tmp_path, containerized=True)
    test_sh = (task_dir / "tests" / "test.sh").read_text()
    blob = test_sh.split("export SCI3D_METADATA='", 1)[1].split("'\n", 1)[0]
    meta = json.loads(blob)
    assert meta["domain"] == "biology"
    assert meta["scorer"]["primary"] == "exact_match"


# ── anti-drift: host == container ───────────────────────────────────────── #
def test_host_and_container_rewards_identical(tmp_path):
    """Both routes call score_rewards -> rewards must be byte-for-byte equal."""
    agent_dir = tmp_path / "agent"
    _write_run(agent_dir)

    # container path: score_rewards directly (score.py is a thin shim over it).
    cont_rewards, _ = score_rewards(
        _META,
        task_id="sci3d/sv-biology-v1-0001",
        instruction="inst",
        agent_dir=agent_dir,
    )

    # host path: Sci3dVerifier (also delegates to score_rewards).
    from harbor_adapters.sci3d.verifier import Sci3dVerifier

    task = SimpleNamespace(
        config=SimpleNamespace(metadata=_META, task=None),
        short_name="sci3d/sv-biology-v1-0001",
        name="sci3d/sv-biology-v1-0001",
        instruction="inst",
    )
    trial_paths = SimpleNamespace(agent_dir=agent_dir, verifier_dir=tmp_path / "verifier")
    v = Sci3dVerifier(
        task=task,
        trial_paths=trial_paths,
        environment=None,
        override_env=None,
        logger=None,
        verifier_env=None,
        step_name=None,
    )
    res = asyncio.run(v.verify())

    assert dict(res.rewards) == cont_rewards
