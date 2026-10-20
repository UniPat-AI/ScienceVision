"""Contract tests for the on-screen operation guidance.

Reviewers reported that the manager workflow was unreadable from the interface
alone: "导入什么信息，能得到什么信息" — what do I feed each step, and what do I
get back. They asked for the same on the expert side, calling out capture and
MCP work: "界面操作指引要更清晰醒目（如采集/MCP操作 等等）". These tests pin the
guidance that answers both, so a new panel cannot ship without saying what it
consumes and what it produces.
"""

from __future__ import annotations

import re
from pathlib import Path

MANAGER_HTML_PATH = Path("tools/offline_task_manager/manager_app/index.html")
EXPERT_HTML_PATH = Path("tools/offline_task_manager/expert_app/index.html")
I18N_PATH = Path("tools/offline_task_manager/online_app/i18n.js")

VIEWS = ("setup", "distribution", "results")


def _views(markup: str) -> dict[str, str]:
    """Return the markup of each workflow view, keyed by its data-view value."""
    pattern = re.compile(
        r'<section class="view" data-view="(\w+)"[^>]*>(.*?)\n        </section>',
        re.DOTALL,
    )
    found = dict(pattern.findall(markup))
    assert set(found) == set(VIEWS), f"unexpected views: {sorted(found)}"
    return found


def _table(language: str) -> dict[str, str]:
    source = I18N_PATH.read_text(encoding="utf-8")
    opening = "'zh-CN': {" if language == "zh-CN" else "\n    en: {"
    start = source.index(opening) + len(opening)
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                body = source[start:index]
                break
    else:  # pragma: no cover - a malformed table fails the assertions below
        raise AssertionError("unbalanced language table")
    return dict(re.findall(r"'([\w.\- ]+)': '([^']*)'", body))


def _frame_labels(label: str) -> tuple[str, ...]:
    """Return every wording a viewer control is drawn with, one per language.

    Mol* draws its panel in English and the workspace glosses it, so a guide that
    names the control has to name it as the reader will see it. English keeps the
    viewer's own label; Chinese is the gloss keyed to it.
    """
    wording = _table("zh-CN").get(f"viewer.frame.{label}")
    assert wording, f"the viewer panel has no Chinese wording for {label}"
    return (label, wording)


def test_every_view_opens_with_a_visible_operation_guide() -> None:
    markup = MANAGER_HTML_PATH.read_text(encoding="utf-8")
    for view, body in _views(markup).items():
        assert '<details class="view-guide" open>' in body, (
            f"the {view} view must show its guide without an extra click"
        )
        assert 'data-i18n="manager.guide.summary"' in body, view


def test_each_guide_step_states_its_input_and_its_output() -> None:
    markup = MANAGER_HTML_PATH.read_text(encoding="utf-8")
    chinese = _table("zh-CN")
    english = _table("en")
    for view, body in _views(markup).items():
        keys = re.findall(rf'data-i18n="(manager\.guide\.{view}\.s\d+\.io)"', body)
        assert keys, f"the {view} guide must describe at least one step"
        for key in keys:
            assert key in chinese, f"unknown guide key {key}"
            assert "输入：" in chinese[key] and "产出：" in chinese[key], (
                f"{key} must name both the input and the output"
            )
            assert "Input:" in english[key] and "Output:" in english[key], key


def test_a_guide_step_exists_for_every_numbered_tool_panel() -> None:
    markup = MANAGER_HTML_PATH.read_text(encoding="utf-8")
    for view, body in _views(markup).items():
        panels = len(re.findall(r'<header class="tool-header"><span>\d+</span>', body))
        steps = len(re.findall(rf'data-i18n="manager\.guide\.{view}\.s\d+\.title"', body))
        assert steps >= panels, (
            f"the {view} view has {panels} tool panels but only {steps} guide steps"
        )


def test_the_setup_guide_explains_the_end_to_end_flow() -> None:
    markup = MANAGER_HTML_PATH.read_text(encoding="utf-8")
    chinese = _table("zh-CN")
    english = _table("en")
    assert 'data-i18n="manager.guide.flow"' in _views(markup)["setup"]
    # Reviewers' own mental model: import, assign, then the expert sees the task.
    assert "专家" in chinese["manager.guide.flow"]
    assert "expert" in english["manager.guide.flow"].lower()


def test_each_view_points_at_the_next_step() -> None:
    markup = MANAGER_HTML_PATH.read_text(encoding="utf-8")
    chinese = _table("zh-CN")
    for view, body in _views(markup).items():
        key = f"manager.guide.{view}.next"
        assert f'data-i18n="{key}"' in body, f"the {view} guide must name what follows"
        assert key in chinese, key


def _expert_guide(markup: str) -> str:
    """Return the markup of the expert workspace guide."""
    start = markup.index('<details class="task-guide"')
    return markup[start:markup.index("</details>", start)]


def test_the_expert_workspace_opens_with_a_visible_operation_guide() -> None:
    markup = EXPERT_HTML_PATH.read_text(encoding="utf-8")
    assert '<details class="task-guide" open>' in markup, (
        "the expert workspace must show its guide without an extra click"
    )
    assert 'data-i18n="expert.guide.summary"' in _expert_guide(markup)


def test_each_expert_guide_step_states_its_input_and_its_output() -> None:
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    chinese = _table("zh-CN")
    english = _table("en")
    keys = re.findall(r'data-i18n="(expert\.guide\.s\d+\.io)"', guide)
    assert keys, "the expert guide must describe at least one step"
    for key in keys:
        assert key in chinese, f"unknown guide key {key}"
        assert "输入：" in chinese[key] and "产出：" in chinese[key], (
            f"{key} must name both the input and the output"
        )
        assert "Input:" in english[key] and "Output:" in english[key], key


def test_the_expert_guide_explains_the_end_to_end_flow() -> None:
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    chinese = _table("zh-CN")
    english = _table("en")
    assert 'data-i18n="expert.guide.flow"' in guide
    # The reviewers\' own mental model ends at handing the work back.
    assert "提交" in chinese["expert.guide.flow"]
    assert "submit" in english["expert.guide.flow"].lower()


def test_the_expert_guide_locates_the_capture_control() -> None:
    """Capture was called out by name, so the guide must point at the button.

    The toolbar is the workspace's own, so it reads Chinese to a Chinese reader.
    The guide has to name the button in the same wording, or it points at a label
    that is not on screen.
    """
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    keys = re.findall(r'data-i18n="(expert\.guide\.s\d+\.io)"', guide)
    for language in ("zh-CN", "en"):
        table = _table(language)
        captures = [key for key in keys if table["capture.shot"] in table[key]]
        assert captures, f"a guide step must name the capture button as {language} draws it"
        # The crop dialog is where the evidence actually leaves the page.
        text = table[captures[0]]
        assert table["capture.download"] in text or table["capture.copy"] in text, language


def test_the_expert_guide_names_the_screenshot_sidecar() -> None:
    """Downloading a crop writes two files, so the guide must promise both."""
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    keys = re.findall(r'data-i18n="(expert\.guide\.s\d+\.io)"', guide)
    for language, view in (("zh-CN", "视角"), ("en", "view")):
        table = _table(language)
        captures = [key for key in keys if table["capture.download"] in table[key]]
        assert captures, f"a guide step must name the download control in {language}"
        text = table[captures[0]]
        assert "JSON" in text, "the capture step must name the sidecar file"
        assert view in text.lower(), "the sidecar records the view, so say so"


def test_the_expert_guide_separates_the_viewer_camera_buttons() -> None:
    """Reviewers asked what the last two Mol* camera buttons differ in."""
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    chinese = _table("zh-CN")
    english = _table("en")
    key = "expert.guide.cameraNote"
    assert f'data-i18n="{key}"' in guide, "the guide must explain the camera buttons"
    for label in ("Reset Zoom", "Orient Axes", "Reset Axes"):
        for wording in _frame_labels(label):
            assert wording in chinese[key], f"{key} must name {label} as the panel reads it"
        assert label in english[key], f"{key} must name {label}"
    # The distinction is what each one aligns to: the structure, or the axes.
    assert "主轴" in chinese[key] and "坐标" in chinese[key]
    assert "principal" in english[key].lower() and "cartesian" in english[key].lower()


def test_the_expert_guide_scopes_reasoning_to_key_operations() -> None:
    """Reasoning is per semantically key operation, not per mouse move."""
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    chinese = _table("zh-CN")
    english = _table("en")
    key = "expert.guide.reasoningNote"
    assert f'data-i18n="{key}"' in guide, "the guide must scope the reasoning field"
    assert "关键" in chinese[key]
    assert "every" in english[key].lower()


def test_the_expert_guide_points_at_the_next_task() -> None:
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    assert 'data-i18n="expert.guide.next"' in guide
    assert "expert.guide.next" in _table("zh-CN")


def test_the_expert_guide_explains_how_to_measure_a_distance() -> None:
    """A reviewer asked for a distance and found no option that shows one."""
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    chinese = _table("zh-CN")
    english = _table("en")
    key = "expert.guide.measureNote"
    assert f'data-i18n="{key}"' in guide, "the guide must show how to read a distance"
    # The path is three controls long, so naming only the last one is useless.
    for label in ("Toggle Selection Mode", "Measurements", "Distance"):
        for wording in _frame_labels(label):
            assert wording in chinese[key], f"{key} must name {label} as the panel reads it"
        assert label in english[key], f"{key} must name {label}"
    for table in (chinese, english):
        assert "Å" in table[key], f"{key} must name the unit the distance is read in"
    assert "距离" in chinese[key]
    assert "distance" in english[key].lower()


def test_the_expert_guide_explains_how_to_show_periodic_images() -> None:
    """One task asked for the nearest periodic-image contact of a structure
    whose file only carries its asymmetric unit: "镜像分子在画面里不存在".
    Mol* can generate the neighbours, so the guide must name the switch."""
    guide = _expert_guide(EXPERT_HTML_PATH.read_text(encoding="utf-8"))
    chinese = _table("zh-CN")
    english = _table("en")
    key = "expert.guide.symmetryNote"
    assert f'data-i18n="{key}"' in guide, "the guide must show how to build the images"
    for label in ("Symmetry Mates", "Unit Cell"):
        for wording in _frame_labels(label):
            assert wording in chinese[key], f"{key} must name {label} as the panel reads it"
        assert label in english[key], f"{key} must name {label}"
    # The switch only appears for files that carry crystal symmetry, and an
    # expert who does not know that will read its absence as a broken viewer.
    assert "对称" in chinese[key] and "镜像" in chinese[key]
    assert "symmetry" in english[key].lower()
