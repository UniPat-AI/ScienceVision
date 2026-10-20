"""Tests for tools.sync_from_sciencevision."""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from tools import sync_from_sciencevision as sync


def _clean_git_env() -> dict[str, str]:
    git_env = os.environ.copy()
    for variable in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        git_env.pop(variable, None)
    return git_env


def _git_source(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    git_env = _clean_git_env()
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True, env=git_env)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Mirror Test"],
        check=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "mirror@example.invalid"],
        check=True,
        env=git_env,
    )
    (source / "tracked.txt").write_text("tracked\n")
    subprocess.run(
        ["git", "-C", str(source), "add", "tracked.txt"], check=True, env=git_env
    )
    subprocess.run(
        ["git", "-C", str(source), "commit", "-qm", "fixture"],
        check=True,
        env=git_env,
    )
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    return source, head


def test_parse_manifest_strips_comments_and_blank_lines(tmp_path: pathlib.Path) -> None:
    manifest = tmp_path / ".mirror-include"
    manifest.write_text(
        "# a comment\n"
        "\n"
        "sci3d_agent/**\n"
        "viewer/**   # trailing comment\n"
        "\n"
        "  scripts/match/**  \n"
    )
    patterns = sync.parse_manifest(manifest)
    assert patterns == ["sci3d_agent/**", "viewer/**", "scripts/match/**"]


def test_matches_any_recursive_glob() -> None:
    patterns = ["sci3d_agent/**", "datasets/_matched/*.json"]
    assert sync.matches_any(pathlib.Path("sci3d_agent/mcp/server.py"), patterns)
    assert sync.matches_any(pathlib.Path("sci3d_agent/x.py"), patterns)
    assert sync.matches_any(pathlib.Path("datasets/_matched/scanqa.json"), patterns)
    assert not sync.matches_any(pathlib.Path("datasets/_matched/sub/scanqa.json"), patterns)
    assert not sync.matches_any(pathlib.Path("README.md"), patterns)


def test_matches_any_single_file_pattern() -> None:
    patterns = ["tools/setup_worktree_data_links.sh"]
    assert sync.matches_any(pathlib.Path("tools/setup_worktree_data_links.sh"), patterns)
    assert not sync.matches_any(pathlib.Path("tools/other.sh"), patterns)


def test_verify_source_commit_accepts_exact_head(tmp_path: pathlib.Path) -> None:
    source, head = _git_source(tmp_path)

    assert sync.verify_source_commit(source, head) == head


def test_verify_source_commit_rejects_mismatched_head(tmp_path: pathlib.Path) -> None:
    source, _head = _git_source(tmp_path)
    other_commit = "0" * 40

    with pytest.raises(sync.SourceCommitError, match="does not match expected commit"):
        sync.verify_source_commit(source, other_commit)


def test_verify_source_commit_rejects_dirty_tracked_file(tmp_path: pathlib.Path) -> None:
    source, head = _git_source(tmp_path)
    (source / "tracked.txt").write_text("modified\n")

    with pytest.raises(sync.SourceCommitError, match=r"not clean:  M tracked\.txt"):
        sync.verify_source_commit(source, head)


def test_verify_source_commit_rejects_dirty_untracked_file(tmp_path: pathlib.Path) -> None:
    source, head = _git_source(tmp_path)
    (source / "untracked.txt").write_text("untracked\n")

    with pytest.raises(sync.SourceCommitError, match=r"not clean: \?\? untracked\.txt"):
        sync.verify_source_commit(source, head)


def test_verify_source_commit_rejects_non_git_source(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(sync.SourceCommitError, match="not a readable Git worktree"):
        sync.verify_source_commit(source, "0" * 40)


def test_verify_source_commit_rejects_abbreviated_sha(tmp_path: pathlib.Path) -> None:
    source, head = _git_source(tmp_path)

    with pytest.raises(sync.SourceCommitError, match="full 40-character SHA"):
        sync.verify_source_commit(source, head[:12])


def test_pinned_sync_copies_from_clean_matching_git_tree(tmp_path: pathlib.Path) -> None:
    source, head = _git_source(tmp_path)
    destination = tmp_path / "destination"
    manifest = tmp_path / "manifest"
    manifest.write_text("tracked.txt\n")

    result = sync.main(
        [
            "--source", str(source),
            "--dest", str(destination),
            "--manifest", str(manifest),
            "--expected-source-commit", head,
        ]
    )

    assert result == 0
    assert (destination / "tracked.txt").read_text() == "tracked\n"


def test_pinned_sync_rejects_manifest_matched_ignored_file(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source, head = _git_source(tmp_path)
    destination = tmp_path / "destination"
    manifest = tmp_path / "manifest"
    git_env = _clean_git_env()
    (source / ".gitignore").write_text(".env\n")
    subprocess.run(
        ["git", "-C", str(source), "add", ".gitignore"], check=True, env=git_env
    )
    subprocess.run(
        ["git", "-C", str(source), "commit", "-qm", "ignore env"],
        check=True,
        env=git_env,
    )
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
    ).stdout.strip()
    (source / ".env").write_text("SECRET=not-for-mirror\n")
    manifest.write_text("*\n")

    result = sync.main(
        [
            "--source", str(source),
            "--dest", str(destination),
            "--manifest", str(manifest),
            "--expected-source-commit", head,
        ]
    )

    assert result == 2
    assert "manifest-matched files not tracked" in capsys.readouterr().err
    assert not destination.exists()


def test_hostile_git_environment_cannot_redirect_pinned_validation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, head = _git_source(tmp_path)
    destination = tmp_path / "destination"
    manifest = tmp_path / "manifest"
    manifest.write_text("tracked.txt\n")
    for variable in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.setenv(variable, str(tmp_path / f"hostile-{variable.lower()}"))

    result = sync.main(
        [
            "--source", str(source),
            "--dest", str(destination),
            "--manifest", str(manifest),
            "--expected-source-commit", head,
            "--dry-run",
        ]
    )

    assert result == 0


def test_main_without_commit_guard_accepts_non_git_source(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    manifest = tmp_path / "manifest"
    source.mkdir()
    (source / "tracked.txt").write_text("tracked\n")
    manifest.write_text("tracked.txt\n")

    result = sync.main(
        [
            "--source",
            str(source),
            "--dest",
            str(destination),
            "--manifest",
            str(manifest),
            "--dry-run",
        ]
    )

    assert result == 0


def test_compute_plan_copies_matched_files_and_deletes_stale(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    (source / "sci3d_agent").mkdir(parents=True)
    (source / "sci3d_agent" / "a.py").write_text("a")
    (source / "sci3d_agent" / "sub").mkdir()
    (source / "sci3d_agent" / "sub" / "b.py").write_text("b")
    (source / "other").mkdir()
    (source / "other" / "ignored.py").write_text("ignored")

    (dest / "sci3d_agent").mkdir(parents=True)
    (dest / "sci3d_agent" / "stale.py").write_text("stale")

    plan = sync.compute_plan(source, dest, ["sci3d_agent/**"])

    assert sorted(p.as_posix() for p in plan.copies) == [
        "sci3d_agent/a.py",
        "sci3d_agent/sub/b.py",
    ]
    assert [p.as_posix() for p in plan.deletes] == ["sci3d_agent/stale.py"]


def test_compute_plan_ignores_dot_dirs(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    (source / "sci3d_agent" / ".git").mkdir(parents=True)
    (source / "sci3d_agent" / ".git" / "config").write_text("git-stuff")
    (source / "sci3d_agent" / "__pycache__").mkdir()
    (source / "sci3d_agent" / "__pycache__" / "x.pyc").write_bytes(b"")
    (source / "sci3d_agent" / "real.py").write_text("real")

    plan = sync.compute_plan(source, dest, ["sci3d_agent/**"])

    assert [p.as_posix() for p in plan.copies] == ["sci3d_agent/real.py"]


@pytest.mark.parametrize(
    "generated_dir",
    [".qagen", "build", "dist", "sciencevision_qa_gen.egg-info"],
)
def test_compute_plan_prunes_runtime_and_build_directories(
    tmp_path: pathlib.Path, generated_dir: str
) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    source_package = source / "QA-Gen" / "qa_gen_pipeline"
    dest_package = dest / "QA-Gen" / "qa_gen_pipeline"
    (source_package / generated_dir).mkdir(parents=True)
    (source_package / generated_dir / "generated.txt").write_text("source artifact")
    (source_package / "src").mkdir()
    (source_package / "src" / "qagen.py").write_text("source")
    (dest_package / generated_dir).mkdir(parents=True)
    (dest_package / generated_dir / "local.txt").write_text("local artifact")

    plan = sync.compute_plan(source, dest, ["QA-Gen/qa_gen_pipeline/**"])

    assert [path.as_posix() for path in plan.copies] == [
        "QA-Gen/qa_gen_pipeline/src/qagen.py"
    ]
    assert plan.deletes == []


@pytest.mark.parametrize("generated_file", ["module.pyc", "module.pyo", ".coverage"])
def test_compute_plan_prunes_generated_files(
    tmp_path: pathlib.Path, generated_file: str
) -> None:
    source = tmp_path / "src"
    dest = tmp_path / "dst"
    source_package = source / "QA-Gen" / "qa_gen_pipeline"
    dest_package = dest / "QA-Gen" / "qa_gen_pipeline"
    source_package.mkdir(parents=True)
    dest_package.mkdir(parents=True)
    (source_package / "module.py").write_text("source")
    (source_package / generated_file).write_text("source artifact")
    (dest_package / generated_file).write_text("local artifact")

    plan = sync.compute_plan(source, dest, ["QA-Gen/qa_gen_pipeline/**"])

    assert [path.as_posix() for path in plan.copies] == [
        "QA-Gen/qa_gen_pipeline/module.py"
    ]
    assert plan.deletes == []
