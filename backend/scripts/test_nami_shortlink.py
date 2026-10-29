"""Nami 短链响应校验与已上传报告重试；全部网络调用使用 mock。"""
import unittest
from unittest.mock import Mock, patch

from app.services import nami_deploy as service


class NamiShortlinkTests(unittest.TestCase):
    def test_short_link_must_be_https_zhaomi_subdomain_homepage(self):
        for url in ('https://report.zhaomi.cn/', 'https://report.zhaomi.cn'):
            self.assertTrue(service.is_short_url(url))
        for url in (None, '', 'http://report.zhaomi.cn/', 'https://zhaomi.cn/',
                    'https://report.zhaomi.cn/index.html', 'https://report.zhaomi.cn/?x=1',
                    'https://report.zhaomi.cn/#x', 'https://report.zhaomi.cn.evil.test/',
                    'https://evil.test@report.zhaomi.cn/', 'https://report.zhaomi.cn:443/',
                    'https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html'):
            self.assertFalse(service.is_short_url(url), url)

    def test_retry_sends_original_directory_and_keeps_credentials_out_of_url(self):
        with patch.object(service, '_read_vm_id', return_value='test-vm'), patch.object(service, '_read_cookie', return_value='test-cookie'), patch.object(service, '_get_short', return_value='https://report.zhaomi.cn/') as shorten, patch.object(service, '_upload') as upload:
            result = service.shorten_uploaded_url('https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html')
            self.assertEqual(result, 'https://report.zhaomi.cn/')
            shorten.assert_called_once_with('test-vm', 'https://ns.chat.360.cn/zhaomi-so/client-up/abc', 'test-cookie')
            upload.assert_not_called()

    def test_retry_rejects_other_hosts_and_non_report_paths(self):
        with patch.object(service, '_read_cookie') as cookie, patch.object(service, '_get_short') as shorten:
            for url in ('https://external.test/report', 'http://127.0.0.1/',
                        'https://ns.chat.360.cn/other/index.html',
                        'https://ns.chat.360.cn/zhaomi-so/client-up/../index.html',
                        'https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html?token=x'):
                with self.assertRaises(service.NamiDeployError):
                    service.shorten_uploaded_url(url)
            cookie.assert_not_called()
            shorten.assert_not_called()

    def test_short_url_reuses_without_cookie_and_missing_vm_stays_retryable(self):
        with patch.object(service, '_read_cookie') as cookie, patch.object(service, '_read_vm_id', return_value=''):
            self.assertEqual(service.shorten_uploaded_url('https://report.zhaomi.cn/'), 'https://report.zhaomi.cn/')
            with self.assertRaises(service.NamiDeployError):
                service.shorten_uploaded_url('https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html')
            cookie.assert_not_called()

    def test_api_long_url_and_expired_login_are_not_shortlink_success(self):
        response = Mock(status_code=200)
        with patch.object(service.requests, 'post', return_value=response):
            for data in ({'code': 110005, 'msg': 'Unauthorized request'},
                         {'code': 0, 'data': {'shor_url': 'https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html'}},
                         {'code': 0, 'data': {'shor_url': 'javascript:alert(1)'}}):
                response.json.return_value = data
                with self.assertRaises(service.NamiDeployError):
                    service._get_short('test-vm', 'https://ns.chat.360.cn/zhaomi-so/client-up/abc', 'test-cookie')
            response.json.return_value = {'code': 0, 'data': {'shor_url': 'https://report.zhaomi.cn/'}}
            self.assertEqual(service._get_short('test-vm', 'base', 'test-cookie'), 'https://report.zhaomi.cn/')


if __name__ == '__main__':
    unittest.main()
