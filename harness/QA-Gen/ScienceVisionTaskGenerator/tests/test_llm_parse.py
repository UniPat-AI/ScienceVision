"""Unit tests for sciencevision_taskgen.llm._parse.parse_json_lenient."""
from __future__ import annotations

import json

import pytest

from sciencevision_taskgen.llm import parse_json_lenient


def test_clean_object() -> None:
    assert parse_json_lenient('{"a": 1}') == {"a": 1}


def test_clean_array() -> None:
    assert parse_json_lenient("[1, 2, 3]") == [1, 2, 3]


def test_clean_with_whitespace() -> None:
    assert parse_json_lenient('   {"a": 1}   \n') == {"a": 1}


def test_fenced_with_json_language_tag() -> None:
    content = '```json\n{"instruction": "look at 1F88"}\n```'
    assert parse_json_lenient(content) == {"instruction": "look at 1F88"}


def test_fenced_without_language_tag() -> None:
    content = '```\n{"a": 1, "b": [2, 3]}\n```'
    assert parse_json_lenient(content) == {"a": 1, "b": [2, 3]}


def test_fenced_uppercase_tag() -> None:
    content = '```JSON\n[{"c": "x"}]\n```'
    assert parse_json_lenient(content) == [{"c": "x"}]


def test_fenced_array_payload() -> None:
    content = '```json\n[{"code": "x", "severity": "low", "detail": "y"}]\n```'
    parsed = parse_json_lenient(content)
    assert isinstance(parsed, list)
    assert parsed[0]["code"] == "x"


def test_prose_before_object_recovered_via_slice() -> None:
    content = 'Here is the JSON: {"a": 1, "b": 2} done.'
    assert parse_json_lenient(content) == {"a": 1, "b": 2}


def test_prose_before_array_recovered_via_slice() -> None:
    content = "Sure! [1, 2, 3] is the result."
    assert parse_json_lenient(content) == [1, 2, 3]


def test_fenced_with_trailing_text_after_fence() -> None:
    content = '```json\n{"a": 1}\n```\nHope that helps!'
    parsed = parse_json_lenient(content)
    assert parsed == {"a": 1}


def test_truly_malformed_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_lenient("this is not json at all")


def test_empty_string_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_lenient("")


def test_only_whitespace_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_lenient("   \n  \t ")


def test_non_string_input_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_lenient(None)  # type: ignore[arg-type]


def test_nested_braces_inside_string_value_preserved() -> None:
    content = '```json\n{"q": "a {nested} value"}\n```'
    assert parse_json_lenient(content) == {"q": "a {nested} value"}


def test_real_sonnet_response_pattern() -> None:
    """Pattern observed live: sonnet wraps the entire response in ```json."""
    content = (
        '```json\n'
        '{\n'
        '  "instruction": "Open hippocampus_001.nii and identify the slice.",\n'
        '  "scientific_action": "anatomical localization on MRI",\n'
        '  "asset_specific_rationale": "hippocampus segmentation visible"\n'
        '}\n'
        '```'
    )
    parsed = parse_json_lenient(content)
    assert parsed["instruction"].startswith("Open hippocampus_001")
    assert "scientific_action" in parsed
