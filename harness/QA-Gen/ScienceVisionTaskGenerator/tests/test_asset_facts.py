"""Tests for the curated asset-fact registry and grounded instruction binding.

These lock in the P0-B fix: template questions are bound to the *selected
asset's* real entities, so a generic template can no longer produce a
mis-grounded question (e.g. asking for the "heme cofactor" of rhodopsin, which
has retinal, not heme), and assets that cannot truthfully answer a template are
excluded rather than asked a false question.
"""

from __future__ import annotations

from sciencevision_taskgen.asset_facts import (
    asset_satisfies,
    lookup_facts,
    render_instruction,
    verified_entities,
)
from sciencevision_taskgen.pipeline.builder_stage import BuilderStage
from sciencevision_taskgen.templates import TEMPLATES


def _asset(name: str) -> dict:
    return {
        "asset_id": f"biology:{name}",
        "name": name,
        "path": f"datasets/biology/{name}.cif",
        "repo_path": f"datasets/biology/{name}.cif",
        "viewer": "molstar",
        "format": "mmcif",
        "thumb": None,
        "exists_on_disk": True,
    }


# --- lookup / normalization -------------------------------------------------

def test_lookup_normalizes_parenthetical_suffix():
    assert lookup_facts(_asset("4HHB (BinaryCIF)"))["cofactor"] == "heme group"
    assert lookup_facts(_asset("4hhb"))["molecule_name"] == "human deoxyhaemoglobin"


def test_lookup_unknown_returns_empty():
    assert lookup_facts(_asset("ZZZZ")) == {}


# --- predicate --------------------------------------------------------------

def test_asset_satisfies_requires():
    assert asset_satisfies(_asset("4HHB"), ("molecule_name", "cofactor")) is True
    # 1BNA is DNA — no cofactor → excluded from cofactor templates.
    assert asset_satisfies(_asset("1BNA"), ("cofactor",)) is False
    # 1IGT is an antibody — no cofactor.
    assert asset_satisfies(_asset("1IGT"), ("cofactor",)) is False
    # Empty requires → always satisfied.
    assert asset_satisfies(_asset("ZZZZ"), ()) is True


# --- rendering --------------------------------------------------------------

def test_render_fills_slots_per_asset():
    tmpl = TEMPLATES["biology"]["V1"].instruction
    heme = render_instruction(tmpl, _asset("4HHB"))
    retinal = render_instruction(tmpl, _asset("1F88"))
    assert "heme group" in heme and "human deoxyhaemoglobin" in heme
    # The core fix: rhodopsin is asked about retinal, NOT heme.
    assert "retinal chromophore" in retinal
    assert "heme" not in retinal
    assert "{" not in heme and "{" not in retinal  # no dangling slots


def test_render_noop_without_slots():
    plain = "Identify the symmetry axis of the protein."
    assert render_instruction(plain, _asset("4HHB")) == plain


def test_verified_entities():
    assert verified_entities(_asset("1F88"), ("molecule_name", "cofactor")) == [
        "bovine rhodopsin",
        "retinal chromophore",
    ]


# --- end-to-end through the builder ----------------------------------------

def _plan(v_class: str) -> list[dict]:
    return [{"domain": "biology", "v_class": v_class, "replica": 0, "status": "ready"}]


def test_builder_excludes_assets_that_cannot_answer():
    """A biology domain of only non-cofactor assets yields no V1 task (skip,
    not a mis-grounded question)."""
    catalog = {"domains": {"biology": [_asset("1BNA"), _asset("1IGT")]}}
    tasks = BuilderStage({}).build_tasks(_plan("V1"), catalog)
    assert tasks == []


def test_builder_grounds_rhodopsin_to_retinal():
    catalog = {"domains": {"biology": [_asset("1F88")]}}
    tasks = BuilderStage({}).build_tasks(_plan("V1"), catalog)
    assert len(tasks) == 1
    instr = tasks[0]["instruction"]
    assert "retinal chromophore" in instr
    assert "heme" not in instr
    assert tasks[0]["asset_grounding"]["status"] == "grounded"


# ---------------------------------------------------------------------------
# Cross-domain grounding: the same requires+slot mechanism now binds every
# annotated domain to its selected asset's real entity, and excludes assets
# that cannot truthfully answer.  One "grounds" + one "excludes" case per
# domain.
# ---------------------------------------------------------------------------

def _generic_asset(domain: str, name: str, fmt: str = "bin", viewer: str = "generic") -> dict:
    return {
        "asset_id": f"{domain}:{name}",
        "name": name,
        "path": f"datasets/{domain}/{name}.{fmt}",
        "repo_path": f"datasets/{domain}/{name}.{fmt}",
        "viewer": viewer,
        "format": fmt,
        "thumb": None,
        "exists_on_disk": True,
    }


def _build(domain: str, v_class: str, assets: list[dict]) -> list[dict]:
    plan = [{"domain": domain, "v_class": v_class, "replica": 0, "status": "ready"}]
    return BuilderStage({}).build_tasks(plan, {"domains": {domain: assets}})


# --- medical: hippocampus anatomy ------------------------------------------

def test_medical_grounds_to_hippocampus():
    tasks = _build("medical", "V1", [_generic_asset("medical", "hippocampus_001", "nii.gz", "niivue")])
    assert len(tasks) == 1
    instr = tasks[0]["instruction"]
    assert "hippocampus" in instr
    assert "{" not in instr
    assert tasks[0]["asset_grounding"]["verified_entities"] == ["hippocampus"]


def test_medical_excludes_asset_without_anatomy_fact():
    # A raw NRRD head scan carries no curated anatomy fact → excluded, not asked
    # a false hippocampus question.
    tasks = _build("medical", "V1", [_generic_asset("medical", "mr_head", "nrrd", "niivue")])
    assert tasks == []


# --- industrial: object_name + part_feature --------------------------------

def test_industrial_grounds_screw_thread():
    tasks = _build("industrial", "V3", [_generic_asset("industrial", "occt_screw", "step", "occt")])
    assert len(tasks) == 1
    instr = tasks[0]["instruction"]
    assert "screw" in instr and "helical thread" in instr
    assert "{" not in instr


def test_industrial_excludes_featureless_part_from_feature_template():
    # A plain cube (object_name but no part_feature) cannot answer the
    # thread/feature-annotation template (V3 requires part_feature).
    tasks = _build("industrial", "V3", [_generic_asset("industrial", "Box", "glb", "gltf")])
    assert tasks == []
    # ... but it *can* answer the object-level V1 viewpoint template.
    v1 = _build("industrial", "V1", [_generic_asset("industrial", "Box", "glb", "gltf")])
    assert len(v1) == 1
    assert "cube" in v1[0]["instruction"]


# --- robotics: robot_name ---------------------------------------------------

def test_robotics_grounds_to_kuka():
    tasks = _build("robotics", "V1", [_generic_asset("robotics", "kuka_iiwa", "urdf", "urdf")])
    assert len(tasks) == 1
    assert "KUKA LBR iiwa arm" in tasks[0]["instruction"]
    assert "{" not in tasks[0]["instruction"]


def test_robotics_excludes_non_robot_asset():
    # gazebo_shapes is a set of primitive shapes, not a robot → no robot_name.
    tasks = _build("robotics", "V1", [_generic_asset("robotics", "gazebo_shapes", "sdf", "urdf")])
    assert tasks == []


def test_robotics_v6_does_not_leak_morphology():
    # The V6 answer is the morphology class; grounding must name the robot only,
    # never the morphology.
    tasks = _build("robotics", "V6", [_generic_asset("robotics", "kuka_iiwa", "urdf", "urdf")])
    assert len(tasks) == 1
    instr = tasks[0]["instruction"]
    assert "KUKA LBR iiwa arm" in instr
    assert tasks[0]["asset_grounding"]["verified_entities"] == ["KUKA LBR iiwa arm"]


# --- topological photonics: plot_type vs layout_type -----------------------

def test_topo_grounds_band_plot():
    tasks = _build(
        "topological_photonics", "V1",
        [_generic_asset("topological_photonics", "nanobeam_band_d1_0p0", "dat", "bandplot")],
    )
    assert len(tasks) == 1
    assert "photonic band diagram" in tasks[0]["instruction"]


def test_topo_layout_template_excludes_band_plot():
    # V2 is about layer coverage in a *layout*; a band-structure .dat is not a
    # layout and must be excluded rather than asked about non-existent layers.
    band = _generic_asset("topological_photonics", "nanobeam_band_d1_0p0", "dat", "bandplot")
    assert _build("topological_photonics", "V2", [band]) == []
    layout = _generic_asset("topological_photonics", "klayout_t10", "gds", "gdsii")
    tasks = _build("topological_photonics", "V2", [layout])
    assert len(tasks) == 1
    assert "GDSII photonic layout" in tasks[0]["instruction"]


# --- astronomy: Saturn vs solar-system scene -------------------------------

def test_astronomy_saturn_ring_template_excludes_solar_scene():
    saturn = _generic_asset("astronomy", "V2 · 土星合相时土星环的可见性", "json", "saturn")
    solar = _generic_asset("astronomy", "V6 · 太阳系行星轨道共面性", "json", "solar_system")
    # Saturn ring visibility (V2) binds only to the Saturn scene ...
    saturn_only = _build("astronomy", "V2", [solar])
    assert saturn_only == []  # the solar-system scene cannot answer a Saturn-ring question
    grounded = _build("astronomy", "V2", [saturn, solar])
    assert len(grounded) == 1
    assert grounded[0]["asset_grounding"]["verified_entities"] == ["Saturn"]
    # ... and the solar-system coplanarity question (V6) binds only to solar.
    v6 = _build("astronomy", "V6", [saturn])
    assert v6 == []
