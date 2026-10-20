"""F5: two spellings of one value must compare equal.

The rule is monotone by construction -- it coarsens the equivalence relation
and applies it to both sides -- so a pair that passes today can never start
failing. These tests pin the coarsening and, just as hard, pin its floor: the
cases that must stay unequal.
"""
import pytest

import sci3d_bench.scorers.deterministic_answer as scorer
from sci3d_bench.scorers.deterministic_answer import (
    _canonical_plain,
    _canonical_string,
    _compare_asset_id,
    _compare_atom_identity,
    _compare_ordered_string_vector,
    _compare_residue,
    _compare_set_equal,
    _compare_space_group,
    _compare_string,
    _compare_string_in_set,
)

CORPUS = [
    ("second_render", "second render"),
    ("the upper-abdominal level in the first render",
     "upper-abdominal level in the first render"),
]

FLOOR = [
    # an interior separator carries meaning
    ("2_6", "26"),
    ("2.6", "26"),
    ("X-axis", "X"),
    # a two-token string is an identifier pair, not prose with an article
    ("a b", "b"),
    # the opposite answer shares every token but one letter
    ("C-terminus", "N-terminus"),
    # a value that normalises to nothing is not the empty answer
    ("___", ""),
    # the ground-truth typo F5 deliberately does not paper over
    ("mC>", "mC"),
    # the two rules interact: swapping ``_`` for a space first would
    # manufacture a token boundary and then delete a letter that was never an
    # article, making these two different values compare equal.
    ("a_b_c", "b c"),
    ("the_a_b", "a b"),
]


@pytest.mark.parametrize("expected, predicted", CORPUS)
def test_corpus_pairs_compare_equal(predicted, expected):
    assert _compare_string(predicted, expected, {}) is True


@pytest.mark.parametrize("expected, predicted", FLOOR)
def test_floor_pairs_stay_unequal(predicted, expected):
    assert _compare_string(predicted, expected, {}) is False


def test_single_letter_survives_the_article_rule():
    assert _canonical_string("A") == "a"
    assert _canonical_string("A") != _canonical_string("")


def test_empty_string_is_stable():
    assert _canonical_string("") == ""
    assert _canonical_string("   ") == ""


def test_non_string_is_still_none():
    assert _canonical_string(20) is None
    assert _canonical_string(None) is None
    assert _canonical_string(["a"]) is None


def test_underscore_does_not_delete_characters():
    # A separator swap, not a character strip: 2_6 is not 26.
    assert _canonical_string("2_6") == "2 6"


def test_leading_article_rule_needs_three_tokens():
    assert _canonical_string("the ramp") == "the ramp"
    assert _canonical_string("the ramp run") == "ramp run"


def test_alias_route_sees_the_same_normalisation():
    # The ground truth is a *different* string; only the accepted alias matches,
    # and it matches only because the alias route normalises too.
    item = {"accepted": ["second render"]}
    assert _compare_string_in_set("second_render", "the other render", item) is True


def test_the_article_rule_judges_the_original_token_count():
    """The two rules are ordered, and the order is load-bearing.

    The article test runs on the string as written, before ``_`` becomes a
    space. Swapping the order manufactures a token boundary and then deletes a
    letter that was never an article -- ``"a_b_c"`` is one identifier token,
    not a sentence beginning "a ...".
    """
    assert _canonical_string("a_b_c") == "a b c"
    assert _canonical_string("the_a_b") == "the a b"
    assert _canonical_string("the_a_b_c") == "the a b c"


def test_the_normalisation_leaves_these_alone():
    """What the rule must NOT do, stated as literal outputs.

    Comparing two inputs can pass with an extra rule in place whenever both
    inputs take it. A rule the spec rejects -- ``-`` -> space is the named one
    -- has to move a literal here, or it is not pinned at all.
    """
    assert _canonical_string("x-axis") == "x-axis"
    assert _canonical_string("2.6") == "2.6"
    assert _canonical_string("mC>") == "mc>"
    assert _canonical_string("C-terminus") == "c-terminus"
    # ...while the two rules that ARE intended still fire.
    assert _canonical_string("A") == "a"
    assert _canonical_string("2_6") == "2 6"
    assert _canonical_string("second_render") == "second render"
    assert _canonical_string("the ramp run") == "ramp run"


def _pre_f5_canonical_string(value):
    """The verbatim pre-F5 body, to drive a comparator under the old form."""
    if not isinstance(value, str):
        return None
    return _canonical_plain(value)


#: Pairs the pre-F5 form ALREADY called equal, per comparator. Monotonicity is
#: a claim about pairs of this shape and only this shape: the change may turn a
#: False into a True and must never turn a True into a False. A list in which
#: every pair was unequal before the change asserts nothing -- the first cut of
#: this test swept CORPUS + FLOOR, in which all nine pairs were old-unequal, so
#: ``assert not old or new`` short-circuited on every iteration. It could not
#: have caught the four narrowings the review found, because a narrowing
#: requires a pair that was old-equal. The residue, space-group and asset-id
#: pairs below are exactly those four; the space-group one is doubled because
#: ``"Aba2"`` is space group No. 41 and gemmi resolves it, which would leave the
#: delimiter-insensitive fallback never reached.
MONOTONE = {
    "_compare_string": [
        # old-equal, not new-equal: the old form collapsed case and runs of
        # whitespace too, and it must keep doing so.
        ("  Second   Render ", "second render"),
    ],
    "_compare_residue": [
        ("Aba2", "A b a 2"),
        ("ab c", "a b c"),
    ],
    "_compare_space_group": [
        ("Aba2", "A b a 2"),
        ("a b c", "abc"),
    ],
    "_compare_asset_id": [
        ("dir/an a b.stp", "an a b.stp"),
    ],
}

COMPARATORS = {
    "_compare_string": _compare_string,
    "_compare_residue": _compare_residue,
    "_compare_space_group": _compare_space_group,
    "_compare_asset_id": _compare_asset_id,
}


@pytest.mark.parametrize("name", sorted(MONOTONE))
def test_monotone_over_the_corpus(monkeypatch, name):
    """Nothing the old form passed may start failing.

    "Old" is not re-derived here: it is produced by driving the *real*
    comparator with `_canonical_string` swapped for its pre-F5 body, so the
    comparison holds for whichever helper the comparator happens to call and
    does not depend on the change being written the way it is written today.
    """
    comparator = COMPARATORS[name]
    pairs = MONOTONE[name]
    old_equal = 0
    for left, right in pairs:
        monkeypatch.setattr(scorer, "_canonical_string", _pre_f5_canonical_string)
        try:
            was = comparator(left, right, {})
        finally:
            monkeypatch.undo()
        now = comparator(left, right, {})
        if was:
            old_equal += 1
            assert now is True, (name, left, right)
    # The premise, asserted rather than assumed: a pair list with no old-equal
    # pair has nothing to say about monotonicity.
    assert old_equal == len(pairs) > 0, (name, old_equal, len(pairs))


# The atom-identity route normalises the leaves of its own dict with a second
# function, so that second function has to answer "same spelling?" the same way
# the string route does. These pin the agreement through the comparator, not
# through the helper: a helper that is right but unwired would still fail here.
def test_atom_identity_dict_label_accepts_the_other_separator():
    oxygen = {"element": "O", "label": "CA_1"}
    assert _compare_atom_identity(oxygen, {"element": "O", "label": "CA 1"}, {}) is True


def test_atom_identity_delegation_is_whole_not_half():
    # The same rule, not merely the same underscore step: a dict leaf takes the
    # leading-article reduction too, because it is normalised by the one string
    # normaliser rather than by a second one that agrees only by accident.
    assert (
        _compare_atom_identity(
            {"element": "O", "label": "the terminal oxygen atom"},
            {"element": "O", "label": "terminal oxygen atom"},
            {},
        )
        is True
    )


def test_atom_identity_dict_label_keeps_the_floor():
    # The separator is swapped, never deleted: ``CA_1`` is not ``CA1``.
    assert (
        _compare_atom_identity(
            {"element": "O", "label": "CA_1"}, {"element": "O", "label": "CA1"}, {}
        )
        is False
    )
    # Punctuation is still not stripped anywhere.
    assert (
        _compare_atom_identity(
            {"element": "O", "label": "mC>"}, {"element": "O", "label": "mC"}, {}
        )
        is False
    )


# ── where the rule stops on purpose: a dict key is not a value ───────────── #

# A key is a schema identifier, not prose a model wrote: it names a slot in the
# contract's own shape (``residue_name``, ``label``), and the two routes that
# walk a dict -- the list comparators through ``_canonical_leaf``, the
# atom-identity route through ``_canonical_atom_object`` -- fold its case and
# strip its surrounding whitespace, and stop there. The article rule and the
# underscore swap do **not** reach it: applying them would let the key a field
# is declared under stop being the identifier it is answered under, and it is
# the same category error as feeding prose rules into a structure parser.
# These two tests exist so that a later reader who "finishes the job" by
# pointing the keys at the value normaliser finds out here rather than in a
# delivered score. There are four stored pairs in v11-250 that would move,
# which is why the exclusion is worth pinning rather than leaving implicit.

def test_the_value_rule_stops_at_a_dict_key():
    # The value rule joins ``cross_sectional_area_mm2`` with ``cross sectional
    # area mm2``; as *keys* they stay two identifiers.
    left = [{"cross_sectional_area_mm2": 4.0}]
    right = [{"cross sectional area mm2": 4.0}]
    assert _compare_ordered_string_vector(left, right, {}) is False
    assert _compare_set_equal(left, right, {}) is False
    # The other half of the scope, so the boundary is exact: a key IS folded to
    # one spelling for case and surrounding whitespace, and that is all.
    assert _compare_ordered_string_vector(
        left, [{"  Cross_Sectional_Area_MM2  ": 4.0}], {}
    ) is True


def test_the_value_rule_stops_at_an_atom_identity_key():
    left = {"chain": "A", "residue_name": "ALA", "residue_number": 17,
            "atom_name": "CA"}
    # The same atom with the key spelled the other way is a *different* dict:
    # it stops carrying the scheme this route reads, so it does not canonicalise
    # at all and the two do not compare equal.
    assert _compare_atom_identity(
        left, {"chain": "A", "residue name": "ALA", "residue_number": 17,
               "atom_name": "CA"}, {}
    ) is False
    # The other half of the scope, and the half that pins the exclusion: spelled
    # the same apart from case, the two dicts are one atom, because the key is
    # read as an identifier. Pointing this route's keys at the value normaliser
    # is the tempting "fix", and it breaks this line -- not by making the two
    # equal, but by turning every scheme key into ``residue name`` and so
    # failing the scheme lookup for both sides at once.
    assert _compare_atom_identity(
        left, {"CHAIN": "A", "Residue_Name": "ALA", "residue_number": 17,
               "atom_name": "CA"}, {}
    ) is True
    # An atom dict's *values* still take the whole value rule, keys and values
    # being different things.
    assert _compare_atom_identity(
        {"element": "O", "label": "CA_1"}, {"element": "O", "label": "CA 1"}, {}
    ) is True


#: Adversarial strings, grouped below by their **pre-F5** form. Hand-written and
#: fixed rather than generated: a generator would obscure which shape broke, and
#: this repo's tests are deterministic on purpose. Each shape carries a case or
#: whitespace twin so that the groups are real groups and not singletons.
SPLIT_PROBES = [
    # an identifier whose first token looks like an article
    "a_b_c", "A_B_C", " A_B_C ",
    "b c", "  B   C  ",
    # the pair F5 deliberately stops short of joining, and the one it joins
    "the_second_render", "THE_SECOND_RENDER",
    "the second render", "THE  SECOND   RENDER",
    "second_render", "SECOND_RENDER",
    "second render", "  Second   Render ",
    # an asset id whose leading "A" is a token once the separator is swapped
    "A_ANZJ_A_03.glb", "a_anzj_a_03.glb", "ANZJ_A_03.glb",
    # a value that normalises to nothing must not become the empty answer
    "___", " ___ ",
    # punctuation, numerals and non-Latin text
    "mC>", " MC> ",
    "2.6", "2_6", "2_6 ",
    "肝实质", "肝 实质", "  肝   实质  ",
    # the empty answer itself
    "", "   ",
]


def test_no_pre_f5_equivalence_class_is_split_by_f5():
    """The property behind Task 2's corpus count, not the count itself.

    ``_canonical_string`` reads its argument only through ``_canonical_plain``:
    the article test, the underscore swap and the empty fallback are each a
    pure function of that value, so ``old(a) == old(b)`` implies
    ``new(a) == new(b)`` for *every* string. Task 2's replay found 0 narrowed
    pairs in 205 real ones; this is the statement that covers the strings the
    corpus does not hold. It is checked by grouping rather than pairwise, so a
    shape that breaks shows up as the class that split.
    """
    classes: dict[str, list[str]] = {}
    for value in SPLIT_PROBES:
        classes.setdefault(_canonical_plain(value), []).append(value)
    split = {old: sorted({_canonical_string(v) for v in group})
             for old, group in classes.items()
             if len({_canonical_string(v) for v in group}) > 1}
    assert split == {}, "an old form's equivalence class must never split"
    # The premise, asserted rather than assumed: a probe list in which every
    # group held one string would satisfy the assertion above and say nothing.
    assert sum(len(group) > 1 for group in classes.values()) >= 8, sorted(
        (old, sorted(group)) for old, group in classes.items()
    )


def test_the_class_that_f5_deliberately_does_not_join():
    """What the property above does not cover: F5 also refuses to *merge*.

    ``the_second_render`` and ``the second render`` are different pre-F5
    classes -- one token, and three -- so no class is split by their staying
    apart. The ruling chose this side of the trade on purpose (a missed
    widening marks a correct answer wrong and is visible; a false positive
    marks a wrong answer right and is not), and this is the pair it gave up.
    """
    assert _canonical_string("the_second_render") == "the second render"
    assert _canonical_string("the second render") == "second render"
    assert _compare_string("the_second_render", "the second render", {}) is False
