"""The deterministic scoring path must not be able to reach a model.

Two facts, both narrower than the directory-wide rule "no module under
``sci3d_bench/scorers/`` mentions a judge", which is false on this tree: the
package facade re-exports the shadow judges on purpose, and ``llm_judge.py``
and ``refseg_text_judge.py`` exist to carry the scoring model for diagnostics.

1. every scorer module except those three imports no model client and no
   judge -- the deterministic closure, at any nesting depth;
2. the scorer image cannot import a judge at all, because the package
   ``__init__`` it is built with is ``MINIMAL_SCORERS_INIT`` and not the
   facade.

The scan is a static source check over imports, so it sees
``import anthropic``, ``from anthropic import Anthropic``,
``from sci3d_agent import llm_client``, ``from . import refseg_text_judge``
and an import inside a function alike.  It is not a runtime proof: it cannot
see a model reached without an import statement.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCORERS = ROOT / "sci3d_bench" / "scorers"
BUNDLE_ROOT = ROOT / "scorer_bundle"

# A model client can arrive under any of these names: the vendor SDK, this
# repository's client (``sci3d_agent/llm_client.py``), or one of the judges
# that only want a client handed to them.
MODEL_CLIENTS = ("anthropic", "openai", "llm_client")
JUDGES = ("llm_judge", "refseg_text_judge")
FORBIDDEN = MODEL_CLIENTS + JUDGES

# Excluded from the scan, each for its own reason.  The package facade
# re-exports the shadow judges, and the image replaces it wholesale (pinned
# below).  The two shadow judges take a client injected at construction
# instead of importing one, which is what the excluded-modules test pins.
# Everything
# else in the directory -- including anything added later, or in a
# subpackage -- is deterministic-path code.
EXCLUDED = {"__init__.py", "llm_judge.py", "refseg_text_judge.py"}


def _imports(path: Path) -> set[str]:
    """Every module and every imported name, so no spelling hides a target.

    ``from pkg import judge`` names the judge only in ``node.names``, and a
    relative ``from . import judge`` carries no module at all.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if module.strip("."):
                names.add(module)
            names |= {
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            }
    return names


def _model_imports(
    paths: list[Path], *, root: Path, needles: tuple[str, ...] = FORBIDDEN
) -> list[str]:
    return sorted(
        f"{path.relative_to(root).as_posix()}: {name}"
        for path in paths
        for name in _imports(path)
        if any(bad in name for bad in needles)
    )


def _scanned_modules() -> list[Path]:
    return sorted(
        path
        for path in SCORERS.rglob("*.py")
        if path.relative_to(SCORERS).as_posix() not in EXCLUDED
    )


def test_the_deterministic_closure_never_imports_a_model_client():
    scanned = _scanned_modules()
    assert scanned, f"no scorer modules found under {SCORERS}; this test would pass vacuously"
    assert _model_imports(scanned, root=SCORERS) == []


def test_the_excluded_modules_never_import_a_model_client():
    """The exclusion is safe only while the judges are handed their client."""
    excluded = sorted(SCORERS / name for name in EXCLUDED if (SCORERS / name).is_file())
    assert len(excluded) == len(EXCLUDED), (
        f"an excluded module was renamed or removed, so the exclusion set is "
        f"stale: expected {sorted(EXCLUDED)} under {SCORERS}"
    )
    assert _model_imports(excluded, root=SCORERS, needles=MODEL_CLIENTS) == []


def test_the_scorer_image_cannot_import_a_judge():
    # The image's package __init__ is overwritten at build time, so the judges
    # are not importable inside the container even though they exist here.
    source = (BUNDLE_ROOT / "build_context.py").read_text(encoding="utf-8")
    marker = "MINIMAL_SCORERS_INIT = "
    assert marker in source, f"{marker!r} was renamed or moved out of build_context.py"
    minimal = source.split(marker, 1)[1].split("\n", 1)[0]
    for bad in ("llm_judge", "refseg_text_judge", "text_rubric", "mask_dice"):
        assert bad not in minimal, f"{bad} must not be bundled into the scorer image"
    for required in ("deterministic_answer", "extent_voxel_tolerance", "base"):
        assert required in minimal, f"{required} must be bundled"


def test_the_staged_image_replaces_the_facade_and_imports_no_judge(tmp_path: Path):
    """The constant above enforces nothing unless the build writes it."""
    context = tmp_path / "context"
    sys.path.insert(0, str(BUNDLE_ROOT))
    module_name = "_scoring_path_is_offline_build_context"
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, BUNDLE_ROOT / "build_context.py"
        )
        assert spec and spec.loader
        build_context = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = build_context
        spec.loader.exec_module(build_context)
        build_context.stage(ROOT, context)
    finally:
        sys.path.remove(str(BUNDLE_ROOT))
        sys.modules.pop(module_name, None)

    staged_sources = sorted((context / "src").rglob("*.py"))
    assert staged_sources, "the staged context carries no Python source"
    staged_init = context / "src" / "sci3d_bench" / "scorers" / "__init__.py"
    assert staged_init.exists(), "the build must write the image's package facade"
    assert staged_init.read_text(encoding="utf-8") == build_context.MINIMAL_SCORERS_INIT
    assert _model_imports(staged_sources, root=context) == []
