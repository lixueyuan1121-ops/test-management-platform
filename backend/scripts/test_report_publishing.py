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
