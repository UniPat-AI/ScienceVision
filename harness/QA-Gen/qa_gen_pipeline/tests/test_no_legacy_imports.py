from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "qagen"
FORBIDDEN = ("sciencevision_taskgen", "ScienceVisionTaskGenerator")


def test_new_pipeline_has_no_runtime_dependency_on_legacy_task_generator() -> None:
    assert SOURCE_ROOT.is_dir(), "qagen source package must exist under src/qagen"

    offenders: list[str] = []
    for source_path in SOURCE_ROOT.rglob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in source:
                offenders.append(f"{source_path.relative_to(PACKAGE_ROOT)}: {token}")

    assert not offenders, "legacy task-generator dependency found:\n" + "\n".join(offenders)
