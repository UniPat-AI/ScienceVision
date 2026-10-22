"""Versioned deterministic scoring for structured ScienceVision answers.

The scorer consumes one strict JSON object and evaluates a declared rubric. It
does not call a model and does not infer family-specific answer formats.

A field whose ground truth is a paragraph leaves the rubric before scoring
(``select_report_only``), because no answer reproduces a paragraph. That step
is not monotone: an item the answer passed may be the one that leaves, and
removing a passed weight from both terms of the weighted fraction lowers it
whenever the answer was not already perfect. A row can therefore lose score.
"Up on this corpus, down nowhere" is a measurement of that corpus and not a
property of this rule.
"""
from __future__ import annotations

import base64
import json
import math
import re
import zlib
from typing import Any, Callable

from sci3d_bench.scorers.base import ScoreResult

FINAL_JSON_MARKER = "SCI3D_FINAL_JSON:"
SCORER_NAME = "deterministic_structured_v2_1"
GENERIC_SCORER_NAME = "structured_deterministic_rubric_v1"
SCORER_ALIASES = frozenset(
    {
        SCORER_NAME,
        GENERIC_SCORER_NAME,
        "deterministic_answer_v2_1",
        "answer_score",
        # Deployment-only widening. The v9 tree builder writes `exact_match`
        # into every task.toml; `tolerance_match` and `expert_rubric` appear in
        # older trees. Without them the registry resolves no scorer at all and
        # the trial is scored `evaluation_eligible: false` with no
        # `answer_score` -- a silent zero. Added by
        # `meta/make_deployment_scorer.py`; never in the release contract.
        "exact_match",
        "tolerance_match",
        "expert_rubric",
    }
)
RUBRIC_VERSION = "sciencevision.deterministic_structured.v2.1"
_RUBRIC_VERSION_ALIASES = frozenset({RUBRIC_VERSION, "2.1", "v2.1"})
GENERIC_RUBRIC_VERSION = "sciencevision.generic_deterministic_rubric.v1"


class AnswerParseError(ValueError):
    """Stable parse failure emitted in scorer audit output."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject_constant(_: str) -> None:
    raise AnswerParseError("non_finite_number")


def _validate_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise AnswerParseError("non_finite_number")
    if isinstance(value, dict):
        for child in value.values():
            _validate_finite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_finite(child)


def parse_strict_answer(answer: Any) -> Any:
    """Return one native JSON value from a terminal answer.

    Native values written by the sci3d harness are accepted directly. Generic
    agent text must be exactly one JSON value or contain exactly one terminal
    marker followed by exactly one JSON value. Prose and fenced-JSON recovery is
    intentionally unsupported.
    """
    if answer is None:
        raise AnswerParseError("missing_answer")
    if not isinstance(answer, str):
        _validate_finite(answer)
        return answer

    text = answer.strip()
    if not text:
        raise AnswerParseError("missing_answer")
    marker_count = text.count(FINAL_JSON_MARKER)
    if marker_count > 1:
        raise AnswerParseError("multiple_final_json_markers")
    payload = text
    if marker_count == 1:
        payload = text.split(FINAL_JSON_MARKER, 1)[1].strip()
        if not payload:
            raise AnswerParseError("empty_final_json_payload")
    try:
        parsed = json.loads(payload, parse_constant=_reject_constant)
    except AnswerParseError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AnswerParseError("invalid_json") from exc
    _validate_finite(parsed)
    return parsed


def _path_get(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


#: An underscore is a word separator that some producers spell as a space.
#: Interior only, and a swap rather than a delete: ``"2_6"`` becomes
#: ``"2 6"``, never ``"26"``.
_UNDERSCORE = "_"

#: Prose that opens with an article says the same thing without it. Judged on
#: the string **as written** and dropped only when that string carries at least
#: three tokens -- two remain after the drop. A bare ``"a b"`` is more often an
#: identifier pair than a sentence, and a single-letter value such as ``"A"``
#: is a label, not an article.
_LEADING_ARTICLE = re.compile(r"^(?:a|an|the) ")


def _canonical_plain(value: str) -> str:
    """The pre-F5 form: strip, casefold, collapse whitespace."""
    return " ".join(value.strip().casefold().split())


def _canonical_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # The article is judged before the underscore swap, and on the string as
    # written. Swapping first would manufacture token boundaries out of a
    # single identifier and then delete a letter that was never an article --
    # ``"a_b_c"`` is one token, and must not collapse onto ``"b c"``.
    text = _canonical_plain(value)
    if text.count(" ") >= 2:
        text = _LEADING_ARTICLE.sub("", text, count=1) or text
    text = " ".join(text.replace(_UNDERSCORE, " ").split())
    if not text:
        # ``"___"`` reduces to nothing, but it is not the empty answer.
        # Falling back keeps a non-empty input from collapsing onto ``""``.
        return _canonical_plain(value)
    return text


def _canonical_structure_string(value: Any) -> str | None:
    """The pre-F5 base, for the canonicalisers that parse a structure.

    These three do not compare spellings, they parse one: each ends in a
    transformation that is already blind to where the delimiters sit --
    ``"second_render"`` and ``"second render"`` have been equal under
    ``_canonical_residue`` since long before F5, both reducing to
    ``secondrender``. An article-dropping, prose-oriented rule inside such a
    parser is a category error, and a lossy one: it can split a pair the parser
    used to call equal, which is the one direction this change may not move.
    Pinned to the pre-F5 base, with the same type guard the base's callers had.
    """
    if not isinstance(value, str):
        return None
    return _canonical_plain(value)


def _canonical_asset_id(value: Any) -> str | None:
    normalized = _canonical_structure_string(value)
    if normalized is None:
        return None
    normalized = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    if ":" in normalized:
        normalized = normalized.rsplit(":", 1)[-1]
    return re.sub(r"\.(?:cif|mmcif|pdb|xyz|sdf)$", "", normalized)


def _canonical_residue(value: Any) -> str | None:
    normalized = _canonical_structure_string(value)
    if normalized is None:
        return None
    match = re.fullmatch(
        r"(?:chain\s*)?([a-z0-9]+)\s*[/ :]\s*([a-z]{1,4})\s*(-?\d+[a-z]?)",
        normalized,
    )
    if match:
        return f"{match.group(1)}/{match.group(2)}{match.group(3)}"
    match = re.fullmatch(
        r"([a-z]{1,4})\s*(-?\d+[a-z]?)\s+(?:chain\s*)?([a-z0-9]+)",
        normalized,
    )
    if match:
        return f"{match.group(3)}/{match.group(1)}{match.group(2)}"
    return re.sub(r"[^a-z0-9]", "", normalized)


def _canonical_space_group(value: Any) -> str | None:
    normalized = _canonical_structure_string(value)
    if normalized is None:
        return None
    try:
        import gemmi

        group = gemmi.find_spacegroup_by_name(value.strip())
    except (ImportError, ValueError, AttributeError):
        group = None
    if group is not None:
        return f"hall:{group.hall}"
    return "hm:" + re.sub(r"\s+", "", normalized)


_ATOM_KEY_ALIASES = {
    "model": "model",
    "model_id": "model",
    "model_index": "model",
    "chain": "chain",
    "chain_id": "chain",
    "asym_id": "chain",
    "residue": "residue_name",
    "resname": "residue_name",
    "residue_name": "residue_name",
    "comp_id": "residue_name",
    "seqid": "residue_number",
    "seq_id": "residue_number",
    "residue_id": "residue_number",
    "residue_number": "residue_number",
    "resseq": "residue_number",
    "icode": "insertion_code",
    "ins_code": "insertion_code",
    "insertion_code": "insertion_code",
    "atom": "atom_name",
    "atom_id": "atom_name",
    "atom_name": "atom_name",
    "alt_id": "altloc",
    "altloc": "altloc",
}
# An atom identity is only meaningful against a scheme, and the two file
# families this benchmark reads use different ones. A macromolecular structure
# locates an atom by chain plus residue plus name; a small-molecule crystal has
# neither a chain nor a residue and offers only ``_atom_site_label`` with its
# element. A dict is accepted as an atom identity if it carries either scheme's
# keys, and equality is still decided over the whole canonicalised dict, so
# covering a second scheme widens what is *parseable*, never what counts as
# equal.
_ATOM_IDENTITY_SCHEMES = (
    frozenset({"chain", "residue_name", "residue_number", "atom_name"}),
    frozenset({"element", "label"}),
)


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, str):
        # The atom-identity route normalises its own dict fields, so it must
        # agree with every other string comparison about what counts as the
        # same spelling. `_canonical_string` never returns None for a str.
        return _canonical_string(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return value


def _canonical_atom_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            return None
        key_text = raw_key.strip().casefold()
        key = _ATOM_KEY_ALIASES.get(key_text, key_text)
        canonical_value = _canonical_scalar(raw_value)
        if key in normalized and normalized[key] != canonical_value:
            return None
        normalized[key] = canonical_value
    if not any(scheme.issubset(normalized) for scheme in _ATOM_IDENTITY_SCHEMES):
        return None
    return normalized


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _all_numeric_leaves(value: Any) -> bool:
    """Every leaf is a number, at any nesting depth."""
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_all_numeric_leaves(item) for item in value)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _within_tolerance(left: float, right: float, tolerance: float) -> bool:
    distance = abs(left - right)
    return distance <= tolerance or math.isclose(
        distance, tolerance, rel_tol=1e-12, abs_tol=1e-12
    )


def _as_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes"}:
            return True
        if normalized in {"false", "no"}:
            return False
    return None


def _compare_exact(predicted: Any, expected: Any, _: dict) -> bool:
    return type(predicted) is type(expected) and predicted == expected


def _compare_string(predicted: Any, expected: Any, item: dict) -> bool:
    left = _canonical_string(predicted)
    right = _canonical_string(expected)
    if left is not None and left == right:
        return True
    # `accepted` is the author saying "this field is one identifier and these
    # are the other spellings of it". It widens string comparison only, so a
    # numeric field carrying a list here still goes through its own comparator.
    if left is None:
        return False
    for alternative in item.get("accepted") or ():
        if _canonical_string(alternative) == left:
            return True
    return False


# A building code cited in prose. Every model paraphrases the sentence and one
# answered in Chinese, so the words can never be compared; the (standard,
# clause) pair inside them can, and that pair does not depend on the language.
# A standard number is one to five digits: JGJ and CJJ number codes from 1
# (``JGJ 3-2010``, ``JGJ 100-2015``), GB in the tens of thousands; a local
# standard may be recommended (``DB11/T``).
_STANDARD_RE = re.compile(
    r"\b(GB\s*/?\s*T?|JGJ|CJJ|DB\s*\d{2}(?:\s*/\s*T)?)\s*(\d{1,5})(?:\s*[-—–]\s*(\d{4}))?",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(
    r"(?:article|clause|第)\s*(\d+(?:\.\d+)+)\s*条?"
    r"(?:\s*[（(]\s*(\d+)\s*[)）]"
    r"|\s*第\s*(\d+)\s*[款项段]"
    r"|\s*,?\s*(?:paragraph|para)\s*(\d+))?",
    re.IGNORECASE,
)
# ``GB50243-2016 6.2.2``: a dotted number written straight after the standard
# is its clause. Read only at the standard's end, and only with three levels
# (chapter.section.article), so an edition year such as ``(2018 Edition)`` or
# a load such as ``12.5 kN/m2`` is never taken for one.
_BARE_CLAUSE_RE = re.compile(r"\s*[,，]?\s*(\d+\.\d+\.\d+(?:\.\d+)*)")


def _canonical_standard(match: re.Match[str]) -> tuple[str, str | None]:
    """``(code, year)``: ``("GB50243", "2016")``; the year may be absent."""
    prefix = re.sub(r"[^A-Za-z0-9]", "", match.group(1)).upper()
    return f"{prefix}{match.group(2)}", match.group(3)


def _cited_clause(value: Any) -> tuple[str, str | None, str, str | None] | None:
    """``(standard, year, clause, sub)`` when the text cites one, else ``None``."""
    if not isinstance(value, str):
        return None
    standard = _STANDARD_RE.search(value)
    if standard is None:
        return None
    code, year = _canonical_standard(standard)
    clause = _CLAUSE_RE.search(value)
    if clause is not None:
        sub = next((group for group in clause.groups()[1:] if group is not None), None)
        return code, year, clause.group(1), sub
    bare = _BARE_CLAUSE_RE.match(value, standard.end())
    if bare is not None:
        return code, year, bare.group(1), None
    return None


def _compare_cited_clause(predicted: Any, expected: Any, item: dict) -> bool:
    """Additive: only fires when *both* sides cite a code, else as before.

    A gold that names no sub-clause does not constrain one -- the answer that
    prompted this cites the article and adds the paragraph, which is more
    precise, not wrong. The edition year works the same way: an answer that
    omits it cites the same code; two different years are two editions.
    """
    left = _cited_clause(predicted)
    right = _cited_clause(expected)
    if left is None or right is None:
        return _compare_string(predicted, expected, item)
    standard, year, clause, sub = left
    gold_standard, gold_year, gold_clause, gold_sub = right
    if standard != gold_standard or clause != gold_clause:
        return False
    if year is not None and gold_year is not None and year != gold_year:
        return False
    return gold_sub is None or sub == gold_sub


#: A trailing annotation such as ``"cylindrical (rotation plus translation)"``.
_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*$")

_CONTAINS_WHITESPACE = re.compile(r"\s")


def derive_aliases(value: str) -> list[str]:
    """Mechanically-derived alternative forms for a controlled-vocabulary value.

    Deliberately narrow. An alias asserts that a *different* answer is also
    correct, which is a claim about the task, not a formatting detail. One
    shape qualifies: a value whose trailing annotation can be dropped -- and
    dropping that annotation is a formatting reduction only when what remains
    is a single token. A leading letter is NOT derived: "D. rotates ..." may be
    option D, but "E. coli O157:H7" is a species, and the gold alone cannot
    tell them apart; a real choice letter is authored in ``accepted``. `"cylindrical (rotation plus translation)"` is one value
    written two ways; `"O-H...O hydrogen bonds (C-H...O secondary)"` is a
    proposition, and an agent that omits the secondary interaction has said
    something less, so the sentence-length remainder is left alone.
    """
    out: list[str] = []
    stripped = _TRAILING_PARENTHETICAL.sub("", value).strip()
    if stripped and stripped != value and not _CONTAINS_WHITESPACE.search(stripped):
        out.append(stripped)
    return out


def _compare_string_in_set(predicted: Any, expected: Any, item: dict) -> bool:
    left = _canonical_string(predicted)
    if left is None:
        return False
    # An explicit rubric item is taken verbatim from the task's config, so the
    # list is validated here rather than assumed: a ``null`` or a bare string
    # would otherwise raise out of the scoring loop instead of failing closed.
    accepted = item.get("accepted")
    candidates = [expected, *(accepted if isinstance(accepted, (list, tuple)) else [])]
    return any(left == _canonical_string(c) for c in candidates)


def _compare_boolean(predicted: Any, expected: Any, _: dict) -> bool:
    left = _as_boolean(predicted)
    right = _as_boolean(expected)
    return left is not None and left == right


def _compare_integer(predicted: Any, expected: Any, _: dict) -> bool:
    # A count written as a string is the same count: a template that shows
    # ``""`` puts every answer in quotes. Only a string that is wholly an
    # integer qualifies, so ``"500 mm"`` and ``"500.5"`` still fail.
    left = _numeric_literal(predicted) if isinstance(predicted, str) else predicted
    right = _numeric_literal(expected) if isinstance(expected, str) else expected
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    return isinstance(left, int) and isinstance(right, int) and left == right


def _compare_abs_tolerance(predicted: Any, expected: Any, item: dict) -> bool:
    left = _measure(predicted)
    right = _measure(expected)
    tolerance = _number(item.get("tolerance"))
    return (
        left is not None
        and right is not None
        and tolerance is not None
        and _within_tolerance(left, right, tolerance)
    )


def _compare_numeric_literal(predicted: Any, expected: Any, item: dict) -> bool:
    left = _numeric_literal(predicted) if isinstance(predicted, str) else predicted
    right = _numeric_literal(expected) if isinstance(expected, str) else expected
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not (isinstance(left, (int, float)) and isinstance(right, (int, float))):
        return False
    tolerance = _number(item.get("tolerance"))
    if tolerance is None:
        return float(left) == float(right)
    return _within_tolerance(float(left), float(right), tolerance)


def _component_tolerances(item: dict, length: int) -> list[float] | None:
    raw = item.get("component_tolerances", item.get("tolerance"))
    scalar = _number(raw)
    if scalar is not None:
        return [scalar] * length
    if not isinstance(raw, (list, tuple)) or len(raw) != length:
        return None
    values = [_number(value) for value in raw]
    if any(value is None for value in values):
        return None
    return [float(value) for value in values if value is not None]


def _compare_vector_tolerance(predicted: Any, expected: Any, item: dict) -> bool:
    """Compare two numeric sequences leaf by leaf, at any nesting depth.

    A field can hold one value per compared structure — an extent triple for
    each of two structures — so the sequence nests. The component tolerance for
    a nested element applies to every leaf beneath it, which is what
    broadcasting it into the recursive call expresses. Shape is still required
    to match exactly: a length mismatch, a scalar where a sequence is expected,
    or a non-numeric leaf all fail closed.
    """
    if not isinstance(predicted, (list, tuple)) or not isinstance(
        expected, (list, tuple)
    ):
        return False
    if len(predicted) != len(expected):
        return False
    tolerances = _component_tolerances(item, len(expected))
    if tolerances is None:
        return False
    for left_raw, right_raw, tolerance in zip(predicted, expected, tolerances):
        if isinstance(right_raw, (list, tuple)):
            nested = {**item, "component_tolerances": tolerance}
            if not _compare_vector_tolerance(left_raw, right_raw, nested):
                return False
            continue
        left = _measure(left_raw)
        right = _measure(right_raw)
        if left is None or right is None or not _within_tolerance(left, right, tolerance):
            return False
    return True


def _compare_circular_tolerance(predicted: Any, expected: Any, item: dict) -> bool:
    # This comparator owns every ``*_angle_deg`` and ``dihedral_*`` field, so a
    # measurement stored as a string reaches it on a numeric-string ground truth
    # without ever passing the ``numeric_literal_equal`` branch above. Reading a
    # string side through ``_number`` returned None and failed the task whatever
    # the agent answered, so both sides normalise first; the modulus arithmetic
    # below is untouched.
    left = _numeric_literal(predicted) if isinstance(predicted, str) else _number(predicted)
    right = _numeric_literal(expected) if isinstance(expected, str) else _number(expected)
    tolerance = _number(item.get("tolerance"))
    if left is None or right is None or tolerance is None:
        return False
    modulus = _number(item.get("periodic_modulus", 360.0))
    if modulus is None or modulus <= 0:
        return False
    distance = abs((left - right + modulus / 2.0) % modulus - modulus / 2.0)
    return distance <= tolerance or math.isclose(
        distance, tolerance, rel_tol=1e-12, abs_tol=1e-12
    )


def _compare_atom_identity(predicted: Any, expected: Any, _: dict) -> bool:
    if isinstance(predicted, str) or isinstance(expected, str):
        if not isinstance(predicted, str) or not isinstance(expected, str):
            return False
        left = _canonical_string(predicted)
        right = _canonical_string(expected)
        return left is not None and left == right
    # A conformer loaded from ``.xyz`` has no chain/residue/atom name to name
    # an atom with, so its questions identify atoms by their index and declare
    # ``<integer>``. Those fields still route here on their name, and the object
    # branch below answers None for anything that is not a dict -- two identical
    # integers compared False.
    left_number = _number(predicted)
    right_number = _number(expected)
    if left_number is not None or right_number is not None:
        return (
            left_number is not None
            and right_number is not None
            and left_number == right_number
        )
    left_object = _canonical_atom_object(predicted)
    right_object = _canonical_atom_object(expected)
    return left_object is not None and left_object == right_object


def _compare_asset_id(predicted: Any, expected: Any, _: dict) -> bool:
    left = _canonical_asset_id(predicted)
    right = _canonical_asset_id(expected)
    return left is not None and left == right


def _compare_residue(predicted: Any, expected: Any, _: dict) -> bool:
    left = _canonical_residue(predicted)
    right = _canonical_residue(expected)
    return left is not None and left == right


def _compare_space_group(predicted: Any, expected: Any, _: dict) -> bool:
    left = _canonical_space_group(predicted)
    right = _canonical_space_group(expected)
    return left is not None and left == right


#: Stands in for a JSON null so it survives the canonical-form round trip
#: (``None`` is the failure signal of ``_canonical_leaf``, so a genuine null
#: needs its own representable value).
_NULL = ("__null__",)

#: Fields whose elements name themselves, so any permutation is the same answer
#: ("O of ALA 17 in chain 2X" denotes that atom whatever its position in the
#: list). Explicit rather than a shape test, so no field is loosened by accident.
SET_EQUAL_FIELDS = frozenset({
    "atom_labels",
    "interaction_types",
    "cited_clause",
    "screenshots",
    "views",
    "longest_axes",
    "residue_numbers",
    "support_layout_issues",
    "panels_showing_the_movement",
    # ``coordinating_atoms`` is a set of atoms the answer is asked to enumerate,
    # with no order stated anywhere in the question. Each element carries its
    # own identity, so a permutation is the same answer.
    "coordinating_atoms",
})

#: Fields whose elements are POSITIONAL -- ``extent_vox`` is ["x", "y", "z"],
#: and swapping two entries is a different, wrong answer. Grading these as a
#: set would accept that wrong answer, which is why the field decides, not the
#: shape.
#:
#: The name says "string" because ``extent_vox`` held the slot alone; the
#: comparator itself is positional equality over canonical leaves at any
#: nesting depth, so lists of objects belong here too when their question
#: orders them. These three do, and each question says so in its own words:
#: ``residues`` ("按链编号的字母顺序排列；同一链内按残基编号升序"), ``measurements``
#: ("按非洋红色链的链编号字母顺序排列"), ``waters`` ("按链编号、残基编号升序排列").
#: Without an entry they fall to ``vector_abs_tolerance`` -- which has no
#: numeric tolerance to find and void the whole task as invalid_task_contract,
#: whatever the agent answered.
ORDERED_STRING_VECTOR_FIELDS = frozenset({
    "extent_vox",
    "residues",
    "measurements",
    "waters",
})


def _canonical_leaf(value: Any) -> Any:
    """Hashable canonical form of one value, or None when it is not comparable.

    Applies the same normalisation the scalar comparators already use per type
    -- strings through ``_canonical_string``, numbers through ``_number`` --
    recursively, so a list of objects is compared on its content rather than on
    its key order.
    """
    if value is None:
        return _NULL
    if isinstance(value, dict):
        out = []
        for key in sorted(value, key=str):
            child = _canonical_leaf(value[key])
            if child is None:
                return None
            out.append((str(key).strip().casefold(), child))
        return tuple(out)
    if isinstance(value, (list, tuple)):
        out = []
        for child in value:
            got = _canonical_leaf(child)
            if got is None:
                return None
            out.append(got)
        return tuple(out)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _canonical_string(value)
    return _number(value)


def _canonical_leaves(value: Any) -> list[Any] | None:
    if not isinstance(value, (list, tuple)):
        return None
    out = [_canonical_leaf(item) for item in value]
    return None if any(item is None for item in out) else out


def _compare_set_equal(predicted: Any, expected: Any, _: dict) -> bool:
    """Order-insensitive equality; multiplicity still counts."""
    left = _canonical_leaves(predicted)
    right = _canonical_leaves(expected)
    if left is None or right is None:
        return False
    return sorted(left, key=repr) == sorted(right, key=repr)


def _compare_ordered_string_vector(predicted: Any, expected: Any, _: dict) -> bool:
    """Positional equality, for lists whose index carries meaning."""
    left = _canonical_leaves(predicted)
    right = _canonical_leaves(expected)
    if left is None or right is None:
        return False
    return left == right


def _voxel_point(value: Any) -> tuple[int, int, int] | None:
    """A 3-list of whole numbers as an integer triple, or None.

    ``9.0`` names the voxel 9 -- a model that emits floats meant that voxel --
    but ``9.4`` does not, because rounding it would invent an answer. Booleans
    are rejected outright: ``True`` is not an index.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    out: list[int] = []
    for item in value:
        if isinstance(item, bool):
            return None
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, float) and item.is_integer():
            out.append(int(item))
        else:
            return None
    return out[0], out[1], out[2]


def _member_set_bits(item: dict) -> tuple[bytes, tuple[int, int, int]] | None:
    """Decode a rubric item's packed voxel region, or None when unusable.

    The format is the one ``scripts.harbor._membership_rules`` writes:
    ``base64(zlib(bit-packed occupancy))``, C-order over ``member_shape``,
    least significant bit first within each byte.
    """
    shape = item.get("member_shape")
    if (
        not isinstance(shape, (list, tuple))
        or len(shape) != 3
        or any(
            isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0
            for dim in shape
        )
    ):
        return None
    blob = item.get("member_set")
    if not isinstance(blob, str) or not blob:
        return None
    try:
        packed = zlib.decompress(base64.b64decode(blob, validate=True))
    except (ValueError, zlib.error):
        return None
    expected = (shape[0] * shape[1] * shape[2] + 7) // 8
    if len(packed) != expected:
        return None
    return packed, (shape[0], shape[1], shape[2])


def _compare_voxel_member_of_set(predicted: Any, expected: Any, item: dict) -> bool:
    """Whether the predicted voxel index lies inside the item's packed region.

    The mask *is* the tolerance region, so there is no numeric tolerance to
    tune: the answer scores when it lands on a region voxel. Out-of-bounds is a
    wrong answer, not a contract fault. ``expected`` (the audit point kept in
    the ground truth) takes no part in the check.
    """
    del expected
    point = _voxel_point(predicted)
    if point is None:
        return False
    decoded = _member_set_bits(item)
    if decoded is None:
        # Unreachable after _compile_rubric validation; a broken item must not
        # crash a scoring run.
        return False
    packed, (dim_0, dim_1, dim_2) = decoded
    x, y, z = point
    if not (0 <= x < dim_0 and 0 <= y < dim_1 and 0 <= z < dim_2):
        return False
    index = (x * dim_1 + y) * dim_2 + z
    return bool((packed[index >> 3] >> (index & 7)) & 1)


#: A leaf has no derived band, so a banded comparator reads it at zero width.
_BANDED_LEAF = frozenset({"circular_tolerance", "vector_abs_tolerance"})


def _compare_leaf(name: str, predicted: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return _compare_object_leafwise(predicted, expected, {})
    # Monotone against the `exact` this replaced: the old whole-object `==`
    # let 14000 equal 14000.0 and True equal 1 inside an object, so a leaf
    # that compares equal that way still passes whatever its comparator says.
    if predicted == expected:
        return True
    comparator, _ = _default_comparator(name, expected, {})
    if comparator == "vector_abs_tolerance" and not _all_numeric_leaves(expected):
        return False
    leaf = {"tolerance": 0.0} if comparator in _BANDED_LEAF else {}
    return _COMPARATORS[comparator](predicted, expected, leaf)


def _compare_object_leafwise(predicted: Any, expected: Any, _: dict) -> bool:
    """Every non-null leaf of the gold object passes its own comparator.

    One field, one verdict: the object passes only when all of its graded
    leaves do, so a leaf the question dictates (a unit) never pays for a wrong
    measurement beside it. A ``null`` gold leaf is a value nobody recorded and
    is not graded; extra keys in the answer are ignored, missing ones fail. An
    object with no graded leaf at all keeps exact equality: no evidence is not
    a pass.
    """
    if not isinstance(expected, dict) or not isinstance(predicted, dict):
        return False
    graded = [(key, gold) for key, gold in expected.items() if gold is not None]
    if not graded:
        return _compare_exact(predicted, expected, {})
    for key, gold in graded:
        if key not in predicted or not _compare_leaf(str(key), predicted[key], gold):
            return False
    return True


_COMPARATORS: dict[str, Callable[[Any, Any, dict], bool]] = {
    "exact": _compare_exact,
    "canonical_string_equal": _compare_string,
    "canonical_string_in_set": _compare_string_in_set,
    "canonical_enum_equal": _compare_string,
    "cited_clause_equal": _compare_cited_clause,
    "boolean_equal": _compare_boolean,
    "integer_equal": _compare_integer,
    "numeric_literal_equal": _compare_numeric_literal,
    "abs_tolerance": _compare_abs_tolerance,
    "vector_abs_tolerance": _compare_vector_tolerance,
    "circular_tolerance": _compare_circular_tolerance,
    "canonical_atom_id_equal": _compare_atom_identity,
    "canonical_atom_identity_equal": _compare_atom_identity,
    "canonical_asset_id_equal": _compare_asset_id,
    "canonical_residue_equal": _compare_residue,
    "canonical_space_group_equal": _compare_space_group,
    "canonical_set_equal": _compare_set_equal,
    "ordered_string_vector_equal": _compare_ordered_string_vector,
    "voxel_member_of_set": _compare_voxel_member_of_set,
    "object_leafwise": _compare_object_leafwise,
}


def _schema_fields(task: Any) -> list[str]:
    fields = getattr(getattr(task, "answer_schema", None), "fields", None)
    if isinstance(fields, dict):
        return [str(field) for field in fields]
    if isinstance(fields, (list, tuple)):
        return [str(field) for field in fields]
    return []


def _scorer_config(task: Any) -> dict:
    scorer = getattr(task, "scorer", None)
    config = getattr(scorer, "config", None)
    if isinstance(config, dict):
        return dict(config)
    return {}


def _ground_truth_object(task: Any) -> tuple[dict | None, list[str]]:
    ground_truth = getattr(task, "ground_truth", None)
    value = getattr(ground_truth, "value", None) if ground_truth is not None else None
    fields = _schema_fields(task)
    if value is None or value == "needs_expert_annotation":
        return None, ["ground_truth_unavailable"]
    if isinstance(value, dict):
        if fields and set(fields) != set(value):
            return None, ["answer_schema_ground_truth_fields_mismatch"]
        return value, []
    if len(fields) == 1:
        return {fields[0]: value}, []
    return None, ["scalar_ground_truth_requires_one_answer_field"]


def _tolerance_for(field: str, config: dict) -> float | list[float] | None:
    tolerance = config.get("tolerance")
    if _number(tolerance) is not None:
        return float(tolerance)
    if not isinstance(tolerance, dict):
        return None
    candidates = [field]
    for prefix in ("contact_", "clash_"):
        if field.startswith(prefix):
            candidates.append(field[len(prefix):])
    for candidate in candidates:
        value = tolerance.get(candidate)
        if _number(value) is not None:
            return float(value)
        if (
            isinstance(value, list)
            and value
            and all(_number(item) is not None for item in value)
        ):
            return [float(item) for item in value]
    return None


# Fields whose gold is a sentence citing a building code. Named here rather
# than inferred: the sentence is what the question asks for, but the code and
# clause inside it are the only part that is stable across answers.
CITED_CLAUSE_FIELDS = frozenset({"cited_clause"})

#: Comparators an authored `accepted` list widens.
_ACCEPTS_SPELLINGS = frozenset({"canonical_string_equal", "canonical_string_in_set", "cited_clause_equal"})


def _accepted_for(field: str, config: dict) -> list[str]:
    """The other spellings the author allows for this field, if any.

    Same shape as `_tolerance_for`, including the prefix fallback, so a config
    that already grades `contact_x` under `x` can widen it the same way.
    """
    accepted = config.get("accepted")
    if not isinstance(accepted, dict):
        return []
    candidates = [field]
    for prefix in ("contact_", "clash_"):
        if field.startswith(prefix):
            candidates.append(field[len(prefix):])
    for candidate in candidates:
        value = accepted.get(candidate)
        if isinstance(value, (list, tuple)) and value:
            return [str(entry) for entry in value]
    return []


def _numeric_literal(value: Any) -> int | float | None:
    """The number a string is entirely made of, or None.

    Only a value that parses *in full* qualifies. A specification string such
    as ``"4500 x 2100 mm"`` and a citation such as ``"Article 4.2.10"`` both
    contain digits and must keep their existing string comparison.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if text.lstrip("+-").isdigit():
        return int(text)
    return number


def _measure(value: Any) -> float | None:
    """A number, or a string that is wholly one, as a float; else None."""
    if isinstance(value, str):
        literal = _numeric_literal(value)
        return None if literal is None else float(literal)
    return _number(value)


def _is_boolean_literal(value: Any) -> bool:
    """``"true"`` / ``"false"`` stored as text. ``"yes"``/``"no"`` stay words:
    they are answer vocabulary, graded as strings."""
    return isinstance(value, str) and value.strip().casefold() in {"true", "false"}


def _default_comparator(field: str, expected: Any, config: dict) -> tuple[str, Any]:
    name = field.casefold()
    tolerance = _tolerance_for(field, config)
    if name in CITED_CLAUSE_FIELDS and isinstance(expected, str):
        return "cited_clause_equal", None
    if isinstance(expected, (list, tuple)) and name in SET_EQUAL_FIELDS:
        # Consulted before the scalar name heuristics: some of these names
        # would otherwise match a scalar branch (``residue_numbers`` matches
        # "residue"), and a list of labels or objects sent to
        # vector_abs_tolerance fails _validate_tolerance and turns the whole
        # task into invalid_task_contract, whatever the agent answered.
        return "canonical_set_equal", None
    if (
        isinstance(expected, (list, tuple))
        and name in ORDERED_STRING_VECTOR_FIELDS
        and not _all_numeric_leaves(expected)
    ):
        # The string-range form of ``extent_vox``. The numeric triplet form is
        # a voxel measurement and keeps the tolerance machinery below.
        return "ordered_string_vector_equal", None
    if isinstance(expected, dict) and _canonical_atom_object(expected) is not None:
        # An atom record is an identity whatever the field is called (`ion`).
        return "canonical_atom_id_equal", None
    if isinstance(expected, dict) and "atom" not in name:
        # An object is graded leaf by leaf, as one field. Ahead of the name
        # heuristics: the identity canonicalisers parse strings, so an object
        # named ``*_structure_*`` sent to one failed whatever was answered. An
        # atom record keeps its own identity comparator below.
        return "object_leafwise", None
    # A number is never a name. ``structure_interference_count`` and
    # ``n_residues_within`` merely contain "structure" and "residue", and these
    # comparators do not decide equality: they canonicalise the value into a key
    # they look up, which no number can produce, so two identical integers
    # compared False and the task could not reach 1.0 against its own ground
    # truth. A numeric value still falls through to the measurement branches.
    named = not (isinstance(expected, (int, float)) and not isinstance(expected, bool))
    if named and ("spacegroup" in name or "space_group" in name):
        return "canonical_space_group_equal", None
    if named and "residue" in name:
        return "canonical_residue_equal", None
    if named and ("asset" in name or "structure" in name):
        return "canonical_asset_id_equal", None
    if "atom" in name and (name.endswith("_id") or isinstance(expected, dict)):
        return "canonical_atom_id_equal", None
    if "dihedral" in name or name.endswith("_angle_deg"):
        return "circular_tolerance", tolerance
    # A number written as a string, once every name heuristic has had its say:
    # ``ligand_atom_id`` keeps its canonicaliser, and this branch stays ahead of
    # ``tolerance is not None`` because ``abs_tolerance`` reads both sides
    # through ``_number``, which rejects strings. The band rides on the
    # comparator's own payload instead.
    numeric_literal = _numeric_literal(expected) if isinstance(expected, str) else None
    if numeric_literal is not None:
        return "numeric_literal_equal", tolerance
    if isinstance(expected, (list, tuple)):
        return "vector_abs_tolerance", tolerance
    if tolerance is not None:
        return "abs_tolerance", tolerance
    if (
        isinstance(expected, bool)
        or _is_boolean_literal(expected)
        or name.startswith("has_")
        or name.startswith("crosses_")
    ):
        return "boolean_equal", None
    if isinstance(expected, int) and not isinstance(expected, bool):
        return "integer_equal", None
    if isinstance(expected, str):
        return "canonical_string_equal", None
    return "exact", None


def _validate_tolerance(item: dict, comparator: str) -> bool:
    if comparator == "voxel_member_of_set":
        return _member_set_bits(item) is not None
    if comparator in {"abs_tolerance", "circular_tolerance"}:
        tolerance = _number(item.get("tolerance"))
        return tolerance is not None and tolerance >= 0
    if comparator == "vector_abs_tolerance":
        raw = item.get("component_tolerances", item.get("tolerance"))
        scalar = _number(raw)
        if scalar is not None:
            return scalar >= 0
        return bool(
            isinstance(raw, (list, tuple))
            and raw
            and all(_number(value) is not None and float(value) >= 0 for value in raw)
        )
    return True


EXPLANATION_FIELDS = frozenset({
    "evidence", "evidence_summary", "visual_basis", "spatial_evidence",
    "spatial_description", "basis", "justification", "visual_justification",
    "located", "verdict", "cited_clause",
})


# The threshold the corpus's own ``answer_shape_flags`` uses to call a value
# prose. Word count is the measure because it is what separates a sentence an
# agent writes from a value an agent picks, and CJK is counted separately
# because it is not space-delimited.
_PROSE_WORDS = 8
_PROSE_CJK = 16


def _is_prose(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return (
        len(re.findall(r"[A-Za-z][A-Za-z'-]*", value)) >= _PROSE_WORDS
        or len(re.findall(r"[一-鿿]", value)) >= _PROSE_CJK
    )


def _grades_as_citation(field: str, value: Any) -> bool:
    return field.casefold() in CITED_CLAUSE_FIELDS and _cited_clause(value) is not None


def select_report_only(ground_truth: dict, declared: list) -> set[str]:
    """Which fields leave the rubric, refusing to empty it.

    These fields are compared verbatim today, so they contribute nothing: no
    answer reproduces a paragraph. Removing them costs no signal and restores
    the task's ceiling. The refusal is the load-bearing part -- a task whose
    every field is an explanation would otherwise become passable by a single
    token.

    The name alone does not decide. ``verdict`` carries ``"Compliant"`` in one
    delivered task and twenty-four words of reasoning in another, so the value
    is what says whether the field is an explanation or a one-word enum the
    agent must actually choose; a name-only rule drops the enum and the task
    loses a real item.

    A ``cited_clause`` that cites a clause is not an explanation: its
    (standard, clause) pair is graded by ``cited_clause_equal``, so it stays
    unless the author declares it report-only. One that cites nothing is
    prose like any other and leaves.
    """
    candidates = {
        field for field, value in ground_truth.items()
        if (field.casefold() in EXPLANATION_FIELDS or field in declared)
        and _is_prose(value)
        and (field in declared or not _grades_as_citation(field, value))
    }
    if not candidates:
        return set()
    if len(candidates) == len(ground_truth):
        return set()
    return candidates


def report_only_drop(
    ground_truth: dict, declared: list, item_ids: set[str]
) -> set[str]:
    """The item ids a rubric loses: the rule, the intersection, the refusal.

    One definition, because the offline replay rebuilds this same decision from
    stored verdicts and a second copy drifts. It did: the refusal began as
    "would empty the rubric" and was widened to "would leave one item", which
    the replay's own copy did not carry.

    ``item_ids`` is treated as ground-truth keys, which holds on the implicit
    path where the items are derived from them and is an assumption on the
    explicit one, where an id may be a path (``targets.X.y``) that no key
    matches; there, nothing is dropped.
    """
    dropped = select_report_only(ground_truth, declared) & set(item_ids)
    # A task must never be left scored on one item, or on none. The spec's
    # refusal is ``<= 1`` survivors, not ``0``: 30 of the audits in the
    # delivery end with exactly one, and one item is passable on a single
    # token -- eight of those 30 are left holding an item whose ground truth is
    # the empty string, which any answer matches. This also covers an explicit
    # rubric that is a strict subset of the ground truth, where dropping its
    # every item would block the task with ``empty_rubric`` rather than score
    # it on less.
    if dropped and len(item_ids) - len(dropped) <= 1:
        return set()
    return dropped


def _compile_generic_rubric(
    config: dict, ground_truth: dict
) -> tuple[list[dict], list[str], list[str]]:
    """Compile the scale-v2.1 generic rubric into canonical scorer items."""
    errors: list[str] = []
    if config.get("schema_version") != GENERIC_RUBRIC_VERSION:
        errors.append("unsupported_or_missing_rubric_version")
    if config.get("strict_json_object") is not True:
        errors.append("strict_json_object_required")
    raw_fields = config.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        return [], [*errors, "empty_rubric"], []

    allowed_keys = {
        "field", "comparison", "tolerance", "unit", "periodic_modulus"
    }
    items: list[dict] = []
    seen: set[str] = set()
    for raw in raw_fields:
        if not isinstance(raw, dict):
            errors.append("invalid_rubric_item_type")
            continue
        field = str(raw.get("field", ""))
        comparison = str(raw.get("comparison", ""))
        if not field or field in seen or field not in ground_truth:
            errors.append(f"invalid_rubric_field:{field}")
        seen.add(field)
        if set(raw) - allowed_keys:
            errors.append(f"unknown_rubric_item_key:{field}")

        expected = ground_truth.get(field)
        comparator = "exact"
        item: dict[str, Any] = {
            "id": field,
            "pred_path": field,
            "gt_path": field,
            "weight": 1.0,
            "required": True,
        }
        if comparison == "exact":
            if field.casefold().endswith("atom_id"):
                comparator = "canonical_atom_identity_equal"
        elif comparison == "absolute_tolerance":
            tolerance = _number(raw.get("tolerance"))
            if tolerance is None or tolerance < 0:
                errors.append(f"missing_tolerance:{field}")
            else:
                item["tolerance"] = tolerance
            if raw.get("periodic_modulus") is not None:
                modulus = _number(raw.get("periodic_modulus"))
                if modulus is None or modulus <= 0:
                    errors.append(f"invalid_periodic_modulus:{field}")
                else:
                    item["periodic_modulus"] = modulus
                comparator = "circular_tolerance"
            elif isinstance(expected, (list, tuple)):
                comparator = "vector_abs_tolerance"
            else:
                comparator = "abs_tolerance"
        else:
            errors.append(f"unsupported_comparison:{field}")
        item["comparator"] = comparator
        items.append(item)
    # No third value: the generic contract carries no explanation fields to
    # report, and its arity still has to match ``_compile_rubric``'s, which
    # returns its value directly.
    return items, errors, []


def _compile_rubric(
    task: Any, ground_truth: dict
) -> tuple[list[dict], list[str], list[str]]:
    config = _scorer_config(task)
    errors: list[str] = []
    scorer = getattr(task, "scorer", None)
    primary = str(getattr(scorer, "primary", ""))
    if primary == GENERIC_SCORER_NAME:
        return _compile_generic_rubric(config, ground_truth)
    version = config.get("rubric_version", config.get("contract_version", config.get("version")))
    if primary in {SCORER_NAME, "deterministic_answer_v2_1"} and version not in _RUBRIC_VERSION_ALIASES:
        errors.append("unsupported_or_missing_rubric_version")

    aggregation = config.get("aggregation")
    if aggregation not in {
        None,
        "",
        "weighted",
        "weighted_mean",
        "all_required_with_partial_credit",
    }:
        errors.append("unsupported_aggregation")
    if "required_gate" in config and not isinstance(config["required_gate"], bool):
        errors.append("invalid_required_gate")

    explicit = config.get("rubric")
    if isinstance(explicit, list):
        items = [dict(item) for item in explicit if isinstance(item, dict)]
        if len(items) != len(explicit):
            errors.append("invalid_rubric_item_type")
        # Authored spellings are the author's, wherever the items came from;
        # an item that declares its own list keeps it.
        for item in items:
            comparator = str(item.get("comparator", ""))
            if "accepted" in item or comparator not in _ACCEPTS_SPELLINGS:
                continue
            authored = _accepted_for(str(item.get("gt_path") or item.get("id") or ""), config)
            if authored:
                item["accepted"] = authored
                if comparator == "canonical_string_equal":
                    item["comparator"] = "canonical_string_in_set"
    elif explicit is None:
        items = []
        for field, expected in ground_truth.items():
            comparator, tolerance = _default_comparator(field, expected, config)
            item = {
                "id": field,
                "pred_path": field,
                "gt_path": field,
                "comparator": comparator,
                "weight": 1.0,
                "required": True,
            }
            if tolerance is not None:
                if isinstance(tolerance, list):
                    item["component_tolerances"] = tolerance
                else:
                    item["tolerance"] = tolerance
            # The guard is load-bearing, and it is why this runs *after*
            # ``_default_comparator``: a numeric string routes to
            # ``numeric_literal_equal``, and re-deciding it as a string
            # comparison would fail every agent that answered the number. It
            # also keeps this pass off a field that has its own canonicaliser,
            # and off any non-string ground truth, which ``derive_aliases``
            # (a string parser) cannot take. Authored spellings come first and
            # keep `_accepted_for`'s prefix fallback; a cited clause carries
            # them for its string fallback.
            authored = _accepted_for(field, config)
            if comparator == "canonical_string_equal":
                aliases = list(authored)
                aliases += [a for a in derive_aliases(expected) if a not in aliases]
                if aliases:
                    item["accepted"] = aliases
                    item["comparator"] = "canonical_string_in_set"
            elif comparator == "cited_clause_equal" and authored:
                item["accepted"] = authored
            items.append(item)
    else:
        items = []
        errors.append("rubric_must_be_array")

    seen_ids: set[str] = set()
    schema_fields = set(_schema_fields(task))
    for item in items:
        item_id = str(item.get("id", ""))
        comparator = str(item.get("comparator", ""))
        if not item_id or comparator not in _COMPARATORS:
            errors.append(f"invalid_rubric_item:{item_id}")
        if item_id in seen_ids:
            errors.append(f"duplicate_rubric_item:{item_id}")
        seen_ids.add(item_id)
        pred_path = str(item.get("pred_path", ""))
        gt_path = str(item.get("gt_path", ""))
        if not pred_path or (schema_fields and pred_path.split(".", 1)[0] not in schema_fields):
            errors.append(f"invalid_pred_path:{item_id}")
        if not gt_path or not _path_get(ground_truth, gt_path)[0]:
            errors.append(f"invalid_gt_path:{item_id}")
        weight = _number(item.get("weight", 1.0))
        if weight is None or weight <= 0:
            errors.append(f"invalid_rubric_weight:{item_id}")
        if not isinstance(item.get("required", True), bool):
            errors.append(f"invalid_required_flag:{item_id}")
        if not _validate_tolerance(item, comparator):
            code = (
                "invalid_member_set"
                if comparator == "voxel_member_of_set"
                else "missing_tolerance"
            )
            errors.append(f"{code}:{item_id}")

    # The drop runs after validation, not before. A dropped item is still an
    # item the contract declared, so a bogus comparator or weight in it is a
    # defect to report loudly rather than to repair by removal, and this is
    # also what every task did before the drop existed.
    declared = config.get("report_only")
    declared = list(declared) if isinstance(declared, list) else []
    # Only an id the rubric actually carries can be removed: reporting the
    # ground-truth key instead would name a field that was never scored, and
    # the replay reads that key as the footprint of the change.
    item_ids = {str(item.get("id")) for item in items}
    dropped = report_only_drop(ground_truth, declared, item_ids)
    for item in items:
        if str(item.get("id")) in dropped:
            item["role"] = "report_only"
    items = [item for item in items if item.get("role") != "report_only"]

    if not items:
        errors.append("empty_rubric")
    # A dropped field is reported, not an error: it leaves the rubric on
    # purpose, and an error here would block the task instead of scoring it.
    return items, errors, sorted(dropped)


class DeterministicAnswerScorer:
    """Strict structured scorer whose formal reward key is ``answer_score``."""

    name = "answer_score"

    def score(self, task: Any, traj: Any) -> ScoreResult:
        ground_truth, contract_errors = _ground_truth_object(task)
        if ground_truth is None:
            return ScoreResult(
                value=0.0,
                metric=self.name,
                raw={
                    "eligible": False,
                    "reason": "invalid_task_contract",
                    "errors": contract_errors,
                },
                degraded=True,
            )
        rubric, rubric_errors, dropped = _compile_rubric(task, ground_truth)
        if rubric_errors:
            return ScoreResult(
                value=0.0,
                metric=self.name,
                raw={
                    "eligible": False,
                    "reason": "invalid_task_contract",
                    "errors": rubric_errors,
                },
                degraded=True,
            )

        try:
            answer = parse_strict_answer(getattr(traj, "final_answer", None))
            parse_error = None
        except AnswerParseError as exc:
            answer = None
            parse_error = exc.code

        fields = set(_schema_fields(task))
        if parse_error is None and not isinstance(answer, dict):
            parse_error = "answer_not_object"
        elif parse_error is None and fields and set(answer) != fields:
            parse_error = (
                "unexpected_answer_fields"
                if set(answer) - fields
                else "answer_fields_mismatch"
            )

        verdicts: list[dict[str, Any]] = []
        passed_weight = 0.0
        total_weight = sum(float(item.get("weight", 1.0)) for item in rubric)
        required_failure = False
        for item in rubric:
            pred_ok, predicted = _path_get(answer, str(item.get("pred_path", "")))
            gt_ok, expected = _path_get(ground_truth, str(item.get("gt_path", "")))
            comparator_name = str(item.get("comparator"))
            comparator = _COMPARATORS[comparator_name]
            passed = bool(
                parse_error is None
                and pred_ok
                and gt_ok
                and comparator(predicted, expected, item)
            )
            weight = float(item.get("weight", 1.0))
            if passed:
                passed_weight += weight
            required = bool(item.get("required", True))
            required_failure = required_failure or (required and not passed)
            reason = "pass" if passed else (
                parse_error or ("missing_pred_path" if not pred_ok else "comparison_failed")
            )
            verdicts.append(
                {
                    "id": str(item.get("id")),
                    "passed": passed,
                    "required": required,
                    "reason": reason,
                    "comparator": comparator_name,
                    "weight": weight,
                }
            )

        ungated_score = passed_weight / total_weight if total_weight else 0.0
        config = _scorer_config(task)
        required_gate = bool(config.get("required_gate", False))
        score = 0.0 if required_gate and required_failure else ungated_score
        return ScoreResult(
            value=score,
            metric=self.name,
            raw={
                "eligible": True,
                "rubric_version": config.get(
                    "rubric_version", config.get("contract_version", config.get("version"))
                ),
                "parse_error": parse_error,
                "required_gate": required_gate,
                "required_gate_passed": not required_failure,
                "ungated_score": ungated_score,
                "passed_items": sum(1 for item in verdicts if item["passed"]),
                "total_items": len(verdicts),
                "report_only": sorted(dropped),
                "verdicts": verdicts,
            },
            degraded=parse_error is not None,
        )
