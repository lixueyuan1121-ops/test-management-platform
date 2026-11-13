"""Evaluation publication shares the daily report theme and short-link policy."""
import tempfile
import unittest
from datetime import datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.eval_report import render_report_page
from app.core.config import settings
from app.db.session import Base
from app.models.ai_eval import EvalTask
from app.services import nami_deploy, task_digest_skill
from app.services.eval_pipeline import _summary_share_url
from scripts.test_task_daily_report_skill import install_test_skill


class Styles(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.styles, self.active = [], False
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        if tag == 'style':
            self.styles.append('')
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'style':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.styles[-1] += data


class EvaluationDailyStyleTests(unittest.TestCase):
    def task(self, **changes):
        fields = dict(name='报告 <安全>', summary_status='done',
                      summary_html='<h2>总体结论</h2><p>通过 9 条、不通过 1 条</p>',
                      detail_html='<section>冻结明细：10 条执行</section>',
                      last_batch_id='batch-1', summary_provider='test',
                      summary_at=datetime(2026, 11, 13, 8, 30, 45))
        return SimpleNamespace(**{**fields, **changes})

    def test_evaluation_uses_same_base_theme_as_rendered_daily_report(self):
        with tempfile.TemporaryDirectory() as root:
            skill, _, _ = install_test_skill(root)
            with patch.object(task_digest_skill, 'get_skill_dir', return_value=skill):
                daily = task_digest_skill.render_task_digest_html({'title':'测试日报', 'content':'日报正文'})
        page = render_report_page(self.task())
        self.assertEqual(Styles(page).styles[0].strip(), Styles(daily).styles[0].strip())
        self.assertIn('class="report-header"', page)
        self.assertIn('class="eyebrow"', page)
        for text in ('通过 9 条、不通过 1 条', '冻结明细：10 条执行', 'batch-1', '2026-11-13 08:30:45'):
            self.assertIn(text, page)
        self.assertIn('报告 &lt;安全&gt;', page)
        self.assertNotIn('报告 <安全>', page)

    def test_unfinished_report_does_not_expose_summary_or_details(self):
        page = render_report_page(self.task(summary_status='running'))
        self.assertIn('正在生成中', page)
        self.assertNotIn('通过 9 条', page)
        self.assertNotIn('冻结明细', page)

    def test_pipeline_requests_short_url_even_when_global_default_is_disabled(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            task = EvalTask(project_id=1, name='测试报告', query_ids='[]',
                            summary_status='done', summary_html='<p>测评结论</p>',
                            summary_share_code='0123456789abcdef')
            db.add(task); db.commit(); task_id = task.id
        # Only the network boundary is replaced: exercise the real publisher and renderer.
        def upload(html, *, prefer_shortlink=None):
            self.assertIn('测评结论', html)
            return 'https://report.zhaomi.cn/' if prefer_shortlink else 'https://ns.chat.360.cn/client-up/test/index.html'
        try:
            with patch.object(settings, 'NAMI_DEPLOY_ENABLED', True), \
                 patch.object(settings, 'NAMI_SHORTLINK_ENABLED', False), \
                 patch.object(settings, 'PLATFORM_BASE_URL', 'https://platform.test'), \
                 patch.object(nami_deploy, 'is_configured', return_value=True), \
                 patch.object(nami_deploy, 'deploy_html', side_effect=upload):
                self.assertEqual(_summary_share_url(sessions, task_id), 'https://report.zhaomi.cn/')
                self.assertFalse(settings.NAMI_SHORTLINK_ENABLED)
            with patch.object(settings, 'NAMI_DEPLOY_ENABLED', False), \
                 patch.object(settings, 'PLATFORM_BASE_URL', 'https://platform.test'):
                self.assertEqual(_summary_share_url(sessions, task_id), 'https://platform.test/r/0123456789abcdef')
        finally:
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
