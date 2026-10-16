#!/usr/bin/env python3
"""Validate schemas, documented references, and native performance artifacts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    def reject(value: str):
        raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(path.read_text(), parse_constant=reject)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def schema(name: str) -> Draft202012Validator:
    value = read_json(ROOT / "schemas" / name)
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value, format_checker=FormatChecker())


def verified_artifact(directory: Path, artifact: dict) -> Path:
    path = (directory / artifact["path"]).resolve()
    require(path.is_relative_to(directory.resolve()), "artifact escapes result directory")
    require(path.is_file(), f"missing artifact: {artifact['path']}")
    require(hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"],
            f"artifact hash mismatch: {artifact['path']}")
    return path


def numeric(value) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool)


def compare_means(actual: dict, rows: list[dict], field: str, group) -> None:
    groups = defaultdict(list)
    for row in rows:
        if numeric(row.get(field)):
            groups[group(row)].append(row[field])
    expected = {key: sum(values) / len(values) for key, values in groups.items()}
    require(set(actual) == set(expected), f"mean groups mismatch for {field}")
    for key, value in expected.items():
        require(math.isclose(actual[key], value, rel_tol=1e-9, abs_tol=1e-9),
                f"mean mismatch for {field}: {key}")


def validate_performance(directory: Path) -> None:
    metadata = read_json(directory / "run.json")
    schema("run-metadata.schema.json").validate(metadata)
    board_path = verified_artifact(directory, metadata["artifacts"]["leaderboard"])
    board = read_json(board_path)
    schema("performance.schema.json").validate(board)
    for artifact in metadata["artifacts"]["evidence"]:
        verified_artifact(directory, artifact)
    task_ids = read_json(verified_artifact(directory, metadata["scope"]["task_set"]))
    require(isinstance(task_ids, list) and all(isinstance(x, str) for x in task_ids),
            "task-set artifact must be an array of task IDs")
    require(len(task_ids) == len(set(task_ids)), "duplicate IDs in task-set artifact")
    rows = board["rows"]
    identities = Counter((x["task_id"], x["harness"], x["model"]) for x in rows)
    require(all(n <= metadata["scope"]["repeats"] for n in identities.values()),
            "collected rows exceed declared repetitions")
    for row in rows:
        require(row["task_id"] in task_ids, "row is outside the expected task set")
        require(row["harness"] in metadata["scope"]["routes"], "undeclared route")
        require(row["model"] == metadata["model"]["name"], "undeclared model")
        if row["reward"] is not None and row["answer_score"] is not None:
            require(math.isclose(row["reward"], row["answer_score"]),
                    "reward differs from deterministic answer_score")
    completed = [x for x in rows if x["reward"] is not None]
    eligible = [x for x in completed if x["evaluation_eligible"]]
    citable = [x for x in eligible if x["citable"] is True]
    blocked = [x for x in eligible if x["citable"] is False]
    ungated = [x for x in eligible if x["citable"] is None]
    counts = {"n_eligible": len(eligible), "n_citable": len(citable),
              "n_blocked": len(blocked), "n_ungated": len(ungated)}
    require(board["n_runs"] == len(rows), "n_runs mismatch")
    require(board["n_completed"] == len(completed), "n_completed mismatch")
    require(board["n_eligible_completed"] == len(eligible), "n_eligible_completed mismatch")
    require(all(board["citability"][k] == v for k, v in counts.items()), "census mismatch")
    expected = len(task_ids) * len(metadata["scope"]["routes"]) * metadata["scope"]["repeats"]
    coverage = {"expected": expected, "attempted": len(rows), "completed": len(completed),
                "eligible_completed": len(eligible), "ineligible_completed": len(completed) - len(eligible),
                "citable": len(citable), "blocked": len(blocked), "ungated": len(ungated),
                "missing": expected - len(rows), "error": sum(x["status"] == "error" for x in rows),
                "incomplete": sum(x["status"] == "incomplete" for x in rows)}
    require(metadata["coverage"] == coverage, "run metadata coverage mismatch")
    if metadata["record_kind"] == "benchmark":
        require(len(completed) == expected, "benchmark contains unscored expected trials")
    hm = lambda row: f"{row['harness']} / {row['model']}"
    hd = lambda row: f"{row['harness']} / {row['domain'] or '_unknown'}"
    by_group = defaultdict(list)
    for row in eligible:
        by_group[hm(row)].append(row)
    expected_census = {key: {"n_eligible": len(values),
                           "n_citable": sum(x["citable"] is True for x in values),
                           "n_blocked": sum(x["citable"] is False for x in values),
                           "n_ungated": sum(x["citable"] is None for x in values)}
                       for key, values in by_group.items()}
    require(board["by_harness_model_census"] == expected_census, "group census mismatch")
    for key, source, field, grouping in [
        ("by_harness_model_mean_reward", citable, "reward", hm),
        ("by_harness_model_mean_reward_all_eligible", eligible, "reward", hm),
        ("by_harness_domain_mean_reward", citable, "reward", hd),
        ("by_harness_domain_mean_reward_all_eligible", eligible, "reward", hd),
        ("by_harness_model_mean_answer", citable, "answer_score", hm),
        ("by_harness_model_mean_process", citable, "process_compliance", hm),
        ("by_harness_model_mean_capability_coverage", eligible, "capability_coverage", hm),
        ("by_harness_model_mean_mcp_success", [x for x in eligible if x["mcp_attempted"] == 1.0], "mcp_success_rate", hm),
    ]:
        compare_means(board[key], source, field, grouping)
    gated = [{**x, "citable_number": float(x["citable"])} for x in eligible if isinstance(x["citable"], bool)]
    compare_means(board["by_harness_model_citable_rate"], gated, "citable_number", hm)


def validate_references() -> None:
    dataset = read_json(ROOT / "resources/dataset.json")
    harness = read_json(ROOT / "resources/harness.json")
    images = read_json(ROOT / "resources/images.json")
    require(re.fullmatch(r"[0-9a-f]{40}", dataset["source"]["commit"]) is not None,
            "dataset commit must be pinned")
    require(re.fullmatch(r"[0-9a-f]{40}", harness["commit"]) is not None,
            "harness commit must be pinned")
    require(sum(dataset["domain_counts"].values()) == dataset["task_count"], "domain count mismatch")
    require(dataset["source"]["revision_type"] == "commit", "dataset revision must be a commit")
    require(dataset["source"]["revision_name"] == dataset["source"]["commit"],
            "dataset revision selector differs from the pinned commit")
    require(harness["dataset_compatibility"]["dataset_commit"] == dataset["source"]["commit"],
            "harness dataset compatibility reference differs from the pinned commit")
    references = set()
    for item in images["images"]:
        expected = item["canonical"].rsplit(":", 1)[0] + "@" + item["canonical_manifest_digest"]
        require(item["canonical_digest_reference"] == expected, "image reference mismatch")
        require(expected.startswith("ghcr.io/unipat-ai/"), "noncanonical image namespace")
        require(expected not in references, "duplicate image version")
        references.add(expected)


def validate_links() -> None:
    for path in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts[:-1]):
            continue
        text = path.read_text()
        require(sum(line.startswith("```") for line in text.splitlines()) % 2 == 0,
                f"unclosed code fence: {path.name}")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = target.split("#", 1)[0]
            if not target or urlparse(target).scheme:
                continue
            resolved = (path.parent / unquote(target)).resolve()
            require(resolved.is_relative_to(ROOT) and resolved.exists(),
                    f"broken local link in {path.relative_to(ROOT)}: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--performance-dir", type=Path)
    parser.add_argument("--legacy-index", type=Path, help="validate an external index.jsonl without copying it")
    args = parser.parse_args()
    for path in (ROOT / "schemas").glob("*.json"):
        schema(path.name)
    validate_references()
    validate_links()
    example = ROOT / "performance/examples"
    schema("dataset-index.schema.json").validate(read_json(example / "dataset-index-row.json"))
    validate_performance(args.performance_dir or example)
    count = 0
    if args.legacy_index:
        validator = schema("dataset-index.schema.json")
        for line in args.legacy_index.read_text().splitlines():
            if line.strip():
                validator.validate(json.loads(line))
                count += 1
    print(json.dumps({"status": "pass", "schemas": 3, "legacy_index_rows_checked": count,
                      "scope": "local references, links, schema and numeric consistency; no model or container execution"}))


if __name__ == "__main__":
    main()
