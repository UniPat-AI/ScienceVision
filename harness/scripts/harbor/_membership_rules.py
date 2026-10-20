"""Pack a declared mask region into a set-membership rubric item.

The ``med_lesion_point``-style family grades one voxel index against "the voxel
lies inside the finding's segmentation mask". The scorer cannot open a NIfTI --
the formal scorer image ships the standard library only, and comparators are
pure functions over the rubric item -- so the region travels inside the item as
bit-packed occupancy:

- ``member_shape``: the mask array's three dimensions, numpy axis order.
- ``member_set``: ``base64(zlib(bit-packed occupancy))``, C-order flat, least
  significant bit first within each byte -- byte-identical to
  ``numpy.packbits(flat, bitorder="little")``, which is what the scorer-side
  ``voxel_member_of_set`` comparator decodes.

A record declares the contract with ``ground_truth.method ==
"radiologist_mask_membership"``, or by carrying a ``mask_path`` + ``mask_id``
pair in its ground-truth value. Records that declare nothing pass through
untouched. The fold runs BEFORE ``normalize_eval_contract`` (see
``qa_gen_to_tasks``): the declaration is only intact while the record still
carries its own schema, and the injected v2.1 rubric is what makes the record
a rich contract. Broken declarations fail loudly at build time: an unreadable
mask, an empty label, an oversized blob, or a representative point that misses
its own mask.
"""
from __future__ import annotations

import base64
import zlib
from pathlib import Path
from typing import Any, Mapping

MEMBERSHIP_METHOD = "radiologist_mask_membership"
COMPARATOR = "voxel_member_of_set"

#: Compressed-blob ceiling. A region whose packed occupancy exceeds this fails
#: loudly at build time rather than quietly bloating every tree it rides in.
MEMBER_SET_MAX_BYTES = 256 * 1024


def declares_membership(ground_truth: Mapping[str, Any] | None) -> bool:
    """Whether the record asks for set-membership judging."""
    if not isinstance(ground_truth, Mapping):
        return False
    if ground_truth.get("method") == MEMBERSHIP_METHOD:
        return True
    value = ground_truth.get("value")
    return isinstance(value, Mapping) and "mask_path" in value and "mask_id" in value


def _resolve_mask_file(mask_path: str, asset_root: Path | None) -> Path:
    candidate = Path(asset_root) / mask_path if asset_root is not None else None
    if candidate is not None and candidate.is_file():
        return candidate
    return Path(mask_path)


def _load_region(
    mask_path: str, mask_id: Any, *, asset_root: Path | None
) -> tuple[Any, str, list[int]]:
    """``(binary, blob, shape)`` for the region ``mask == mask_id``."""
    import numpy as np  # build-time only: the scorer image is stdlib

    mask_file = _resolve_mask_file(str(mask_path), asset_root)
    if not mask_file.is_file():
        raise ValueError(f"membership_mask_unreadable:{mask_file}")
    try:
        label = int(mask_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"membership_mask_id_not_an_integer:{mask_id!r}") from exc

    import nibabel as nib

    try:
        array = np.asarray(nib.load(str(mask_file)).dataobj)
    except (OSError, ValueError) as exc:
        raise ValueError(f"membership_mask_unreadable:{mask_file}:{exc}") from exc
    binary = array == label
    if int(binary.sum()) == 0:
        raise ValueError(f"membership_mask_empty:{mask_file}:label={label}")

    packed = np.packbits(
        binary.reshape(-1).astype(np.uint8), bitorder="little"
    ).tobytes()
    compressed = zlib.compress(packed)
    if len(compressed) > MEMBER_SET_MAX_BYTES:
        raise ValueError(
            f"member_set_too_large:{mask_file}:{len(compressed)}>{MEMBER_SET_MAX_BYTES}"
        )
    return binary, base64.b64encode(compressed).decode("ascii"), [
        int(dim) for dim in binary.shape
    ]


def pack_member_set(
    mask_path: str | Path, mask_id: Any, *, asset_root: Path | None = None
) -> tuple[str, list[int]]:
    """``(blob, shape)`` for the region ``mask == mask_id``, in the wire format."""
    _, blob, shape = _load_region(str(mask_path), mask_id, asset_root=asset_root)
    return blob, shape


def _coerce_point(value: Any) -> tuple[int, int, int] | None:
    """A 3-list of whole numbers as an integer triple, or None."""
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


def _representative_point(binary: Any) -> tuple[int, int, int]:
    """The first in-mask voxel in C-order: deterministic, inside by construction."""
    indices = tuple(int(axis[0]) for axis in binary.nonzero())
    return indices[0], indices[1], indices[2]


def apply_membership(
    rec: Mapping[str, Any], *, asset_root: Path | None = None
) -> Mapping[str, Any]:
    """Fold a declared membership contract into one raw record.

    Returns ``rec`` unchanged when nothing is declared. Otherwise the scorer
    grows an explicit v2.1 rubric item that judges the answer field by set
    membership, and the ground truth is trimmed to exactly the answer schema's
    fields -- the representative point (declared when the record carries one
    and it lies inside the mask; derived from the mask otherwise) is all the
    scorer keeps for audit.
    """
    ground_truth = rec.get("ground_truth")
    if not declares_membership(ground_truth):
        return rec
    task_id = rec.get("task_id")
    value = ground_truth.get("value")
    if not isinstance(value, Mapping):
        raise ValueError(f"membership_ground_truth_value_missing:{task_id}")
    schema = rec.get("answer_schema")
    fields = schema.get("fields") if isinstance(schema, Mapping) else None
    if not (isinstance(fields, list) and len(fields) == 1):
        raise ValueError(f"membership_answer_schema_must_declare_one_field:{task_id}")
    field = str(fields[0])

    mask_path = value.get("mask_path")
    if not isinstance(mask_path, str) or not mask_path:
        raise ValueError(f"membership_mask_path_missing:{task_id}")
    binary, blob, shape = _load_region(
        mask_path, value.get("mask_id"), asset_root=asset_root
    )

    point = _coerce_point(value.get(field))
    if point is None:
        point = _representative_point(binary)
    elif not (
        all(0 <= coord < dim for coord, dim in zip(point, shape))
        and bool(binary[point])
    ):
        raise ValueError(
            f"membership_point_outside_mask:{task_id}:{field}:{list(point)}"
        )

    item = {
        "id": field,
        "pred_path": field,
        "gt_path": field,
        "comparator": COMPARATOR,
        "member_set": blob,
        "member_shape": shape,
        "weight": 1.0,
        "required": True,
    }
    scorer = dict(rec.get("scorer") or {})
    scorer.setdefault("primary", "deterministic_structured_v2_1")
    scorer.setdefault("rubric_version", "sciencevision.deterministic_structured.v2.1")
    scorer["rubric"] = [*list(scorer.get("rubric") or []), item]
    return {
        **rec,
        "scorer": scorer,
        "ground_truth": {**ground_truth, "value": {field: [int(coord) for coord in point]}},
    }
