"""Tests for viewer/manifest.json schema validation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.validate_manifest import validate_manifest_file, validate_manifest_obj

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "viewer" / "manifest.json"
SCHEMA_PATH = REPO_ROOT / "viewer" / "shared" / "manifest.schema.json"


@pytest.fixture
def valid_manifest() -> dict:
    """Load the committed production manifest as a fresh dict."""
    with MANIFEST_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture
def schema() -> dict:
    with SCHEMA_PATH.open() as fh:
        return json.load(fh)


def test_production_manifest_validates() -> None:
    """The shipped viewer/manifest.json must validate against its schema."""
    errors = validate_manifest_file(MANIFEST_PATH, SCHEMA_PATH)
    assert errors == [], f"Production manifest failed validation: {errors}"


@pytest.mark.parametrize("missing_key", ["brand", "version", "generated_at", "counts", "data", "docs", "domains"])
def test_missing_required_key_fails(valid_manifest: dict, schema: dict, missing_key: str) -> None:
    """Removing any required top-level key must produce a validation error."""
    bad = copy.deepcopy(valid_manifest)
    del bad[missing_key]
    errors = validate_manifest_obj(bad, schema)
    assert errors, f"Removing '{missing_key}' should have failed validation"


def test_counts_must_be_integer(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    bad["counts"]["assets"] = "zero"
    errors = validate_manifest_obj(bad, schema)
    assert errors, "String count should fail validation"


def test_negative_count_rejected(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    bad["counts"]["assets"] = -1
    errors = validate_manifest_obj(bad, schema)
    assert errors, "Negative count should fail validation"


def test_domain_color_must_be_hex(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    if not bad["domains"]:
        pytest.skip("manifest has no domains to mutate")
    bad["domains"][0]["color"] = "red"
    errors = validate_manifest_obj(bad, schema)
    assert errors, "Non-hex color should fail validation"


def test_domain_id_must_be_snake_case(valid_manifest: dict, schema: dict) -> None:
    bad = copy.deepcopy(valid_manifest)
    if not bad["domains"]:
        pytest.skip("manifest has no domains to mutate")
    bad["domains"][0]["id"] = "Bad-Id"
    errors = validate_manifest_obj(bad, schema)
    assert errors, "Non-snake_case id should fail validation"


def test_extra_top_level_key_allowed(valid_manifest: dict, schema: dict) -> None:
    """Forward compatibility: schema allows unknown top-level keys."""
    ok = copy.deepcopy(valid_manifest)
    ok["future_field"] = {"experimental": True}
    errors = validate_manifest_obj(ok, schema)
    assert errors == [], f"Extra top-level keys should be allowed: {errors}"
