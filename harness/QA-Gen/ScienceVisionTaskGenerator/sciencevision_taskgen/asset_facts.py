"""Curated, deterministic asset-fact registry for grounded question generation.

Raw asset manifests (``viewer/assets.json``) carry only ``name`` / ``path`` /
``format`` / ``thumb`` — no scientific entities.  Grounding a template question
to a *specific* asset without an LLM (and without the raw file on disk)
therefore requires a curated fact table.  This module is that table plus the
lookup / predicate / render helpers the :class:`BuilderStage` uses to

  (a) select only assets a template can be *truthfully* asked about, and
  (b) fill instruction slots with the asset's real per-asset entities.

Design contract
---------------
* Facts are keyed by a normalized asset key (upper-cased PDB id, or a
  lower-cased short name).  :func:`lookup_facts` tries several normalizations
  of ``asset["name"]`` so ``"4HHB"``, ``"4HHB (PDB)"`` and ``"4HHB (BinaryCIF)"``
  all resolve to the same entry.
* A template declares ``requires=(<fact_key>, ...)``.  An asset qualifies for
  that template only if it supplies **every** required key with a truthy value
  (:func:`asset_satisfies`).  Instruction slots ``{fact_key}`` are filled from
  the same dict, plus the always-available ``{asset_name}``
  (:func:`render_instruction`).
* Unknown assets return ``{}`` → they satisfy only templates with no
  ``requires`` and render un-slotted instructions verbatim.  This is
  deliberate: emitting a generic-but-correct question (or none) always beats a
  confidently *mis-grounded* one — e.g. asking for the "heme cofactor" of
  rhodopsin (which has retinal, not heme).

Extending
---------
Add an entry keyed by the asset's short name.  Keep values flat
(str / int / float / tuple).  When you add a new required fact key to a
template, make sure the assets you want that template to draw from carry it.
"""

from __future__ import annotations

import string
from typing import Any

# ---------------------------------------------------------------------------
# The registry.  Keyed by normalized short name (see _candidate_keys).
# ---------------------------------------------------------------------------

ASSET_FACTS: dict[str, dict[str, Any]] = {
    # --- biology: proteins / nucleic acids / small molecules -----------------
    # cofactor-bearing proteins (qualify for cofactor-oriented templates)
    "4HHB": {
        "molecule_type": "protein",
        "molecule_name": "human deoxyhaemoglobin",
        "cofactor": "heme group",
        "chain_count": 4,
    },
    "1A3N": {
        "molecule_type": "protein",
        "molecule_name": "human haemoglobin (R state)",
        "cofactor": "heme group",
        "chain_count": 4,
    },
    "1MBN": {
        "molecule_type": "protein",
        "molecule_name": "sperm-whale myoglobin",
        "cofactor": "heme group",
        "chain_count": 1,
    },
    "1F88": {
        "molecule_type": "protein",
        "molecule_name": "bovine rhodopsin",
        "cofactor": "retinal chromophore",
        "chain_count": 2,
    },
    "3PQR": {
        "molecule_type": "protein",
        "molecule_name": "metarhodopsin II",
        "cofactor": "retinal chromophore",
        "chain_count": 1,
    },
    # proteins without a classical cofactor (do NOT qualify for cofactor
    # templates — they carry other facts a different template can use)
    "1IGT": {
        "molecule_type": "protein",
        "molecule_name": "intact IgG2a monoclonal antibody",
        "chain_count": 12,
    },
    "1HVR": {
        "molecule_type": "protein",
        "molecule_name": "HIV-1 protease with XK-263 inhibitor",
        "ligand": "XK-263 inhibitor",
        "chain_count": 2,
    },
    "6LU7": {
        "molecule_type": "protein",
        "molecule_name": "SARS-CoV-2 main protease (Mpro)",
        "ligand": "N3 inhibitor",
        "chain_count": 1,
    },
    "4EY7": {
        "molecule_type": "protein",
        "molecule_name": "human acetylcholinesterase with donepezil",
        "ligand": "donepezil",
    },
    # nucleic acids (no cofactor)
    "1BNA": {
        "molecule_type": "dna",
        "molecule_name": "Dickerson–Drew B-DNA dodecamer",
    },
    "1EHZ": {
        "molecule_type": "rna",
        "molecule_name": "yeast phenylalanine tRNA",
    },
    "1L2Y": {
        "molecule_type": "protein",
        "molecule_name": "Trp-cage miniprotein",
    },
    "4AKE": {
        "molecule_type": "protein",
        "molecule_name": "E. coli adenylate kinase (open apo form)",
        "chain_count": 2,
    },
    # small molecules (PubChem 3D conformers; qualify for molecule-name-grounded
    # templates such as biology/V6 stereochemistry).
    "aspirin": {
        "molecule_type": "small molecule",
        "molecule_name": "aspirin (acetylsalicylic acid)",
    },
    "ibuprofen": {
        "molecule_type": "small molecule",
        "molecule_name": "ibuprofen",
    },
    "caffeine": {
        "molecule_type": "small molecule",
        "molecule_name": "caffeine",
    },
    "acetaminophen": {
        "molecule_type": "small molecule",
        "molecule_name": "acetaminophen (paracetamol)",
    },
    "penicillin_g": {
        "molecule_type": "small molecule",
        "molecule_name": "penicillin G",
    },

    # --- medical: 3D MRI volumes ---------------------------------------------
    # Medical-Decathlon Task04 hippocampus MRI (mono-modal MRI).  anatomy names
    # the hippocampal structure the medical templates are grounded on.
    "hippocampus_001": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_002": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_003": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_004": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_005": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_006": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_007": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippocampus_009": {"anatomy": "hippocampus", "modality": "MRI"},
    "hippo_001_m3d": {"anatomy": "hippocampus", "modality": "MRI"},

    # --- industrial: CAD parts / meshes --------------------------------------
    # object_name identifies the part; part_feature names a real machined
    # feature (only assets that actually have one carry it, gating V2/V3).
    "DamagedHelmet": {"object_name": "battle-damaged sci-fi helmet"},
    "Box": {"object_name": "cube"},
    "Duck": {"object_name": "rubber duck"},
    "Avocado": {"object_name": "avocado"},
    "BoomBox": {"object_name": "boombox"},
    "occt_screw": {"object_name": "screw", "part_feature": "helical thread"},
    "occt_bearing": {"object_name": "rolling-element bearing", "part_feature": "bearing raceway"},
    "occt_linkrods": {"object_name": "link-rods assembly"},
    "trimesh_featuretype": {
        "object_name": "machined feature-test part",
        "part_feature": "machined slots and holes",
    },
    "stanford_bunny": {"object_name": "Stanford bunny"},
    "trimesh_bunny": {"object_name": "Stanford bunny"},
    "threejs_walt_head": {"object_name": "Walt Disney head bust"},

    # --- robotics: articulated models ----------------------------------------
    # robot_name identifies the model; morphology is deliberately NOT stored so
    # it can never leak into the V6 morphology-classification question.
    "kuka_iiwa": {
        "robot_name": "KUKA LBR iiwa arm",
        "robot_type": "7-DOF serial manipulator",
        "dof": 7,
    },
    "r2d2": {
        "robot_name": "R2-D2 droid model",
        "robot_type": "articulated multi-limb robot",
    },
    # gazebo_shapes is a set of primitive shapes, not a robot → intentionally
    # unregistered so it is excluded from robot-grounded templates.

    # --- topological photonics: band plots / layouts -------------------------
    # plot_type gates the band-plot templates; layout_type gates the layer
    # coverage template.  (Trivial-vs-topological is the V4 answer and is NOT
    # stored.)
    "nanobeam_band_d1_0p0": {"plot_type": "photonic band diagram"},
    "nanobeam_band_d1_1p0": {"plot_type": "photonic band diagram"},
    "nanobeam_edge_vs_noedge": {"plot_type": "photonic band diagram"},
    "nanobeam_gap_dependence_a300_w07": {"plot_type": "photonic band diagram"},
    "nanobeam_measured_spectrum": {"plot_type": "measured photonic spectrum"},
    "klayout_t10": {"layout_type": "GDSII photonic layout"},
    # mpb_diamond_lattice is an MPB simulation spec (neither a band plot nor a
    # layout) → unregistered; it serves only the generic V6 template.

    # --- astronomy: ephemeris scenes -----------------------------------------
    # Asset names in the manifest are per-scene descriptions; keyed verbatim.
    # planet gates the Saturn-ring templates; system gates the solar-system
    # coplanarity template — so the Saturn question can never be asked of the
    # solar-system scene (or vice-versa).
    "V2 · 土星合相时土星环的可见性": {"planet": "Saturn", "feature": "ring system"},
    "V4 · 土星合相事件的多视角自一致性": {"planet": "Saturn", "feature": "ring system"},
    "V6 · 太阳系行星轨道共面性": {"system": "solar system"},
}

# Fact keys that name a scientific entity (used to populate
# asset_grounding.verified_entities when a template does not declare requires).
_ENTITY_KEYS: tuple[str, ...] = (
    "molecule_name",
    "cofactor",
    "ligand",
    "anatomy",
    "object_name",
    "part_feature",
    "robot_name",
    "plot_type",
    "layout_type",
    "planet",
    "system",
)


# ---------------------------------------------------------------------------
# Lookup + predicates
# ---------------------------------------------------------------------------

def _candidate_keys(name: str) -> list[str]:
    """Return normalization candidates for an asset name, most-specific first.

    ``"4HHB (BinaryCIF)"`` → ``["4HHB", "4HHB", "4hhb", "4hhb", ...]``.
    """
    name = (name or "").strip()
    base = name.split("(")[0].strip()  # drop trailing "(PDB)" / "(MOL2)" etc.
    first = base.split()[0] if base.split() else base
    seen: list[str] = []
    for key in (base.upper(), first.upper(), base.lower(), first.lower(), base, first):
        if key and key not in seen:
            seen.append(key)
    return seen


def lookup_facts(asset: dict[str, Any]) -> dict[str, Any]:
    """Return the curated fact dict for *asset*, or ``{}`` if unknown."""
    for key in _candidate_keys(str(asset.get("name", ""))):
        if key in ASSET_FACTS:
            return ASSET_FACTS[key]
    return {}


def asset_satisfies(asset: dict[str, Any], requires: tuple[str, ...]) -> bool:
    """True iff *asset* carries every required fact key with a truthy value.

    A template with empty ``requires`` is satisfied by any asset.
    """
    if not requires:
        return True
    facts = lookup_facts(asset)
    return all(facts.get(key) for key in requires)


def display_name(asset: dict[str, Any]) -> str:
    """Short, human-facing asset name for the ``{asset_name}`` slot."""
    name = str(asset.get("name", "")).strip()
    base = name.split("(")[0].strip()
    return base or name or str(asset.get("asset_id", "the asset"))


# ---------------------------------------------------------------------------
# Instruction rendering
# ---------------------------------------------------------------------------

def _slot_values(asset: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {"asset_name": display_name(asset)}
    for key, val in lookup_facts(asset).items():
        if isinstance(val, (str, int, float)):
            values[key] = val
    return values


def render_instruction(instruction: str, asset: dict[str, Any]) -> str:
    """Fill ``{slot}`` placeholders in *instruction* from *asset* facts.

    Renders **only** when every referenced slot is resolvable from the asset's
    facts (plus ``asset_name``); otherwise returns *instruction* unchanged.
    This keeps un-slotted templates byte-identical to their source text and
    guarantees a rendered instruction never contains a dangling ``{slot}``.
    """
    values = _slot_values(asset)
    try:
        field_names = [
            fname
            for _, fname, _, _ in string.Formatter().parse(instruction)
            if fname
        ]
    except ValueError:
        return instruction
    # Only the top-level field name matters (no attribute/index access used).
    roots = [fn.split(".")[0].split("[")[0] for fn in field_names]
    if roots and not all(root in values for root in roots):
        return instruction
    try:
        return instruction.format(**values)
    except (KeyError, IndexError, ValueError):
        return instruction


def verified_entities(asset: dict[str, Any], requires: tuple[str, ...]) -> list[str]:
    """Entity strings this task is grounded on (for asset_grounding)."""
    facts = lookup_facts(asset)
    keys = requires if requires else _ENTITY_KEYS
    out: list[str] = []
    for key in keys:
        val = facts.get(key)
        if isinstance(val, (str, int, float)) and str(val) not in out:
            out.append(str(val))
    return out
