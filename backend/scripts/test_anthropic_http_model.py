"""anthropic_http provider 的模型名解析回归。

线上故障：网关返回 400 code 1001「model参数对应模型不存在（模型名严格区分大小写）」，
根因是 _model() 硬编码 'claude-opus-4-8'，不像 _base_url()/_auth_token() 那样在
未显式配置时回落到 cc-switch settings.json 的 ANTHROPIC_DEFAULT_OPUS_MODEL
（网关实际认的 'claude-opus-4-8[1M]'）。本测试锁定回落行为。
"""
import unittest
from unittest.mock import patch

from app.services.generators import anthropic_http_runner as runner


class ModelResolutionTests(unittest.TestCase):
    def test_explicit_setting_wins(self):
        """显式配置 ANTHROPIC_HTTP_MODEL 时，直接用它，不读 cc-switch。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", "my/explicit-model"), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value={
                 "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8[1M]"}):
            self.assertEqual(runner._model(), "my/explicit-model")

    def test_falls_back_to_cc_switch_opus_model(self):
        """未显式配置时，回落到 cc-switch 的 ANTHROPIC_DEFAULT_OPUS_MODEL（网关认的名字）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value={
                 "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8[1M]"}):
            self.assertEqual(runner._model(), "claude-opus-4-8[1M]")

    def test_env_var_overrides_cc_switch(self):
        """进程环境变量 ANTHROPIC_MODEL 优先于 cc-switch（与 base_url/token 的分层一致）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {"ANTHROPIC_MODEL": "env/model"}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value={
                 "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8[1M]"}):
            self.assertEqual(runner._model(), "env/model")

    def test_last_resort_when_nothing_configured(self):
        """全都取不到时，回落到一个不带 [1M] 的裸名（尽力而为，避免 None）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value={}):
            self.assertEqual(runner._model(), "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main()
