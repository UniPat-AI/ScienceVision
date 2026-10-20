#!/usr/bin/env python3
"""Build a `babyvision.online_manager_release_manifest.v1` release artifact.

The Aug-17 release was produced by hand, so the layout it deployed existed only
on the server. This script reproduces that layout from a git worktree:

    dist/<release_id>/
        release-manifest.json   payload inventory + source provenance
        SHA256SUMS              digests of the payload and of the manifest
        artifact.tar.gz         the same tree, rooted at babyvision-online-manager/

`release_id` is `<git_head>-<artifact_sha256[:12]>`, which is the convention the
systemd unit's WorkingDirectory already follows.

The payload is an explicit allowlist. Nothing is discovered by scanning the repo
root, and a denylist rejects anything that looks like state or a secret even if
an allowlisted root somehow contains it, so a stray credentials file or SQLite
database cannot be packaged by accident.

Usage:
    python3 deploy/build_online_manager_release.py [--repo .] [--out dist]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

RELEASE_NAME = "babyvision-online-manager"
SCHEMA_VERSION = "babyvision.online_manager_release_manifest.v1"

# Files taken verbatim, and directory roots walked recursively. Keeping these
# explicit is what makes the payload auditable: the release can only grow by
# editing this list.
PAYLOAD_FILES = (
    "LICENSE",
    "deploy/Caddyfile.live-precompressed",
    "deploy/babyvision-task-manager.service",
    "tools/__init__.py",
    "viewer/shared/task-screenshot.js",
)
PAYLOAD_TREES = ("tools/offline_task_manager",)

# Developer documentation and fixtures the served app never reads. `THIN_README.md`
# is deliberately absent from this list: packager.py copies it into every thin
# package, so excluding it would ship a broken `build-package --mode thin`.
EXCLUDED_NAMES = ("README.md", "REFERENCE.md")
EXCLUDED_DIRS = ("__pycache__", "examples")
EXCLUDED_SUFFIXES = (".pyc", ".pyo")

# Anything matching these never belongs in a release artifact. This is a
# backstop against an allowlisted root being polluted, not the primary filter.
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".key", ".pem", ".env")
FORBIDDEN_STEMS = ("credentials", "session", "secrets", "passwords")

EXCLUSIONS_NOTE = (
    "credentials",
    "session secrets",
    "manager database and SQLite state",
    "runtime asset roots and task bundles",
    "developer documentation and CSV fixtures",
    "tests",
    "Python bytecode and caches",
)

ENTRYPOINT = (
    "python3 -m tools.offline_task_manager --db <state>/manager.db serve-online"
    " --credentials <state>/credentials.json --session-secret <state>/session.key"
    " --public-origin <origin> --port 4174 --base-path /sv-annotate"
)


class BuildError(RuntimeError):
    """A release cannot be built from this worktree."""


@dataclass(frozen=True)
class PayloadFile:
    """One packaged file, addressed by its path inside the release root."""

    path: str
    source: Path
    sha256: str
    bytes: int
    mode: str

    def entry(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "mode": self.mode,
        }


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def is_excluded(relative: Path) -> bool:
    if relative.name in EXCLUDED_NAMES or relative.suffix in EXCLUDED_SUFFIXES:
        return True
    return any(part in EXCLUDED_DIRS for part in relative.parts)


def reject_forbidden(relative: Path) -> None:
    """Fail loudly rather than shipping something that looks like state."""
    lowered = relative.name.lower()
    if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise BuildError(f"refusing to package {relative}: forbidden suffix")
    if any(lowered.startswith(stem) for stem in FORBIDDEN_STEMS):
        raise BuildError(f"refusing to package {relative}: looks like a secret")


def file_mode(source: Path) -> str:
    return "0755" if source.stat().st_mode & 0o111 else "0644"


def read_payload_file(repo: Path, relative: str) -> PayloadFile:
    source = repo / relative
    if not source.is_file():
        raise BuildError(f"payload file is missing: {relative}")
    reject_forbidden(Path(relative))
    body = source.read_bytes()
    return PayloadFile(
        path=relative,
        source=source,
        sha256=sha256_bytes(body),
        bytes=len(body),
        mode=file_mode(source),
    )


def collect_payload(repo: Path) -> tuple[PayloadFile, ...]:
    """Resolve the allowlist into a sorted, deduplicated payload."""
    relatives: list[str] = list(PAYLOAD_FILES)
    for tree in PAYLOAD_TREES:
        root = repo / tree
        if not root.is_dir():
            raise BuildError(f"payload tree is missing: {tree}")
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(repo)
            if is_excluded(relative):
                continue
            relatives.append(relative.as_posix())
    unique = sorted(set(relatives))
    if len(unique) != len(relatives):
        raise BuildError("payload allowlist contains a duplicate path")
    return tuple(read_payload_file(repo, relative) for relative in unique)


def untracked_entries(repo: Path, payload_paths: frozenset[str]) -> tuple[dict, ...]:
    """Record untracked files so a dirty release stays auditable."""
    listing = git(repo, "ls-files", "--others", "--exclude-standard")
    entries = []
    for relative in sorted(filter(None, listing.splitlines())):
        source = repo / relative
        if not source.is_file():
            continue
        body = source.read_bytes()
        entries.append(
            {
                "path": relative,
                "sha256": sha256_bytes(body),
                "bytes": len(body),
                "included_in_release": relative in payload_paths,
            }
        )
    return tuple(entries)


def source_provenance(repo: Path, payload_paths: frozenset[str]) -> dict:
    unstaged = git(repo, "diff")
    staged = git(repo, "diff", "--cached")
    combined = git(repo, "diff", "HEAD")
    return {
        "git_head": git(repo, "rev-parse", "HEAD"),
        "git_branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_dirty": bool(combined),
        "dirty_diff_sha256": sha256_text(combined),
        "unstaged_diff_sha256": sha256_text(unstaged),
        "staged_diff_sha256": sha256_text(staged),
        "untracked_files": list(untracked_entries(repo, payload_paths)),
    }


def build_manifest(repo: Path, payload: tuple[PayloadFile, ...]) -> dict:
    payload_paths = frozenset(item.path for item in payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "release_name": RELEASE_NAME,
        "source": source_provenance(repo, payload_paths),
        "runtime": {
            "python": ">=3.10",
            "dependencies": (
                "Python standard library only for the packaged online manager"
            ),
            "entrypoint": ENTRYPOINT,
            "trajectories": {
                "route": "POST /api/expert/trajectory",
                "storage": (
                    "--trajectory-root, defaulting to <state>/trajectories;"
                    " the route answers 503 until that directory exists"
                ),
            },
            "review_bundles": {
                "enabled": False,
                "configuration": (
                    "deploy/babyvision-task-manager.service omits"
                    " --review-bundles; serve_online receives no review bundle"
                    " path and loads an empty bundle map."
                ),
            },
        },
        "exclusions": list(EXCLUSIONS_NOTE),
        "payload": {
            "file_count": len(payload),
            "total_bytes": sum(item.bytes for item in payload),
            "files": [item.entry() for item in payload],
        },
    }


def sha256sums(manifest_bytes: bytes, payload: tuple[PayloadFile, ...]) -> str:
    lines = {item.path: item.sha256 for item in payload}
    lines["release-manifest.json"] = sha256_bytes(manifest_bytes)
    return "".join(f"{lines[path]}  {path}\n" for path in sorted(lines))


def write_artifact(
    destination: Path,
    payload: tuple[PayloadFile, ...],
    extra: tuple[tuple[str, bytes], ...],
    mtime: int,
) -> str:
    """Write a byte-reproducible tarball and return its sha256."""

    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = mtime
        return info

    # gzip stamps its own header mtime, so the stream is opened explicitly with
    # the pinned timestamp; "w:gz" would use the wall clock and defeat
    # reproducibility.
    raw = gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=destination.open("wb"), mtime=mtime)
    with raw, tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for item in payload:
            info = tar.gettarinfo(str(item.source), f"{RELEASE_NAME}/{item.path}")
            info.mode = 0o755 if item.mode == "0755" else 0o644
            with item.source.open("rb") as handle:
                tar.addfile(normalize(info), handle)
        for name, body in extra:
            info = tarfile.TarInfo(f"{RELEASE_NAME}/{name}")
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(normalize(info), io.BytesIO(body))
    return sha256_bytes(destination.read_bytes())


def build(repo: Path, out_root: Path) -> tuple[str, Path]:
    payload = collect_payload(repo)
    manifest = build_manifest(repo, payload)
    manifest_bytes = (
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    ).encode("utf-8")
    sums = sha256sums(manifest_bytes, payload).encode("utf-8")
    extra = (("release-manifest.json", manifest_bytes), ("SHA256SUMS", sums))
    mtime = int(git(repo, "log", "-1", "--format=%ct"))

    staging = out_root / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged_artifact = staging / "artifact.tar.gz"
    artifact_sha = write_artifact(staged_artifact, payload, extra, mtime)

    head = manifest["source"]["git_head"]
    release_id = f"{head}-{artifact_sha[:12]}"
    release_dir = out_root / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "release-manifest.json").write_bytes(manifest_bytes)
    (release_dir / "SHA256SUMS").write_bytes(sums)
    staged_artifact.replace(release_dir / "artifact.tar.gz")
    staged_artifact.parent.rmdir()
    return release_id, release_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    out_root = (args.out or repo / "dist").resolve()
    try:
        release_id, release_dir = build(repo, out_root)
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    manifest = json.loads((release_dir / "release-manifest.json").read_text())
    print(f"release_id     {release_id}")
    print(f"release_dir    {release_dir}")
    print(f"file_count     {manifest['payload']['file_count']}")
    print(f"total_bytes    {manifest['payload']['total_bytes']}")
    print(f"worktree_dirty {manifest['source']['worktree_dirty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
