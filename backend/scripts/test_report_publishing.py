import unittest
from unittest.mock import patch
from app.services import report_publishing as service


class ReportPublishingTests(unittest.TestCase):
    def test_reuses_existing_nami_service_and_url(self):
        with patch.object(service.settings, 'NAMI_DEPLOY_ENABLED', True), patch.object(service.nami_deploy, 'is_configured', return_value=True), patch.object(service.nami_deploy, 'deploy_html', return_value='https://ns.chat.360.cn/example/index.html') as deploy:
            result = service.publish_html('<html>摘要</html>', '/d/existing')
            deploy.assert_called_once_with('<html>摘要</html>')
            self.assertEqual(result, {'url':'https://ns.chat.360.cn/example/index.html','hosting':'nami'})

    def test_existing_fallback_is_preserved(self):
        with patch.object(service.settings, 'NAMI_DEPLOY_ENABLED', True), patch.object(service.nami_deploy, 'is_configured', return_value=True), patch.object(service.nami_deploy, 'deploy_html', side_effect=RuntimeError('unavailable')):
            self.assertEqual(service.publish_html('html', 'https://platform/r/eval')['url'], 'https://platform/r/eval')
            self.assertEqual(service.publish_html('html', '/d/digest')['url'], '/d/digest')

    def test_digest_can_request_short_link_without_changing_global_settings(self):
        with patch.object(service.settings,'NAMI_DEPLOY_ENABLED',True), patch.object(service.settings,'NAMI_SHORTLINK_ENABLED',False), patch.object(service.nami_deploy,'is_configured',return_value=True), patch.object(service.nami_deploy,'deploy_html',return_value='https://report.zhaomi.cn/') as deploy:
            result=service.publish_html('html','/d/test',prefer_shortlink=True)
            deploy.assert_called_once_with('html',prefer_shortlink=True)
            self.assertEqual(result['url'],'https://report.zhaomi.cn/')
            self.assertFalse(service.settings.NAMI_SHORTLINK_ENABLED)

    def test_short_link_override_uses_existing_uploader_and_safe_fallback(self):
        nd=service.nami_deploy
        with patch.object(nd.settings,'NAMI_SHORTLINK_ENABLED',False), patch.object(nd,'_read_cookie',return_value='test'), patch.object(nd,'_upload',return_value='https://ns.chat.360.cn/client-up/abc') as upload, patch.object(nd,'_read_vm_id',return_value='test-vm'), patch.object(nd,'_get_short',return_value='https://report.zhaomi.cn/') as shorten:
            self.assertEqual(nd.deploy_html('html',prefer_shortlink=True),'https://report.zhaomi.cn/')
            upload.assert_called_once_with('html','test')
            shorten.assert_called_once()
            self.assertFalse(nd.settings.NAMI_SHORTLINK_ENABLED)
            shorten.side_effect=nd.NamiDeployError('unavailable')
            self.assertEqual(nd.deploy_html('html',prefer_shortlink=True),'https://ns.chat.360.cn/client-up/abc/index.html')
