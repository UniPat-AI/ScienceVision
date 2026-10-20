#!/usr/bin/env python3
"""Validate static QA-Gen configuration and local knowledge fixtures.

This is intentionally a repository check, not a runtime import path. JSON
Schema covers document structure; this command also verifies constraints that
need filesystem resolution or comparison across multiple index entries.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path, PureWindowsPath
import re
import sys
from typing import Any

import yaml

try:
    from jsonschema import Draft7Validator, SchemaError
except ImportError as exc:  # pragma: no cover - depends on caller environment
    raise SystemExit(
        "jsonschema is required for static config validation; install the "
        "development validation dependency before running this command."
    ) from exc


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPOSITORY_ROOT / "configs" / "schemas"
SOURCE_ROOT = REPOSITORY_ROOT / "configs" / "sources"
KNOWLEDGE_ROOT = REPOSITORY_ROOT / "knowledge"
SOURCE_ROOT_FOR_IMPORT = REPOSITORY_ROOT / "src"

if str(SOURCE_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT_FOR_IMPORT))

from qagen.contracts import digest  # noqa: E402


FIXTURE_REFERENCE = re.compile(
    r"^fixture-subdomain-knowledge:"
    r"(?P<leaf_id>[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)"
    r"@(?P<digest>sha256:[0-9a-f]{64})$"
)
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class Reporter:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, location: Path | str, message: str) -> None:
        self.errors.append(f"{location}: {message}")


def load_json(path: Path, reporter: Reporter) -> Any | None:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        reporter.error(path, f"invalid JSON: {exc}")
        return None


def load_yaml(path: Path, reporter: Reporter) -> Any | None:
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        reporter.error(path, f"invalid YAML: {exc}")
        return None


def format_schema_location(error: Any) -> str:
    if not error.absolute_path:
        return "$"
    return "$" + "".join(f"[{part!r}]" for part in error.absolute_path)


def validate_instance(
    instance: Any,
    schema: Mapping[str, Any],
    path: Path,
    reporter: Reporter,
) -> None:
    for error in sorted(Draft7Validator(schema).iter_errors(instance), key=str):
        reporter.error(path, f"schema {format_schema_location(error)}: {error.message}")


def load_schemas(reporter: Reporter) -> dict[str, Mapping[str, Any]]:
    schemas: dict[str, Mapping[str, Any]] = {}
    for path in sorted(SCHEMA_ROOT.glob("*.json")):
        value = load_json(path, reporter)
        if not isinstance(value, Mapping):
            if value is not None:
                reporter.error(path, "schema document must be a JSON object")
            continue
        try:
            Draft7Validator.check_schema(value)
        except SchemaError as exc:
            reporter.error(path, f"invalid Draft 7 schema: {exc.message}")
            continue
        schemas[path.name] = value
    return schemas


def validate_file_against_schema(
    path: Path,
    schema_name: str,
    schemas: Mapping[str, Mapping[str, Any]],
    reporter: Reporter,
) -> Any | None:
    schema = schemas.get(schema_name)
    if schema is None:
        reporter.error(path, f"missing loaded schema {schema_name}")
        return None
    value = load_yaml(path, reporter)
    if value is not None:
        validate_instance(value, schema, path, reporter)
    return value


def validate_static_documents(
    schemas: Mapping[str, Mapping[str, Any]], reporter: Reporter
) -> Any | None:
    validation_sets = (
        (REPOSITORY_ROOT / "configs" / "base.yaml", "base.schema.json"),
        (REPOSITORY_ROOT / "configs" / "rubric.yaml", "rubric.schema.json"),
    )
    for path, schema_name in validation_sets:
        validate_file_against_schema(path, schema_name, schemas, reporter)
    for path in sorted((REPOSITORY_ROOT / "configs" / "domains").glob("*.yaml")):
        validate_file_against_schema(path, "domain.schema.json", schemas, reporter)
    for path in sorted((REPOSITORY_ROOT / "configs" / "profiles").glob("*.yaml")):
        validate_file_against_schema(path, "profile.schema.json", schemas, reporter)
    for path in sorted((REPOSITORY_ROOT / "configs" / "agents").glob("*.yaml")):
        value = load_yaml(path, reporter)
        if value is None:
            continue
        kinds = {
            "qagen_provider_runtime": "provider_runtime.schema.json",
            "qagen_provider_endpoint_approval": "provider_endpoint_approval.schema.json",
        }
        schema_name = kinds.get(value.get("kind")) if isinstance(value, Mapping) else "model_routing.schema.json"
        if schema_name is None:
            schema_name = "model_routing.schema.json"
        schema = schemas.get(schema_name)
        if schema is None:
            reporter.error(path, f"missing loaded schema {schema_name}")
            continue
        validate_instance(value, schema, path, reporter)
        if schema_name == "model_routing.schema.json":
            validate_model_routing(value, path, reporter)
    for path in sorted((REPOSITORY_ROOT / "configs" / "subdomains").glob("*/*.yaml")):
        validate_file_against_schema(path, "subdomain.schema.json", schemas, reporter)
    for path in sorted(SOURCE_ROOT.glob("*.yaml")):
        value = load_yaml(path, reporter)
        if value is None:
            continue
        source_schemas = {
            "qagen_curated_semantic_gt_source": "curated_semantic_gt_source.schema.json",
            "qagen_v32_private_source_spec": "v32_private_source_spec.schema.json",
        }
        schema_name = source_schemas.get(value.get("kind")) if isinstance(value, Mapping) else None
        schema = schemas.get(schema_name or "source.schema.json")
        if schema is None:
            reporter.error(path, f"missing loaded schema {schema_name or 'source.schema.json'}")
            continue
        validate_instance(value, schema, path, reporter)
    for path in sorted((SOURCE_ROOT / "aliases").glob("*.yaml")):
        validate_file_against_schema(path, "subdomain_aliases.schema.json", schemas, reporter)
    for path in sorted((KNOWLEDGE_ROOT / "compiled").glob("*.yaml")):
        validate_file_against_schema(path, "subdomain_knowledge.schema.json", schemas, reporter)
    return validate_file_against_schema(
        KNOWLEDGE_ROOT / "index.yaml", "knowledge_index.schema.json", schemas, reporter
    )


def resolve_knowledge_path(raw_path: Any, entry_path: str, reporter: Reporter) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        reporter.error(entry_path, "knowledge path must be a non-empty string")
        return None
    if (
        Path(raw_path).is_absolute()
        or PureWindowsPath(raw_path).is_absolute()
        or WINDOWS_DRIVE_PATH.match(raw_path)
    ):
        reporter.error(entry_path, f"knowledge path must be relative: {raw_path!r}")
        return None
    normalized_parts = raw_path.replace("\\", "/").split("/")
    if ".." in normalized_parts:
        reporter.error(entry_path, f"knowledge path must not contain '..': {raw_path!r}")
        return None

    knowledge_root = KNOWLEDGE_ROOT.resolve()
    candidate = (knowledge_root / raw_path).resolve()
    try:
        candidate.relative_to(knowledge_root)
    except ValueError:
        reporter.error(entry_path, f"knowledge path escapes knowledge root: {raw_path!r}")
        return None
    return candidate


def expected_index_state(payload_state: Any) -> str | None:
    states = {
        "sample_compile_eligible": "fixture_compile_eligible",
        "blocked": "fixture_blocked",
    }
    return states.get(payload_state)


def validate_local_fixture(
    entry: Mapping[str, Any], index: int, fixture_path: Path, reporter: Reporter
) -> None:
    entry_path = f"{KNOWLEDGE_ROOT / 'index.yaml'} entries[{index}]"
    if not fixture_path.is_file():
        reporter.error(entry_path, f"fixture path does not exist: {entry.get('path')!r}")
        return

    fixture = load_yaml(fixture_path, reporter)
    if not isinstance(fixture, Mapping):
        if fixture is not None:
            reporter.error(fixture_path, "fixture document must be an object")
        return

    payload = fixture.get("fixture_payload")
    if "fixture_payload" not in fixture:
        reporter.error(fixture_path, "fixture payload is required for digest verification")
        return
    try:
        actual_digest = digest(payload)
    except Exception as exc:  # Canonical serialization reports its own precise cause.
        reporter.error(fixture_path, f"fixture payload cannot be digested: {exc}")
        return

    if fixture.get("id") != entry.get("leaf_id"):
        reporter.error(entry_path, "leaf_id does not match the fixture id")
    if fixture.get("fixture_digest") != actual_digest:
        reporter.error(fixture_path, "fixture_digest does not match fixture_payload")
    if entry.get("digest") != actual_digest:
        reporter.error(entry_path, "digest does not match the fixture payload")

    reference = entry.get("reference")
    match = FIXTURE_REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
    if match is None:
        reporter.error(entry_path, "reference is not a valid fixture-subdomain-knowledge reference")
    else:
        if match.group("leaf_id") != entry.get("leaf_id"):
            reporter.error(entry_path, "reference leaf id does not match leaf_id")
        if match.group("digest") != entry.get("digest"):
            reporter.error(entry_path, "reference digest does not match digest")

    expected_state = expected_index_state(
        payload.get("state") if isinstance(payload, Mapping) else None
    )
    if expected_state is None:
        reporter.error(fixture_path, "fixture payload has no recognized state")
    elif entry.get("state") != expected_state:
        reporter.error(entry_path, "state does not match the fixture payload state")


def validate_knowledge_index(value: Any, reporter: Reporter) -> None:
    if not isinstance(value, Mapping):
        return
    entries = value.get("entries")
    if not isinstance(entries, list):
        return

    seen_leaf_ids: dict[str, int] = {}
    seen_references: dict[str, int] = {}
    for index, entry in enumerate(entries):
        entry_path = f"{KNOWLEDGE_ROOT / 'index.yaml'} entries[{index}]"
        if not isinstance(entry, Mapping):
            continue
        leaf_id = entry.get("leaf_id")
        if isinstance(leaf_id, str):
            prior = seen_leaf_ids.setdefault(leaf_id, index)
            if prior != index:
                reporter.error(entry_path, f"duplicate leaf_id {leaf_id!r}; first used by entries[{prior}]")
        reference = entry.get("reference")
        if isinstance(reference, str):
            prior = seen_references.setdefault(reference, index)
            if prior != index:
                reporter.error(entry_path, f"duplicate reference {reference!r}; first used by entries[{prior}]")
        knowledge_path = resolve_knowledge_path(entry.get("path"), entry_path, reporter)
        if entry.get("local_fixture") is True and knowledge_path is not None:
            validate_local_fixture(entry, index, knowledge_path, reporter)


def validate_model_routing(value: Any, path: Path, reporter: Reporter) -> None:
    if not isinstance(value, Mapping):
        return
    roles = value.get("roles")
    if not isinstance(roles, Mapping):
        return
    providers = value.get("providers")
    if value.get("schema_reference_state") == "planned":
        enabled_values = [value.get("enabled")]
        for role in roles.values():
            if isinstance(role, Mapping):
                enabled_values.append(role.get("enabled"))
        if isinstance(providers, Mapping):
            for provider in providers.values():
                if isinstance(provider, Mapping):
                    enabled_values.append(provider.get("configured"))
        fixture_adapter = value.get("fixture_adapter")
        if isinstance(fixture_adapter, Mapping):
            enabled_values.append(fixture_adapter.get("enabled"))
        if any(enabled is not False for enabled in enabled_values):
            reporter.error(path, "planned schema references require every route to remain disabled")
        if value.get("run_eligibility") != "blocked":
            reporter.error(path, "planned schema references require blocked run eligibility")
    author = roles.get("author")
    verifier = roles.get("verifier")
    if not isinstance(author, Mapping) or not isinstance(verifier, Mapping):
        return
    author_models = author.get("allowed_models")
    verifier_models = verifier.get("allowed_models")
    if not isinstance(author_models, list) or not isinstance(verifier_models, list):
        return
    author_pair = (author.get("provider"), tuple(author_models))
    verifier_pair = (verifier.get("provider"), tuple(verifier_models))
    if author_pair == verifier_pair:
        reporter.error(path, "verifier route must use a different provider and model from author")


def main() -> int:
    reporter = Reporter()
    schemas = load_schemas(reporter)
    index = validate_static_documents(schemas, reporter)
    validate_knowledge_index(index, reporter)
    if reporter.errors:
        print("Static configuration validation failed:", file=sys.stderr)
        for error in reporter.errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Static configuration validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
