"""SDT-lc-002 v2.1 4 类风险分级测试"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path("/home/ai/lingclaude/.lingclaude/scripts/sdt_lc_002_v2.py")
SPEC = importlib.util.spec_from_file_location("sdt_lc_002_v2", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class TestRiskClassification:
    def test_classify_port_availability(self) -> None:
        assert mod.classify_port(8765) == 'availability'
        assert mod.classify_port(8785) == 'availability'

    def test_classify_port_identity(self) -> None:
        assert mod.classify_port(9528) == 'identity'
        assert mod.classify_port(9530) == 'identity'

    def test_classify_port_credential(self) -> None:
        assert mod.classify_port(8100) == 'credential'
        assert mod.classify_port(13459) == 'credential'

    def test_classify_port_authorization(self) -> None:
        assert mod.classify_port(8767) == 'authorization'
        assert mod.classify_port(8768) == 'authorization'

    def test_unknown_port_defaults_availability(self) -> None:
        assert mod.classify_port(7890) == 'availability'  # clash 默认 availability


class TestFailClosedAction:
    def test_availability_soft(self) -> None:
        assert mod.fail_closed_action('availability') == 'alert'

    def test_identity_block(self) -> None:
        assert mod.fail_closed_action('identity') == 'block'

    def test_credential_double_sign(self) -> None:
        assert mod.fail_closed_action('credential') == 'double_sign'

    def test_authorization_block(self) -> None:
        assert mod.fail_closed_action('authorization') == 'block'

    def test_unknown_defaults_alert(self) -> None:
        assert mod.fail_closed_action('unknown_class') == 'alert'


class TestRiskClassMapping:
    def test_service_risk_class_count(self) -> None:
        # 确保每类至少 1 个
        classes = set(mod.SERVICE_RISK_CLASS.values())
        assert classes == {'availability', 'identity', 'credential', 'authorization'}


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])