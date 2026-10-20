from __future__ import annotations

from pathlib import Path

from tools import sync_from_sciencevision as sync


REPO_ROOT = Path(__file__).resolve().parents[1]
QAGEN_ROOT = REPO_ROOT / "QA-Gen" / "qa_gen_pipeline"
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".yaml", ".yml"}


def test_vnext_mirror_manifest_is_explicit_and_preserves_legacy_owner() -> None:
    patterns = sync.parse_manifest(REPO_ROOT / ".mirror-include")

    assert {
        "QA-Gen/ScienceVisionTaskGenerator/**",
        "QA-Gen/qa_gen_pipeline/**",
        "QA-Gen/docs/Question-Rubric.md",
        "QA-Gen/docs/QA-gen-pipeline-vNext-plan.md",
        "QA-Gen/docs/QA-gen-pipeline-target.md",
        "QA-Gen/docs/question-family-gt-redesign.md",
        "QA-Gen/docs/v3.2-expert-feedback-triage.md",
        "tests/viewer/test_molstar_mcp_adapter.cjs",
    }.issubset(patterns)


def test_vnext_does_not_reference_legacy_qagen_indexes() -> None:
    forbidden = ("data/qagen_index.json", "viewer/qagen_index.json")
    offenders: list[str] = []

    for path in sorted(QAGEN_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        contents = path.read_text(encoding="utf-8")
        for reference in forbidden:
            if reference in contents:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {reference}")

    assert not offenders, "vNext crossed a legacy index boundary:\n" + "\n".join(offenders)


def test_qagen_runtime_state_is_pruned_from_mirror_plans(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    package = source / "QA-Gen" / "qa_gen_pipeline"
    (package / "src").mkdir(parents=True)
    (package / "src" / "module.py").write_text("", encoding="utf-8")
    (package / ".qagen" / "private").mkdir(parents=True)
    (package / ".qagen" / "private" / "receipt.json").write_text(
        "{}\n", encoding="utf-8"
    )

    plan = sync.compute_plan(source, destination, ["QA-Gen/qa_gen_pipeline/**"])

    assert [path.as_posix() for path in plan.copies] == [
        "QA-Gen/qa_gen_pipeline/src/module.py"
    ]
    assert ".qagen" in sync._PRUNE_DIRS
    assert ".qagen/" in (QAGEN_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert '"/.qagen/**"' in (QAGEN_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_vnext_controlled_viewer_bridge_is_explicit() -> None:
    viewer_root = REPO_ROOT / "viewer"
    bridge = viewer_root / "shared" / "viewer-mcp-api.js"
    molstar_adapter = viewer_root / "shared" / "molstar-mcp-adapter.js"
    niivue_adapter = viewer_root / "shared" / "niivue-mcp-adapter.js"

    assert bridge.is_file()
    assert molstar_adapter.is_file()
    assert niivue_adapter.is_file()
    assert "ScienceVisionViewerMCP" in bridge.read_text(encoding="utf-8")
    molstar_adapter_text = molstar_adapter.read_text(encoding="utf-8")
    assert "viewer_measure_distance" in molstar_adapter_text
    assert "function createProvider(viewer)" in molstar_adapter_text
    assert "source: item.source" not in molstar_adapter_text
    assert "name: basename(item.source)" not in molstar_adapter_text
    assert "format: item.format" not in molstar_adapter_text
    assert "viewer_bbox" in niivue_adapter.read_text(encoding="utf-8")

    molstar_page = (viewer_root / "molstar.html").read_text(encoding="utf-8")
    niivue_page = (viewer_root / "niivue.html").read_text(encoding="utf-8")
    assert '/viewer/shared/viewer-mcp-api.js' in molstar_page
    assert '/viewer/shared/molstar-mcp-adapter.js' in molstar_page
    assert "__ScienceVisionCreateMolstarMCPProvider(viewer, [" not in molstar_page
    assert '/viewer/shared/viewer-mcp-api.js' in niivue_page
    assert '/viewer/shared/niivue-mcp-adapter.js' in niivue_page
    assert (REPO_ROOT / "tests/viewer/test_molstar_mcp_adapter.cjs").is_file()
