"""Per-field grading tolerances, derived from a task's own ground truth.

A task tree whose ``scorer`` carries no ``tolerance`` is not scored leniently —
it is scored *wrongly*, in two different ways:

- a float field falls through ``_default_comparator`` to ``exact``, which is
  ``type(a) is type(b) and a == b``: every last-digit difference scores 0;
- a list field enters ``vector_abs_tolerance`` with no tolerance attached, which
  ``_validate_tolerance`` rejects as ``missing_tolerance:<field>`` and turns the
  whole task into ``invalid_task_contract`` / ``eligible: false`` — reward 0.0
  regardless of what the agent answered.

So this module exists to put a number where there was none. The number follows
the closed-loop band agreed with the expert side on 2026-09-23: **use exact
match and let the third decimal place differ** — implemented as ``±0.005`` for
every float-valued measurement, whatever precision it is written with. That
wording supersedes the earlier "one place fewer than written" decimal rule
(``10 ** (1 - d)``), which graded ``2.755`` to ``±0.01`` and ``79.5`` to
``±1.0``.

Three properties of the scorer make the worked examples below non-obvious, and
each is a silent failure rather than an error:

1. **Never emit a tolerance for a count, a flag or a label.** Branch order in
   ``_default_comparator`` tests ``tolerance is not None`` *before* it tests
   ``isinstance(expected, int)``, so declaring one for a count silently converts
   ``integer_equal`` into a float ``abs_tolerance``. "Tolerance zero" is spelled
   by *omitting the key*, never by writing ``0``. The few integer fields where
   that swap *is* wanted are named one by one in ``_INT_TOLERANCE``.
2. **A list field needs one or it fails closed.** ``point_vox`` without a
   tolerance is not "graded strictly", it is ``invalid_task_contract``.
3. **A field that cannot be compared numerically must be left alone.** A list of
   strings (``screenshots``) would be *made eligible* by attaching a tolerance
   and then fail every comparison, converting a loud contract error into a quiet
   zero. Only all-numeric lists get a tolerance here.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

# The scorer's own definition of "this string is entirely a number", imported
# rather than re-derived: two parsers would drift, and the band a field gets
# must agree with the comparator that grades it.
from sci3d_bench.scorers.deterministic_answer import _numeric_literal

#: The closed-loop measurement band (expert feedback, 2026-09-23): exact match,
#: the third decimal place may differ, implemented as +/- 0.005. It applies to
#: every float leaf, scalar or inside a measurement vector, whatever precision
#: the value is written with.
MEASUREMENT_TOLERANCE = 0.005

#: A whole number (an int leaf) keeps the one-unit band the decimal rule used
#: to give it: `2530 mm` still grades with +/- 1, not +/- 0.005.
_WHOLE_NUMBER_TOLERANCE = 1.0

# Voxel-index fields are discrete counts of whole voxels, so their tolerance is
# a whole number of voxels per axis and cannot be expressed by the closed-loop
# band above. `extent_vox` is a difference of two bbox corners, each of which
# carries its own one-voxel reading ambiguity, hence 2 rather than 1.
_VOXEL_TOLERANCE: Mapping[str, float] = {
    "extent_vox": 2.0,
    "longest_axes": 1.0,
}
_DEFAULT_VOXEL_TOLERANCE = 1.0

# `_vox` anywhere in the name is the convention the medical CT geometry family
# uses for a voxel coordinate: `point_vox`, `bbox_min_vox`, and
# `bbox_max_vox_inclusive` — which does *not* end in `_vox`, so this is a
# containment test rather than a suffix test.
_VOXEL_MARKER = "_vox"

# Integer fields that take a whole-number tolerance on purpose. Declaring one
# swaps `integer_equal` for a float `abs_tolerance` (property 1), which is what
# is wanted for a slice index — the adjacent slice is a defensible answer — and
# is never what is wanted for a count. Kept as an explicit allow-list so no
# count is loosened by accident.
_INT_TOLERANCE: Mapping[str, float] = {
    "representative_axial_index": 1.0,
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _all_numeric(items: Iterable[Any]) -> bool:
    """Whether every leaf is a number, at any nesting depth.

    A list of strings returns False and so gets no tolerance: see property 3 in
    the module docstring.
    """
    seen = False
    for item in items:
        seen = True
        if isinstance(item, (list, tuple)):
            if not _all_numeric(item):
                return False
        elif not _is_number(item):
            return False
    return seen


def _voxel_tolerance(field: str, value: Any) -> float | None:
    # ``_all_numeric`` iterates its argument, so a scalar must not reach it: a
    # ``*_vox*`` field holding a float, or holding the string form of one,
    # arrives here through the numeric-string branch above. ``None`` is the
    # fail-closed answer for a value this helper cannot read.
    if not isinstance(value, (list, tuple)):
        return None
    if not _all_numeric(value):
        return None
    return _VOXEL_TOLERANCE.get(field, _DEFAULT_VOXEL_TOLERANCE)


def _is_identity_name(name: str) -> bool:
    """Whether the field names a thing rather than measures one.

    `sp2_atom_ids` holds atom indices: `[6, 7]` and `[7, 6]` are one answer
    written twice, and `[6, 7]` against `[7, 7]` is a different molecule. A
    measurement list like `void_ladder_bottom_up_mm` is the opposite — `[2530,
    2481]` is a pair of readings, and the pair is graded by the decimal rule.
    """
    return name == "ids" or name.endswith("_ids") or "_id_" in name or name.endswith("_id")


def _component_band(value: Any) -> float | list[float] | None:
    """The band for one leaf or nested list of a measurement vector."""
    if isinstance(value, (list, tuple)):
        bands = [_component_band(item) for item in value]
        if any(band is None for band in bands):
            return None
        return bands
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _WHOLE_NUMBER_TOLERANCE
    if isinstance(value, float):
        return MEASUREMENT_TOLERANCE
    return None


def tolerance_for(field: str, value: Any) -> float | list[float] | None:
    """The tolerance this one ground-truth field should be graded with.

    Returns None when the field must be left untouched — a count, a flag, a
    label, or anything not comparable as a number.

    A measurement vector returns one band per component rather than a scalar,
    so `void_ladder_bottom_up_mm` grades `[2530, 2481]` with `[1.0, 1.0]`; a
    scalar would broadcast the same number to every axis, which is only right
    where the axes share a unit and a reading ambiguity.
    """
    name = str(field).casefold()

    if isinstance(value, bool):
        return None
    # A number written as a string is still a number, and the scorer grades it
    # as one, so it must be banded as one: the *storage form* of a ground truth
    # must not decide the grading rule. Without this, ``"20"`` reached
    # ``numeric_literal_equal`` with no band at all while ``20.0`` got the
    # closed-loop band. Anything the scorer's own parser rejects -- prose, a
    # specification, an empty string -- keeps returning None below.
    if isinstance(value, str):
        literal = _numeric_literal(value)
        if literal is None:
            return None
        value = literal
    if isinstance(value, int):
        # Property 1: a tolerance here would rewrite the comparator, which is
        # only ever intended for the fields named in _INT_TOLERANCE.
        return _INT_TOLERANCE.get(name)

    if _VOXEL_MARKER in name or name in _VOXEL_TOLERANCE:
        return _voxel_tolerance(name, value)

    if isinstance(value, float):
        return MEASUREMENT_TOLERANCE

    if isinstance(value, (list, tuple)):
        if not _all_numeric(value):
            return None
        if _is_identity_name(name):
            # Compared positionally by `vector_abs_tolerance`, so this is graded
            # on the order the ground truth happens to be written in — a known
            # narrowing, not a set comparison.
            return 0.0
        return _component_band(value)

    return None


def derived_tolerances(ground_truth: Mapping[str, Any] | None) -> dict[str, float]:
    """The tolerance map to fold into a task's ``scorer.tolerance``.

    Keyed by the ground-truth field name exactly as written, because
    ``_tolerance_for`` looks a field up by that string and silently returns None
    for any key that does not match character for character.
    """
    if not isinstance(ground_truth, Mapping):
        return {}
    out: dict[str, float] = {}
    for field, value in ground_truth.items():
        tolerance = tolerance_for(str(field), value)
        if tolerance is not None:
            out[str(field)] = tolerance
    return out


def merge_tolerances(
    declared: Mapping[str, Any] | None,
    ground_truth: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Declared tolerances first, derived ones filling what is left.

    A declared key is honoured only when it names a field the ground truth
    actually has; a key naming no field is inert in the scorer anyway, and
    keeping it would push the real field back onto the derived band.
    """
    derived = derived_tolerances(ground_truth)
    if not isinstance(declared, Mapping):
        return derived
    fields = set(ground_truth) if isinstance(ground_truth, Mapping) else set()
    merged = dict(derived)
    for key, value in declared.items():
        if str(key) in fields and _is_number(value):
            merged[str(key)] = float(value)
    return merged
