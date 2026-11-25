"""Switch only among the public NIfTI images declared for this task.

The same implementation runs in the external worker and the bundled runtime.
It never searches neighbouring files: the staged asset manifest is the boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec, register
from sci3d_agent.mcp.tools.vision3d._store import get_store


def manifest_assets() -> list[dict[str, Any]] | None:
    value = os.environ.get("SV_MCP_TASK_ASSET_MANIFEST", "").strip()
    if not value:
        return None
    payload = json.loads(Path(value).read_text())
    if payload.get("schema_version") != "sciencevision.mcp_task_assets.v1":
        raise ValueError("unsupported task asset manifest")
    rows = payload.get("assets")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("task asset manifest must declare assets")
    ids, paths = set(), set()
    for row in rows:
        asset_id, value = row.get("asset_id"), row.get("asset_path")
        if not isinstance(asset_id, str) or not asset_id or asset_id in ids:
            raise ValueError("task asset identifiers must be unique and non-empty")
        if not isinstance(value, str) or not value or value in paths:
            raise ValueError("task asset paths must be unique and non-empty")
        path = Path(value)
        if os.environ.get("SV_MCP_TASK_ISOLATED") == "1":
            if path.parent != Path("/data") or path.resolve().parent != Path("/data"):
                raise ValueError("isolated task assets must be staged directly under /data")
        ids.add(asset_id)
        paths.add(value)
    if sum(row.get("role") == "primary" and row.get("viewer") in (None, "", "niivue")
           and row["asset_path"].lower().endswith((".nii", ".nii.gz")) for row in rows) > 1:
        raise ValueError("task asset manifest declares multiple primary images")
    return rows


def sequence_entries(rows: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    paths = []
    for row in rows:
        path = str(row.get("asset_path") or row.get("path") or "")
        if row.get("role") not in {"primary", "sequence"}:
            continue
        if row.get("viewer") not in {None, "", "niivue"}:
            continue
        if path.lower().endswith((".nii", ".nii.gz")) and path not in paths:
            paths.append(path)
    # Match the platform's asset-order default and collision namespace.
    bases = [Path(p).name[:-7] if p.lower().endswith(".nii.gz") else Path(p).name[:-4] for p in paths]
    return tuple((base if bases.count(base) == 1 else "./" + Path(path).as_posix().lstrip("/"), path)
                 for base, path in zip(bases, paths))


def initialize_sequences(session):
    if session.kind != "nifti":
        return session
    rows = manifest_assets()
    if rows is None:
        return session
    entries = sequence_entries(rows)
    current = next((name for name, path in entries if Path(path).resolve() == Path(session.asset_path).resolve()), None)
    if current is None or len(entries) < 2:
        return session
    return replace(session, sequences=entries, sequence=current,
                   sequence_mask_path=session.mask_path,
                   sequence_windows=((current, session.slice.window),))


def sequence_state(session) -> dict[str, Any]:
    if len(session.sequences) < 2:
        return {}
    import nibabel as nib
    return {"sequence": session.sequence,
            "sequence_focus_world": list(_focus_world(session, nib.load(session.asset_path))),
            "sequence_focus_semantics": "continuous world anchor; displayed slice indices are nearest voxels",
            "sequences": [{"name": name, "file": Path(path).name} for name, path in session.sequences]}


def task_tools(tools, rows=None):
    """Specialize only the new capability, preserving every existing schema."""
    rows = manifest_assets() if rows is None else rows
    if rows is None:
        return list(tools)  # image capability catalog, before task staging
    names = [name for name, _ in sequence_entries(rows)]
    result = []
    for tool in tools:
        if tool.name != "sv3d_switch_sequence":
            result.append(tool)
        elif len(names) > 1:
            schema = {**tool.input_schema, "properties": {**tool.input_schema["properties"],
                      "sequence": {"type": "string", "enum": names}}}
            result.append(replace(tool, input_schema=schema))
    return result


def _focus_world(session, image):
    import numpy as np
    from sci3d_agent.mcp.tools.vision3d._slice_math import _AXIS_TO_NP
    shape = image.shape
    axis = _AXIS_TO_NP[session.slice.axis]
    previous = session.sequence_focus_slice
    if previous is not None and session.sequence_focus_world is not None:
        if session.slice == previous:
            return session.sequence_focus_world
        values = np.linalg.solve(image.affine, [*session.sequence_focus_world, 1])[:3]
    else:
        values = np.array([(extent - 1) / 2 for extent in shape], dtype=float)
    memory = getattr(session.slice, "axis_indices", (None,) * 3)
    old_memory = getattr(previous, "axis_indices", (None,) * 3)
    for i, value in enumerate(memory):
        if value is not None and value != old_memory[i]:
            values[i] = value
    if previous is None or (session.slice.axis, session.slice.index, session.slice.fraction) != (previous.axis, previous.index, previous.fraction):
        values[axis] = float(session.slice.index) if session.slice.index is not None else float(session.slice.fraction or 0) * (shape[axis] - 1)
    return tuple(float(v) for v in (image.affine @ [*values, 1])[:3])


def update_slice_focus(session, slice_state):
    """Remember each explicit axis move, including on the legacy one-axis model."""
    candidate = replace(session, slice=slice_state)
    if len(session.sequences) < 2:
        return candidate
    import nibabel as nib
    image = nib.load(session.asset_path)
    previous = replace(session, sequence_focus_world=_focus_world(session, image),
                       sequence_focus_slice=session.slice)
    focus = _focus_world(replace(previous, slice=slice_state), image)
    return replace(candidate, sequence_focus_world=focus, sequence_focus_slice=slice_state)


def remember_crosshair(sid, planes):
    """The legacy handler returns three axes but stores only the active one."""
    store = get_store()
    with store._lock:
        session = store.get(sid)
        if len(session.sequences) < 2:
            return
        import nibabel as nib
        image = nib.load(session.asset_path)
        voxel = [planes[axis]["index"] for axis in ("sagittal", "coronal", "axial")]
        world = tuple(float(v) for v in (image.affine @ [*voxel, 1])[:3])
        store.update(replace(session, sequence_focus_world=world, sequence_focus_slice=session.slice))


def _mask_for_target(session, target, original):
    """Resample a public overlay with nearest labels, never treat it as an image."""
    import nibabel as nib
    import numpy as np
    from nibabel.processing import resample_from_to
    from sci3d_agent.mcp.runtime.paths import render_cache_dir
    if original is None:
        return None
    mask = nib.load(original)
    if mask.shape == target.shape and np.allclose(mask.affine, target.affine):
        return original
    key = hashlib.sha256((str(Path(original).resolve()) + str(Path(original).stat().st_mtime_ns)
                          + repr(target.shape) + repr(target.affine.tolist())).encode()).hexdigest()
    path = render_cache_dir() / "sequence-overlays" / f"{key}.nii.gz"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        nib.save(resample_from_to(mask, (target.shape, target.affine), order=0), path)
    return str(path)


def switch_sequence(args: Mapping[str, Any]) -> dict[str, Any]:
    # Loading, validation and the immutable replacement form one transaction.
    # Keep other session mutations from interleaving with the slow image read.
    with get_store()._lock:
        return _switch_sequence_locked(args)


def _switch_sequence_locked(args: Mapping[str, Any]) -> dict[str, Any]:
    import nibabel as nib
    import numpy as np
    from sci3d_agent.mcp.tools.vision3d._slice_math import _AXIS_TO_NP
    from sci3d_agent.mcp.tools.vision3d.session import _get_state_handler
    sid, name = args.get("session_id"), args.get("sequence")
    try:
        store = get_store()
        session = store.get(sid)
        known = dict(session.sequences)
        if session.kind != "nifti" or len(known) < 2 or name not in known:
            raise ValueError(f"sequence unavailable: {name!r}; available: {list(known)}")
        if name != session.sequence:
            source, target = nib.load(session.asset_path), nib.load(known[name])
            if len(source.shape) != 3 or len(target.shape) != 3:
                raise ValueError("sequence switching requires 3D NIfTI images")
            if source.header.get_xyzt_units()[0] != target.header.get_xyzt_units()[0]:
                raise ValueError("sequences must use the same spatial units")
            world = _focus_world(session, source)
            voxel = np.linalg.solve(target.affine, [*world, 1])[:3]
            if not np.isfinite(voxel).all() or np.any(voxel < -1e-6) or np.any(voxel > np.array(target.shape) - 1 + 1e-6):
                raise ValueError("current world focus is outside the target sequence field of view")
            # Validate/read the target before replacing the immutable session.
            np.asanyarray(target.dataobj)
            indices = tuple(int(round(v)) for v in voxel)
            axis = _AXIS_TO_NP[session.slice.axis]
            changes = {"index": indices[axis], "fraction": indices[axis] / max(1, target.shape[axis] - 1)}
            if hasattr(session.slice, "axis_indices"):
                changes["axis_indices"] = indices
            mask_path = _mask_for_target(session, target, session.sequence_mask_path)
            masks = session.masks_info
            if masks:
                # Recompute per-axis maxima from the target-grid labels rather
                # than transforming rounded indices as if they were one point.
                mask_data = np.asanyarray(nib.load(mask_path).dataobj)
                mapped = []
                for mask in masks:
                    label = mask_data == int(mask.mask_id)
                    best = {a: int(np.argmax(label.sum(axis=tuple(j for j in range(3) if j != i))))
                            for a, i in _AXIS_TO_NP.items()} if label.any() else {}
                    mapped.append(replace(mask, best_slices=best))
                masks = tuple(mapped)
            windows = dict(session.sequence_windows)
            if name not in windows:
                from .session import _initial_slice_for_nifti
                inspection = REGISTRY["sv_inspect_nifti"].handler({"path": known[name]})
                if "error" in inspection:
                    raise ValueError(inspection["error"])
                windows[name] = _initial_slice_for_nifti(inspection).window
            changes.update(window=windows[name], level=sum(windows[name]) / 2)
            corners = np.array([[x, y, z, 1] for x in (0, target.shape[0] - 1)
                                for y in (0, target.shape[1] - 1) for z in (0, target.shape[2] - 1)])
            bounds = (target.affine @ corners.T).T[:, :3]
            next_slice = replace(session.slice, **changes)
            next_session = replace(session, asset_path=known[name], sequence=name,
                                   affine_4x4=tuple(tuple(float(v) for v in row) for row in target.affine),
                                   bbox=(tuple(bounds.min(axis=0)), tuple(bounds.max(axis=0))),
                                   slice=next_slice, mask_path=mask_path, masks_info=masks,
                                   sequence_focus_world=world, sequence_focus_slice=next_slice,
                                   sequence_windows=tuple(windows.items()))
            store.update(next_session)
        return {"state": _get_state_handler({"session_id": sid}),
                "replay_input": {"session_id": sid, "sequence": name}}
    except Exception as exc:
        return {"error": str(exc)}


TOOLS = [register(ToolSpec(
    name="sv3d_switch_sequence",
    description="Switch a NIfTI session to an available public sequence. Read sequence/sequences in sv3d_get_state; preserves world focus, view and public overlay. Repeating a switch is deterministic.",
    input_schema={"type": "object", "required": ["session_id", "sequence"],
                  "properties": {"session_id": {"type": "string"}, "sequence": {"type": "string"}},
                  "additionalProperties": False},
    handler=switch_sequence, category="F"))]
