from __future__ import annotations

import hashlib
import json

from qagen.contracts import canonical_sha256


def test_canonical_sha256_is_stable_across_mapping_order() -> None:
    left = {"z": [3, {"b": 2, "a": 1}], "alpha": "value"}
    right = {"alpha": "value", "z": [3, {"a": 1, "b": 2}]}

    assert canonical_sha256(left) == canonical_sha256(right)


def test_canonical_sha256_matches_documented_utf8_json_encoding() -> None:
    value = {"name": "cafe", "unicode": "\u6d4b\u8bd5", "items": [2, 1]}
    expected_payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert canonical_sha256(value) == hashlib.sha256(expected_payload).hexdigest()


def test_canonical_sha256_keeps_list_order_semantic() -> None:
    assert canonical_sha256({"items": ["a", "b"]}) != canonical_sha256(
        {"items": ["b", "a"]}
    )
