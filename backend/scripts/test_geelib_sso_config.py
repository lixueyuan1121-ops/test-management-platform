"""真实文件系统上的 SSO 配置发现和授权密钥持久化回归。"""
import json
import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services import geelib_sso_config as config


class SsoConfigTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.client_file = self.directory / 'client.json'
        self.client_file.write_text('{}')
        self.key_file = self.directory / 'secrets/geelib.key'
        for name, value in {
            'GEELIB_SSO_CONFIG_FILE': str(self.client_file), 'GEELIB_SSO_URL': '',
            'GEELIB_OAUTH_CLIENT_ID': '', 'GEELIB_OAUTH_CLIENT_SECRET': '',
            'GEELIB_TOKEN_ENCRYPTION_KEY': '', 'GEELIB_TOKEN_KEY_FILE': str(self.key_file),
        }.items():
            p = patch.object(settings, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'SSO_URL': '', 'SSO_CLIENT_ID': '', 'SSO_CLIENT_SECRET': ''})
        p.start(); self.addCleanup(p.stop)

    def test_defaults_and_configuration_precedence(self):
        self.assertEqual(config.client_config()['client_id'], config.DEFAULT_CLIENT_ID)
        self.client_file.write_text(json.dumps({'sso_url': 'https://configured.example', 'client_id': 'file-client', 'client_secret': 'private-secret', 'access_token': 'MUST-NOT-READ'}))
        self.assertEqual(config.client_config()['client_id'], 'file-client')
        self.assertNotIn('access_token', config.client_config())
        with patch.dict(os.environ, {'SSO_CLIENT_ID': 'env-client'}):
            self.assertEqual(config.client_config()['client_id'], 'env-client')
            with patch.object(settings, 'GEELIB_OAUTH_CLIENT_ID', 'explicit-client'):
                self.assertEqual(config.client_config()['client_id'], 'explicit-client')

    def test_key_survives_new_cipher_and_concurrent_first_access(self):
        def encrypt(_):
            return config.credential_cipher().encrypt(b'personal-credential')
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(encrypt, range(16)))
        for value in values:
            self.assertEqual(config.credential_cipher().decrypt(value), b'personal-credential')
        self.assertEqual(list(self.key_file.parent.iterdir()), [self.key_file])
        if os.name != 'nt':
            self.assertEqual(self.key_file.stat().st_mode & 0o777, 0o600)

    def test_corrupted_key_is_never_overwritten(self):
        self.key_file.parent.mkdir()
        self.key_file.write_text('corrupted-key')
        with self.assertRaisesRegex(ValueError, '损坏'):
            config.credential_cipher()
        self.assertEqual(self.key_file.read_text(), 'corrupted-key')

    def test_explicit_key_failure_is_not_silently_replaced(self):
        with patch.object(settings, 'GEELIB_TOKEN_ENCRYPTION_KEY', 'invalid'):
            with self.assertRaisesRegex(ValueError, '无效'):
                config.credential_cipher()
        self.assertFalse(self.key_file.exists())

    def test_missing_or_invalid_client_file_is_actionable(self):
        self.client_file.unlink()
        with self.assertRaisesRegex(ValueError, '不存在'):
            config.client_config()
        self.client_file.write_text('invalid-json')
        with self.assertRaisesRegex(ValueError, 'JSON'):
            config.client_config()

    def test_invalid_sso_url_is_rejected(self):
        for url in ['http://example.com', 'https:', 'https://user:secret@example.com']:
            with patch.object(settings, 'GEELIB_SSO_URL', url):
                with self.assertRaisesRegex(ValueError, 'HTTPS'):
                    config.client_config()


if __name__ == '__main__':
    unittest.main()
