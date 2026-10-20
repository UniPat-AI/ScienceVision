import hashlib
import json
from pathlib import Path
from typing import Any, Callable


def cache_key(path: Path, tool: str, args: dict) -> str:
    abs_path = str(Path(path).resolve())
    mtime_ns = Path(path).stat().st_mtime_ns
    args_json = json.dumps(args, sort_keys=True, separators=(",", ":"))
    payload = f"{abs_path}:{mtime_ns}:{tool}:{args_json}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def get_or_compute(key: str, compute_fn: Callable[[], Any], cache_dir: Path) -> Any:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists():
        try:
            with cache_file.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
    value = compute_fn()
    tmp = cache_file.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True, separators=(",", ":"))
    tmp.replace(cache_file)
    return value
