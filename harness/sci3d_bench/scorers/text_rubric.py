"""Deterministic scoring for open-ended text answers, with no model in the loop.

Structured answers do not need this module: their ground truth is a JSON object
and ``deterministic_answer`` compares it field by field. This is for the residue
-- an expert answer that is a sentence, such as *"The overall deformation pattern
is symmetric, with the largest deformation at the mid-span of the bridge; no
twisting occurs."* -- where there is no field to compare and the answer is
free text on both sides.

**Why not similarity to the reference sentence.** Token overlap against one
reference rewards wording, not correctness. The sentence above contains *"no
twisting occurs"*; an answer saying *"twisting occurs"* overlaps with it almost
completely and is exactly wrong. Length normalisation does not fix this, and no
threshold on a lexical metric separates paraphrase from contradiction.

So the unit is a **keyword group**: one idea, satisfied by any of several
spellings, optionally negated. A group marked ``negated`` is satisfied only when
its phrase appears *and* a negator governs it, which is what tells the two
sentences above apart. Scoring is the fraction of required groups matched, with
any forbidden group zeroing the answer outright.

**Two sources, and the score says which.** ``TextRubric.source`` is ``authored``
when a person wrote the groups and ``derived`` when they were extracted from the
ground-truth sentence. A derived rubric cannot see paraphrase -- it can only ask
whether the reference's own words came back -- so a derived score is returned
with ``degraded=True`` and must not be mixed into a headline with authored ones.
The derivation exists to keep unlabelled questions scoreable during the
transition, not to replace the labels.

Nothing here calls a model, and nothing here is probabilistic: the same answer
and the same rubric produce the same score on every machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from sci3d_bench.scorers.base import ScoreResult

METRIC = "text_rubric"

# A negator governs the rest of its clause, not a fixed number of tokens. The
# window version got "no twisting occurs" wrong: it negated ``twisting`` and left
# ``occurs`` looking affirmative, so an answer saying "twisting occurs" scored the
# same as one saying "no twisting". Clause scope is both simpler and correct.
#
# Punctuation ends a clause and is not a token, so it is recorded as a boundary
# *index* rather than by splitting the text -- splitting drops the conjunctions
# and every later position shifts, which silently mislabels the negated words.
_PUNCTUATION_BREAK = re.compile(r"[.;:,]")
_CONJUNCTIONS = frozenset({"and", "but", "while", "whereas", "yet", "or"})

_NEGATORS = frozenset(
    {
        "no",
        "not",
        "never",
        "without",
        "none",
        "neither",
        "nor",
        "cannot",
        "absent",
        "lacking",
    }
)

# Function words only. A content word dropped here is a required idea that can
# never be earned, so the list stays deliberately small.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "than", "so", "such",
        "of", "in", "on", "at", "to", "for", "with", "from", "by", "as", "into",
        "over", "under", "between", "during", "about", "also", "both", "each",
        "all", "any", "is", "are", "was", "were", "be", "been", "being", "has",
        "have", "had", "do", "does", "did", "can", "could", "may", "might",
        "must", "shall", "should", "will", "would", "it", "its", "this", "that",
        "these", "those", "there", "their", "they", "he", "she", "his", "her",
        "which", "who", "more", "most", "very", "same",
    }
)


@dataclass(frozen=True)
class KeywordGroup:
    """One idea. Satisfied by any alternative, when not negated."""

    alternatives: tuple[str, ...]
    negated: bool = False

    def __post_init__(self) -> None:
        if not self.alternatives:
            raise ValueError("a keyword group needs at least one alternative")
        if any(not str(a).strip() for a in self.alternatives):
            raise ValueError("a keyword group alternative cannot be blank")


@dataclass(frozen=True)
class TextRubric:
    """What an open-ended answer has to say, and what it must not say."""

    required: tuple[KeywordGroup, ...]
    forbidden: tuple[KeywordGroup, ...] = ()
    source: str = "authored"
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in {"authored", "derived"}:
            raise ValueError(f"unknown rubric source: {self.source!r}")

    @property
    def is_authored(self) -> bool:
        return self.source == "authored"


def _clean(text: Any) -> str:
    cleaned = str(text or "").casefold().replace("’", "'")
    cleaned = re.sub(r"n't\b", " not", cleaned)
    return re.sub(r"[-_/]", " ", cleaned)


def tokenize(text: Any) -> tuple[str, ...]:
    """Lowercase, split contractions, drop punctuation, collapse whitespace."""
    return tuple(re.sub(r"[^\w\s]", " ", _clean(text)).split())


def clause_starts(text: Any) -> frozenset[int]:
    """Indices in the token stream where a new clause begins.

    The first token of each clause, plus 0 when the text does not open on a
    boundary. Indices are into :func:`tokenize`'s output for the same text, which
    is what keeps the two views from drifting apart.
    """
    cleaned = _clean(text)
    starts: set[int] = set()
    token_count = 0
    for match in re.finditer(r"\w+|[.;:,]", cleaned):
        piece = match.group(0)
        if _PUNCTUATION_BREAK.fullmatch(piece):
            starts.add(token_count)
        else:
            token_count += 1
    starts.discard(0)
    return frozenset(starts)


def negated_positions(text: Any, tokens: Sequence[str] | None = None) -> set[int]:
    """Indices in the token stream that a negator governs.

    A negator reaches the end of its clause. Punctuation and the coordinating
    conjunctions both close a clause, so "torsion but no twisting" marks
    ``twisting`` and "torsion, no twisting" marks it too.
    """
    flat = tuple(tokens) if tokens is not None else tokenize(text)
    starts = clause_starts(text)
    negated: set[int] = set()
    seen_negator = False
    for index, token in enumerate(flat):
        if index in starts:
            seen_negator = False
        if token in _CONJUNCTIONS:
            seen_negator = False
            continue
        if token in _NEGATORS:
            seen_negator = True
            continue
        if seen_negator:
            negated.add(index)
    return negated


def _phrase_variants(token: str) -> tuple[str, ...]:
    """A content token plus a crude singular, for derived groups only.

    Not a stemmer: ``supports`` should also match ``support``, and that is the
    whole ambition. ``ss`` endings are left alone so ``stress`` does not become
    ``stres``.
    """
    variants = [token]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        variants.append(token[:-1])
    return tuple(variants)


def _contains(tokens: Sequence[str], phrase: Sequence[str]) -> int | None:
    """Index of the first occurrence of ``phrase`` in ``tokens``, else None."""
    width = len(phrase)
    if width == 0 or width > len(tokens):
        return None
    for start in range(len(tokens) - width + 1):
        if tuple(tokens[start : start + width]) == tuple(phrase):
            return start
    return None


def group_is_satisfied(
    tokens: Sequence[str], group: KeywordGroup, negated: set[int]
) -> bool:
    """Whether the answer carries this idea, with the polarity the group asks for."""
    for alternative in group.alternatives:
        phrase = tokenize(alternative)
        start = _contains(tokens, phrase)
        if start is None:
            continue
        if not group.negated:
            return True
        if start in negated:
            return True
    return False


def score_text(answer: Any, rubric: TextRubric) -> ScoreResult:
    """Fraction of required groups matched; zero if any forbidden group appears.

    ``degraded`` mirrors the rubric's provenance rather than the answer's
    quality: a derived score means "we could only check the reference's own
    words", which is a weaker claim than an authored one and has to stay
    distinguishable downstream.
    """
    tokens = tokenize(answer)
    negated = negated_positions(answer, tokens)
    degraded = not rubric.is_authored
    if not rubric.required:
        return ScoreResult(
            value=0.0,
            metric=METRIC,
            raw={"reason": "rubric has no required groups", "source": rubric.source},
            degraded=True,
        )

    hit_forbidden = [
        group.alternatives[0]
        for group in rubric.forbidden
        if group_is_satisfied(tokens, group, negated)
    ]
    matched = [g for g in rubric.required if group_is_satisfied(tokens, g, negated)]
    missed = [g.alternatives[0] for g in rubric.required if g not in matched]
    if hit_forbidden:
        return ScoreResult(
            value=0.0,
            metric=METRIC,
            raw={
                "reason": "a forbidden phrase is present",
                "forbidden_hit": hit_forbidden,
                "matched": len(matched),
                "required": len(rubric.required),
                "source": rubric.source,
            },
            degraded=degraded,
        )
    return ScoreResult(
        value=len(matched) / len(rubric.required),
        metric=METRIC,
        raw={
            "matched": [g.alternatives[0] for g in matched],
            "missed": missed,
            "required": len(rubric.required),
            "source": rubric.source,
        },
        degraded=degraded,
    )


def derive_rubric(ground_truth: Any) -> TextRubric:
    """Build a fallback rubric from the reference sentence alone.

    Every content word of the reference becomes a required group, and a word the
    reference negates becomes a negated group -- which is the one thing this
    derivation gets right that a similarity metric does not. It cannot recognise
    a correct paraphrase that avoids the reference's vocabulary, so the result is
    marked ``derived`` and scores against it come back ``degraded``.
    """
    if not isinstance(ground_truth, str):
        raise ValueError(
            "derive_rubric needs a prose ground truth, got "
            f"{type(ground_truth).__name__}. A structured answer has fields to "
            "compare -- send it to the field comparator instead. Tokenising its "
            "repr would produce a rubric built from field names, and a score "
            "from that looks plausible while meaning nothing."
        )
    tokens = tokenize(ground_truth)
    negated = negated_positions(ground_truth, tokens)
    groups: list[KeywordGroup] = []
    seen: set[tuple[str, bool]] = set()
    for index, token in enumerate(tokens):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        is_negated = index in negated
        key = (token, is_negated)
        if key in seen:
            continue
        seen.add(key)
        groups.append(KeywordGroup(_phrase_variants(token), negated=is_negated))
    return TextRubric(
        required=tuple(groups),
        forbidden=(),
        source="derived",
        notes="extracted from the ground-truth sentence; cannot see paraphrase",
    )


def rubric_for(ground_truth: Any, authored: TextRubric | None = None) -> TextRubric:
    """The authored rubric when there is one, else the derived fallback."""
    return authored if authored is not None else derive_rubric(ground_truth)


def rubric_from_json(payload: dict[str, Any]) -> TextRubric:
    """Load an authored rubric.

    Each entry is either a string or a list of alternatives; a negated group is
    written ``{"any": [...], "negated": true}``.
    """

    def group(raw: Any) -> KeywordGroup:
        if isinstance(raw, str):
            return KeywordGroup((raw,))
        if isinstance(raw, (list, tuple)):
            return KeywordGroup(tuple(str(x) for x in raw))
        if isinstance(raw, dict):
            alternatives = raw.get("any") or raw.get("alternatives") or ()
            if isinstance(alternatives, str):
                alternatives = (alternatives,)
            return KeywordGroup(
                tuple(str(x) for x in alternatives), bool(raw.get("negated"))
            )
        raise ValueError(f"cannot read a keyword group from {raw!r}")

    return TextRubric(
        required=tuple(group(g) for g in payload.get("required") or ()),
        forbidden=tuple(group(g) for g in payload.get("forbidden") or ()),
        source=str(payload.get("source") or "authored"),
        notes=str(payload.get("notes") or ""),
    )


def rubric_to_json(rubric: TextRubric) -> dict[str, Any]:
    def group(g: KeywordGroup) -> dict[str, Any]:
        return {"any": list(g.alternatives), "negated": g.negated}

    return {
        "required": [group(g) for g in rubric.required],
        "forbidden": [group(g) for g in rubric.forbidden],
        "source": rubric.source,
        "notes": rubric.notes,
    }


def score_with_fallback(
    answer: Any, ground_truth: Any, authored: TextRubric | None = None
) -> ScoreResult:
    """Score an open-ended answer, using the authored rubric when one exists."""
    return score_text(answer, rubric_for(ground_truth, authored))
