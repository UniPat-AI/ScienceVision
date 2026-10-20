"""Tests for sciencevision_taskgen.llm.routing."""
from __future__ import annotations

import pytest

from sciencevision_taskgen.llm.client import ChatClientError
from sciencevision_taskgen.llm.routing import cross_model_for, load_credentials, resolve_model
import sciencevision_taskgen.llm.routing as routing_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg_with_pool(models: list[str], default: str = "default-model") -> dict:
    return {
        "llm": {
            "default_model": default,
            "model_pool": models,
            "model_by_v_class": {},
        }
    }


# ---------------------------------------------------------------------------
# resolve_model
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_force_model_overrides_everything(self) -> None:
        config = {
            "llm": {
                "force_model": "forced-model",
                "model_by_v_class": {"V1": "v1-model"},
                "default_model": "default-model",
            }
        }
        assert resolve_model("V1", config) == "forced-model"

    def test_force_model_overrides_default(self) -> None:
        config = {"llm": {"force_model": "forced", "default_model": "default"}}
        assert resolve_model("V3", config) == "forced"

    def test_v_class_map_lookup(self) -> None:
        config = {
            "llm": {
                "model_by_v_class": {f"V{i}": f"model-v{i}" for i in range(1, 7)},
                "default_model": "default",
            }
        }
        for v in range(1, 7):
            assert resolve_model(f"V{v}", config) == f"model-v{v}"

    def test_falls_through_to_default(self) -> None:
        config = {"llm": {"model_by_v_class": {}, "default_model": "catch-all"}}
        assert resolve_model("V5", config) == "catch-all"

    def test_missing_config_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="no model resolved"):
            resolve_model("V1", {})

    def test_empty_llm_section_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="no model resolved"):
            resolve_model("V2", {"llm": {}})

    def test_error_message_includes_v_class(self) -> None:
        with pytest.raises(ValueError, match="V4"):
            resolve_model("V4", {"llm": {}})


# ---------------------------------------------------------------------------
# cross_model_for
# ---------------------------------------------------------------------------


class TestCrossModelFor:
    def test_returns_different_model_from_pool(self) -> None:
        config = _cfg_with_pool(["model-a", "model-b"])
        result = cross_model_for("model-a", config)
        assert result != "model-a"
        assert result == "model-b"

    def test_challenger_cross_model_map_takes_priority(self) -> None:
        config = {
            "llm": {
                "model_pool": ["model-a", "model-b"],
                "challenger_cross_model_map": {"model-a": "explicit-cross"},
            }
        }
        assert cross_model_for("model-a", config) == "explicit-cross"

    def test_degenerate_single_model_pool_returns_same(self) -> None:
        config = _cfg_with_pool(["only-model"])
        result = cross_model_for("only-model", config)
        assert result == "only-model"  # graceful degradation

    def test_degenerate_empty_pool_returns_builder(self) -> None:
        config = _cfg_with_pool([])
        result = cross_model_for("some-model", config)
        assert result == "some-model"

    def test_skips_builder_picks_first_other(self) -> None:
        config = _cfg_with_pool(["A", "B", "C"])
        assert cross_model_for("A", config) == "B"
        assert cross_model_for("B", config) == "A"


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------


class TestLoadCredentials:
    def _clear_all_cred_envs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "MINDRACODE_API_BASE",
            "MINDRACODE_API_KEY",
            "MINDRA_BASE_URL",
            "MINDRA_API",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_mindracode_vars_primary_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setenv("MINDRACODE_API_BASE", "https://gw.example.com")
        monkeypatch.setenv("MINDRACODE_API_KEY", "key-abc")
        base_url, api_key = load_credentials()
        assert api_key == "key-abc"
        assert base_url == "https://gw.example.com/v1"

    def test_normalises_base_url_adds_v1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setenv("MINDRACODE_API_BASE", "https://gw.example.com/")
        monkeypatch.setenv("MINDRACODE_API_KEY", "k")
        base_url, _ = load_credentials()
        assert base_url == "https://gw.example.com/v1"

    def test_already_has_v1_not_doubled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setenv("MINDRACODE_API_BASE", "https://gw.example.com/v1")
        monkeypatch.setenv("MINDRACODE_API_KEY", "k")
        base_url, _ = load_credentials()
        assert base_url == "https://gw.example.com/v1"
        assert "/v1/v1" not in base_url

    def test_mindra_vars_secondary_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setenv("MINDRA_BASE_URL", "https://mindra.test")
        monkeypatch.setenv("MINDRA_API", "mindra-key")
        base_url, api_key = load_credentials()
        assert api_key == "mindra-key"
        assert base_url == "https://mindra.test/v1"

    def test_raises_when_no_env_and_no_env_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Patch the module's _REPO_ROOT_ENV to a nonexistent file."""
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setattr(routing_mod, "_REPO_ROOT_ENV", tmp_path / "no_such.env")
        with pytest.raises(ChatClientError, match="could not resolve"):
            load_credentials()

    def test_mindracode_takes_priority_over_mindra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setenv("MINDRACODE_API_BASE", "https://code.example.com")
        monkeypatch.setenv("MINDRACODE_API_KEY", "code-key")
        monkeypatch.setenv("MINDRA_BASE_URL", "https://plain.example.com")
        monkeypatch.setenv("MINDRA_API", "plain-key")
        _, api_key = load_credentials()
        assert api_key == "code-key"

    def test_load_credentials_falls_back_to_anthropic_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        self._clear_all_cred_envs(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.mindracode.com")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        # Block the .env-file fallback so it cannot shadow ANTHROPIC_* vars
        monkeypatch.setattr(routing_mod, "_REPO_ROOT_ENV", tmp_path / "no_such.env")
        base, key = load_credentials()
        assert base == "https://api.mindracode.com/v1"
        assert key == "anth-key"


# ---------------------------------------------------------------------------
# resolve_model — force_model validation
# ---------------------------------------------------------------------------


class TestForceModelValidation:
    def test_force_model_empty_string_raises(self) -> None:
        config = {"llm": {"force_model": ""}}
        with pytest.raises(ValueError, match="non-empty string"):
            resolve_model("V1", config)

    def test_force_model_whitespace_raises(self) -> None:
        config = {"llm": {"force_model": "   "}}
        with pytest.raises(ValueError, match="non-empty string"):
            resolve_model("V1", config)


# ---------------------------------------------------------------------------
# _load_env_file — inline comment stripping
# ---------------------------------------------------------------------------


class TestLoadEnvFile:
    def test_inline_comment_stripped_from_unquoted_value(self, tmp_path) -> None:
        from sciencevision_taskgen.llm.routing import _load_env_file

        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value # comment\nKEY2=\"value # not a comment\"\n")
        result = _load_env_file(env_file)
        assert result["KEY"] == "value"
        assert result["KEY2"] == "value # not a comment"
