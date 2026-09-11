"""Model failures must not masquerade as testcase JSON parsing failures."""
import json
import unittest
from unittest.mock import Mock, patch

from app.services.claude_runner import _parse_line, _claude_env
from app.core.config import settings
from app.services.generators.sharded import generate_sharded
from app.api.ai import _gen_once


class GenerationErrorTests(unittest.TestCase):
    def test_proxy_failure_even_without_error_flag(self):
        result = _parse_line(json.dumps({"type": "result", "result": "API Error: Unable to connect to API (UnsupportedProxyProtocol)"}))
        self.assertTrue(result["is_error"])
        self.assertIn("CLAUDE_PROXY_URL", result["error"])

    def test_unsupported_proxy_fails_without_exposing_secret(self):
        with patch.dict("os.environ", {"https_proxy": "socks5://user:secret@localhost:1080"}, clear=True), patch.object(settings, "CLAUDE_PROXY_URL", ""):
            with self.assertRaises(ValueError) as exc:
                _claude_env()
            self.assertIn("https_proxy", str(exc.exception))
            self.assertNotIn("secret", str(exc.exception))

    def test_override_is_scoped_and_preserves_no_proxy(self):
        with patch.dict("os.environ", {"all_proxy": "socks5://localhost:1080", "NO_PROXY": "localhost"}, clear=True), patch.object(settings, "CLAUDE_PROXY_URL", "http://proxy.example:8080"):
            env = _claude_env()
            self.assertNotIn("all_proxy", env)
            self.assertEqual(env["HTTPS_PROXY"], "http://proxy.example:8080")
            self.assertEqual(env["NO_PROXY"], "localhost")
            import os
            self.assertEqual(os.environ["all_proxy"], "socks5://localhost:1080")

    def test_existing_http_proxy_and_direct_environment_preserved(self):
        for original in ({}, {"HTTPS_PROXY": "http://proxy.example:8080"}):
            with patch.dict("os.environ", original, clear=True), patch.object(settings, "CLAUDE_PROXY_URL", ""):
                self.assertEqual(_claude_env(), original)

    def test_cli_error_preserves_errors_and_subtype(self):
        result = _parse_line(json.dumps({"type": "result", "subtype": "error_during_execution",
                                         "errors": ["upstream unavailable"]}))
        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"], "upstream unavailable")

    def test_cli_success_unchanged(self):
        result = _parse_line(json.dumps({"type": "result", "subtype": "success", "result": "[]"}))
        self.assertFalse(result["is_error"])
        self.assertEqual(result["text"], "[]")

    def test_failed_result_is_not_parsed(self):
        engine = Mock()
        engine.stream_generate.return_value = iter([
            {"type": "result", "is_error": True, "text": "upstream unavailable"}])
        result = generate_sharded(engine, "req", shards=[{"id": "flow", "name": "flow"}])
        engine.parse_testcases.assert_not_called()
        self.assertEqual(result["cases"], [])
        self.assertIn("upstream unavailable", result["errors"][0])
        self.assertIn("upstream unavailable", result["raw"])

    def test_single_generation_preserves_failure(self):
        engine = Mock()
        engine.stream_generate.return_value = iter([
            {"type": "result", "is_error": True, "text": "denied"}])
        raw, meta, err = _gen_once(engine, "req", None, None)
        self.assertEqual(err, "denied")
        self.assertEqual(raw, "denied")

    def test_parser_exception_is_contained_in_shard(self):
        engine = Mock()
        engine.stream_generate.return_value = iter([{"type": "result", "text": "broken"}])
        engine.parse_testcases.side_effect = ValueError("bad format")
        result = generate_sharded(engine, "req", shards=[{"id": "flow", "name": "flow"}])
        self.assertEqual(result["cases"], [])
        self.assertIn("bad format", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
