"""Safe local persistence primitives used by all registries."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

from ..contracts import canonical_json, digest_bytes
from ..errors import InputError


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read JSON {path}: {exc}") from exc


def read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read YAML {path}: {exc}") from exc


def _atomic_replace(path: Path, content: bytes, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600 if private else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any, *, private: bool = False) -> str:
    content = canonical_json(value) + b"\n"
    _atomic_replace(path, content, private=private)
    return digest_bytes(content.rstrip(b"\n"))


def atomic_write_yaml(path: Path, value: Any, *, private: bool = False) -> str:
    try:
        text = yaml.safe_dump(value, allow_unicode=True, sort_keys=True, default_flow_style=False)
    except yaml.YAMLError as exc:
        raise InputError(f"cannot serialize YAML {path}: {exc}") from exc
    _atomic_replace(path, text.encode("utf-8"), private=private)
    return digest_bytes(text.encode("utf-8"))


def atomic_write_text(path: Path, text: str, *, private: bool = False) -> str:
    content = text.encode("utf-8")
    _atomic_replace(path, content, private=private)
    return digest_bytes(content)


def atomic_copy_file(source: Path, destination: Path, *, private: bool = False) -> str:
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read source file {source}: {exc}") from exc
    _atomic_replace(destination, content, private=private)
    return digest_bytes(content)
