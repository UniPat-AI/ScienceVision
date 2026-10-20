import pytest
from sci3d_agent.modes import get_profile
from sci3d_agent.policy import apply_render_policy

@pytest.mark.unit
def test_mode1_strips_images_and_flags():
    profile = get_profile("mode1_no_vision")
    raw_result = {"stdout": "ok", "images": [b"PNG..."], "exec_count": 1}
    cleaned, violations = apply_render_policy(raw_result, profile, code="plt.savefig('a.png')")
    assert cleaned["images"] == []
    assert any("plt.savefig" in v for v in violations)

@pytest.mark.unit
def test_mode1_leaves_text_only_results_untouched():
    profile = get_profile("mode1_no_vision")
    raw_result = {"stdout": "ok", "images": [], "exec_count": 1}
    cleaned, violations = apply_render_policy(raw_result, profile, code="x = 1")
    assert cleaned == raw_result
    assert violations == []

@pytest.mark.unit
def test_mode2_strips_kernel_images_and_flags():
    profile = get_profile("mode2_one_image")
    raw_result = {"stdout": "", "images": [b"PNG..."], "exec_count": 2}
    cleaned, violations = apply_render_policy(raw_result, profile, code="mesh.show()")
    assert cleaned["images"] == []
    assert any("mesh.show" in v or "image_returned" in v for v in violations)

@pytest.mark.unit
def test_mode3_passes_images_through():
    profile = get_profile("mode3_free_visual")
    raw_result = {"stdout": "", "images": [b"PNG..."], "exec_count": 3}
    cleaned, violations = apply_render_policy(raw_result, profile, code="plt.savefig('x.png')")
    assert cleaned["images"] == [b"PNG..."]
    assert violations == []

@pytest.mark.unit
def test_mode1_uses_image_returned_fallback_when_no_pattern_matches():
    profile = get_profile("mode1_no_vision")
    raw_result = {"stdout": "", "images": [b"PNG..."], "exec_count": 4}
    cleaned, violations = apply_render_policy(raw_result, profile, code="some_obscure_render_call(x)")
    assert cleaned["images"] == []
    assert violations == [f"{profile.name}_render_attempt:image_returned"]

@pytest.mark.unit
def test_signatures_deduped_when_multiple_patterns_match_same_call():
    profile = get_profile("mode1_no_vision")
    raw_result = {"stdout": "", "images": [b"PNG..."], "exec_count": 5}
    cleaned, violations = apply_render_policy(raw_result, profile, code="plt.savefig('a.png')")
    assert len(violations) == len(set(violations))
