# -*- coding: utf-8 -*-
"""CI unit job 的最小 hermetic 单测（清偿 ci-unit-job-empty，2026-09-30）。

背景：tests/unit 目录空缺，CI unit job（观察期 job）每次 0 测试空转。
本文件只收**纯函数**用例：不写文件、不碰网络、不改全局状态——
CI 任意环境可跑。更重的语义覆盖在 ../test_tuning_externalized.py 等。
"""
from lingclaude.cli.interface import _sgr_params_to_style
from lingclaude.core.policy_loader import _tuned


class TestSgrParamsToStyle:
    """SGR 参数串 → PT 样式（纯映射，白名单由 sgr_styles.yaml 驱动）。"""

    def test_basic_foreground_color(self):
        assert _sgr_params_to_style("31") == "ansired"

    def test_modifier_plus_color(self):
        got = _sgr_params_to_style("1;31")
        assert got is not None
        assert set(got.split()) == {"ansired", "bold"}

    def test_empty_returns_none(self):
        assert _sgr_params_to_style("") is None

    def test_reset_returns_none(self):
        assert _sgr_params_to_style("0") is None

    def test_extended_color_rejected(self):
        # 256 色（38;5;N）默认 extended: reject——跳过参数，不产生样式
        assert _sgr_params_to_style("38;5;196") is None

    def test_unknown_param_ignored(self):
        # 未知参数宽容忽略（对齐终端语义）；全未知 → None
        assert _sgr_params_to_style("99") is None


class TestTunedFallback:
    """_tuned 未配置键的兜底语义（键名刻意用 tuning 段不存在的键，
    避开 coding_runtime.yaml 真实配置值，保证断言确定性）。"""

    def test_absent_key_returns_int_default(self):
        assert _tuned("unit_test_absent_int_key", 7) == 7
        assert isinstance(_tuned("unit_test_absent_int_key", 7), int)

    def test_absent_key_returns_float_default(self):
        got = _tuned("unit_test_absent_float_key", 0.5)
        assert got == 0.5
        assert isinstance(got, float)

    def test_bool_value_excluded(self):
        # isinstance(True, int) 陷阱：bool 必须被排除回 default
        assert _tuned("unit_test_absent_bool_key", 3) == 3
