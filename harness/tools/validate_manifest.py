"""Validate viewer/manifest.json against viewer/shared/manifest.schema.json.

CLI usage:
    python tools/validate_manifest.py
    python tools/validate_manifest.py --manifest other/manifest.json

Exit code 0 on success, 1 on validation failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "viewer" / "manifest.json"
DEFAULT_SCHEMA = REPO_ROOT / "viewer" / "shared" / "manifest.schema.json"


def validate_manifest_obj(manifest: dict, schema: dict) -> list[str]:
    """Validate a manifest object against a schema object.

    Returns a list of human-readable error messages; empty list means valid.
    Does not mutate either input.
    """
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(manifest), key=lambda e: e.path):
        location = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{location}: {err.message}")
    return errors


def validate_manifest_file(manifest_path: Path, schema_path: Path) -> list[str]:
    """Load files from disk and validate.

    Returns the same list-of-errors contract as validate_manifest_obj.
    Raises FileNotFoundError if either file is missing.
    """
    with manifest_path.open() as fh:
        manifest = json.load(fh)
    with schema_path.open() as fh:
        schema = json.load(fh)
    return validate_manifest_obj(manifest, schema)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST,
                        help="Path to manifest.json (default: viewer/manifest.json)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA,
                        help="Path to schema.json (default: viewer/shared/manifest.schema.json)")
    args = parser.parse_args(argv)

    errors = validate_manifest_file(args.manifest, args.schema)
    if errors:
        print(f"FAIL: {args.manifest} has {len(errors)} validation error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"OK: {args.manifest} validates against {args.schema}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
