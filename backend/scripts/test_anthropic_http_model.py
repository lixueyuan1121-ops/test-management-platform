"""anthropic_http provider 的模型名解析回归。

线上故障：360 网关（https://api.360.cn，后端直连、无 cc-switch 本地代理翻译）返回
400 code 1001「model参数对应模型不存在（模型名严格区分大小写）」。

curl 实测（线上网关）结论：
  anthropic/claude-opus-4.8       -> HTTP 200  ✅ （cc-switch 的 *_MODEL_NAME 值）
  anthropic/claude-opus-4.8[1M]   -> HTTP 400  ❌ （cc-switch 的 *_MODEL 值，带 [1M] 内部标记）
  claude-opus-4.8                 -> HTTP 400  ❌

根因：[1M] 是 Claude Code 给本地代理用的“百万上下文”内部标记，360 原始网关不认。
_model() 必须取干净真名（*_MODEL_NAME 优先），并剥掉任何 [...] 后缀兜底。
"""
import unittest
from unittest.mock import patch

from app.services.generators import anthropic_http_runner as runner

# 线上 ~/.claude/settings.json 的 env 实况（截取模型相关键）
LIVE_ENV = {
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "anthropic/claude-opus-4.8[1M]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "anthropic/claude-opus-4.8",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "anthropic/claude-opus-4.8[1M]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "anthropic/claude-opus-4.8",
}


class ModelResolutionTests(unittest.TestCase):
    def test_explicit_setting_wins(self):
        """显式配置 ANTHROPIC_HTTP_MODEL 时，原样使用（运维已知网关认的名字）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", "anthropic/claude-opus-4.8"), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value=LIVE_ENV):
            self.assertEqual(runner._model(), "anthropic/claude-opus-4.8")

    def test_live_env_resolves_to_gateway_accepted_name(self):
        """线上实况：未显式配置时，输出网关实测 200 的干净真名（不带 [1M]）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value=LIVE_ENV):
            self.assertEqual(runner._model(), "anthropic/claude-opus-4.8")

    def test_strips_1m_suffix_when_only_suffixed_value_present(self):
        """只有带 [1M] 的键时，也要剥掉后缀（网关严格区分大小写、不认 [1M]）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value={
                 "ANTHROPIC_DEFAULT_OPUS_MODEL": "anthropic/claude-opus-4.8[1M]"}):
            self.assertEqual(runner._model(), "anthropic/claude-opus-4.8")

    def test_env_var_overrides_cc_switch(self):
        """进程环境变量 ANTHROPIC_MODEL 优先于 cc-switch（与 base_url/token 分层一致），并剥后缀。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {"ANTHROPIC_MODEL": "some/model[1M]"}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value=LIVE_ENV):
            self.assertEqual(runner._model(), "some/model")

    def test_last_resort_when_nothing_configured(self):
        """全都取不到时，回落一个裸名兜底（避免 None）。"""
        with patch.object(runner.settings, "ANTHROPIC_HTTP_MODEL", ""), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(runner, "_cc_switch_env", return_value={}):
            self.assertEqual(runner._model(), "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main()
