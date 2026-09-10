"""P0.1 单元测试: config._resolve_api_key 解析链。

覆盖: 显式值直通 / ${VAR} 引用 / 环境变量回退 / 项目 .env 回退 /
全空返回空串（交还 factory key_store 链）。
"""
import os
from pathlib import Path

import pytest

from lingclaude.core import config as config_mod


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """隔离环境变量与 .env 路径。"""
    for name in ("ZHIPU_API_KEY", "GLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    fake_env = tmp_path / ".env"
    monkeypatch.setattr(config_mod, "_ENV_FILE", fake_env)
    return fake_env


class TestResolveApiKey:
    def test_explicit_value_passthrough(self):
        assert config_mod._resolve_api_key("sk-explicit") == "sk-explicit"

    def test_var_reference_reads_env(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "from-env-ref")
        assert config_mod._resolve_api_key("${ZHIPU_API_KEY}") == "from-env-ref"

    def test_var_reference_unset_returns_empty(self):
        assert config_mod._resolve_api_key("${NOPE_VAR}") == ""

    def test_empty_falls_to_env_var(self, monkeypatch, isolated):
        monkeypatch.setenv("GLM_API_KEY", "glm-env")
        assert config_mod._resolve_api_key("") == "glm-env"

    def test_empty_falls_to_project_env_file(self, isolated):
        isolated.write_text("ZHIPU_API_KEY=file-key\n", encoding="utf-8")
        assert config_mod._resolve_api_key("") == "file-key"

    def test_all_miss_returns_empty(self, isolated):
        assert config_mod._resolve_api_key("") == ""

    def test_env_overrides_file(self, monkeypatch, isolated):
        isolated.write_text("ZHIPU_API_KEY=file-key\n", encoding="utf-8")
        monkeypatch.setenv("ZHIPU_API_KEY", "env-wins")
        assert config_mod._resolve_api_key("") == "env-wins"

    def test_load_config_end_to_end(self, monkeypatch, isolated):
        """集成: config.yaml api_key 为空时经解析链拿到 .env 里的值。"""
        isolated.write_text("ZHIPU_API_KEY=e2e-key\n", encoding="utf-8")
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        # 直接调 from_dict，避免依赖真实 config.yaml 的内容
        cfg = config_mod.lingclaudeConfig.from_dict({"model": {"api_key": ""}})
        assert cfg.model.api_key == "e2e-key"
