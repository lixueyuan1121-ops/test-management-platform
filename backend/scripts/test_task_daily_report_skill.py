"""Private skill integration tests; set TASK_DAILY_REPORT_SKILL_ARCHIVE for the real package."""
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app.services import task_digest_skill as service
from scripts.install_task_daily_report_skill import install


def install_test_skill(root):
    archive = os.environ.get('TASK_DAILY_REPORT_SKILL_ARCHIVE')
    if not archive:
        archive = Path(root) / 'fixture.zip'
        # A minimal upstream contract for CI; actual-package verification uses the env path.
        upstream = '''import json, re, sys
def _split_item(item):
    return item['text'], item.get('goodcase', [])
def generate_report(date_str, version, web_items, upgrade_items):
    return 'original release report'
def main():
    raise RuntimeError('The message-fetching entry point must not be called')
if __name__ == "__main__":
    main()
'''
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('tuitui-daily-report-skill-latest/SKILL.md', '# Test skill')
            z.writestr('tuitui-daily-report-skill-latest/references/template.html', '<style>body { background: #f5f5f7; }</style>')
            z.writestr('tuitui-daily-report-skill-latest/scripts/fetch_daily_report.py', upstream)
    destination = Path(root) / 'installed'
    manifest = install(archive, destination)
    return destination, archive, manifest


class DailyReportSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.skill, self.archive, self.manifest = install_test_skill(self.temp.name)
        self.path_patch = patch.object(service, 'get_skill_dir', return_value=self.skill)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def payload(self, tasks=None):
        return {'project_name': '验证项目', 'report_date': '2026-10-16', 'filter_summary': '多选筛选',
                'tasks': tasks if tasks is not None else [dict(id=1, title='待测试功能', status='testing', owners=['平台姓名'])]}

    def test_real_entry_keeps_all_statuses_and_preserves_goodcases(self):
        statuses = ['online','ready_online','testing','regressing','waiting_fix','pending',
                    'unassigned','at_risk','blocked','paused','closed']
        tasks = [dict(id=i, title=f'任务-{status}', status=status, owners=['平台甲','平台乙'])
                 for i,status in enumerate(statuses,1)]
        tasks[2].update(title='说明写着已上线但实际测试中', owners=[], target_version='桌面版',
            progress_note='验证进度\ngoodcase：\n【案例】https://example.test/share/abc?x=1&y=2',
            description='飞书原说明：一尘跟进验收', next_action='继续回归', risk_level='red', affects_release=True)
        report = service.generate_task_digest(self.payload(tasks))
        content = report['content']
        self.assertEqual(report['generator']['archive_sha256'],self.manifest['archive_sha256'])
        self.assertIn('共 11 个功能',content)
        for task in tasks:
            self.assertEqual(content.count(task['title']),1)
        self.assertIn('测试负责人：平台甲、平台乙',content)
        self.assertIn('测试负责人：未指定',content)
        self.assertIn('一尘跟进验收',content)
        self.assertIn('goodcase：【案例】https://example.test/share/abc?x=1&y=2',content)
        self.assertIn('归属版本：桌面版',content)
        testing_section = content.split('**测试中（1 项）**')[1].split('**回归中')[0]
        self.assertIn(tasks[2]['title'],testing_section)
        self.assertNotIn('goodcase：无',content)

    def test_install_is_idempotent_and_keeps_original(self):
        self.assertEqual(install(self.archive,self.skill),self.manifest)
        import hashlib
        original=(self.skill/'scripts/fetch_daily_report.upstream.py').read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),self.manifest['upstream_script_sha256'])
        with self.assertRaises(ValueError):
            install(self.archive,Path(self.temp.name)/'wrong',expected_sha256='wrong')

    def test_html_uses_template_and_preserves_all_groups_edits_and_safe_text(self):
        tasks = [dict(id=i, title='任务-' + status, status=status, owners=['平台甲'])
                 for i, status in enumerate(['online','ready_online','testing','regressing','waiting_fix',
                     'pending','unassigned','at_risk','blocked','paused','closed'])]
        report = service.generate_task_digest(self.payload(tasks))
        report.update(report_date='2026-10-16', title='手工标题 <script>')
        report['content'] += '\n\n**补充说明**\n<img src=x onerror=alert(1)>\n人工补充 https://example.test/?a=1&b=2（第 3 行）'
        html = service.render_task_digest_html(report)
        import re
        style = re.search(r'<style[^>]*>(.*?)</style>', (self.skill/'references/template.html').read_text(), re.S|re.I).group(1)
        self.assertIn(style, html)
        for task in tasks:
            self.assertEqual(html.count(task['title']), 1)
        self.assertIn('手工标题 &lt;script&gt;', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)
        self.assertNotIn('<img', html)
        self.assertIn('人工补充', html)
        self.assertIn('href="https://example.test/?a=1&amp;b=2"', html)
        self.assertIn('</a>（第 3 行）', html)
        self.assertIn('<h2>测试中</h2>', html)
        self.assertIn('<h2>已关闭</h2>', html)

    def test_template_integrity_and_upgrade_keeps_backup(self):
        manifest_path = self.skill/'platform-install.json'
        old = dict(self.manifest, adapter_sha256='previous-version', platform_adapter_version=1)
        manifest_path.write_text(json.dumps(old))
        with self.assertRaises(ValueError): install(self.archive, self.skill)
        upgraded = install(self.archive, self.skill, upgrade=True)
        self.assertEqual(upgraded['platform_adapter_version'], 2)
        backups = list(self.skill.parent.glob('installed.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads((backups[0]/'platform-install.json').read_text()), old)
        (self.skill/'references/template.html').write_text('modified')
        with self.assertRaises(HTTPException) as caught: service.generate_task_digest(self.payload())
        self.assertEqual(caught.exception.status_code, 503)

    def test_archive_path_traversal_is_rejected(self):
        archive=Path(self.temp.name)/'invalid.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('tuitui-daily-report-skill-latest/../../escape.py','bad')
        with self.assertRaises(ValueError):
            install(archive,Path(self.temp.name)/'invalid')
        self.assertFalse((Path(self.temp.name)/'escape.py').exists())

    def test_missing_or_modified_skill_has_no_fallback(self):
        (self.skill/'scripts/fetch_daily_report.py').write_text('modified')
        with self.assertRaises(HTTPException) as caught:
            service.generate_task_digest(self.payload())
        self.assertEqual(caught.exception.status_code,503)

    def test_timeout_failure_and_output_size(self):
        with patch.object(service.subprocess,'run',side_effect=subprocess.TimeoutExpired('skill',10)):
            with self.assertRaises(HTTPException) as caught: service.generate_task_digest(self.payload())
            self.assertEqual(caught.exception.status_code,504)
        with patch.object(service.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','failure')):
            with self.assertRaises(HTTPException) as caught: service.generate_task_digest(self.payload())
            self.assertEqual(caught.exception.status_code,502)
        with patch.object(service.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'title':'标题','content':'x'*60001}),'')):
            with self.assertRaises(HTTPException) as caught: service.generate_task_digest(self.payload())
            self.assertEqual(caught.exception.status_code,422)

    def test_child_process_receives_only_task_json_and_safe_environment(self):
        real_run = subprocess.run
        with patch.object(service.subprocess,'run',wraps=real_run) as run:
            service.generate_task_digest(self.payload())
        command = run.call_args.args[0]
        self.assertEqual(command[-1],'--tasks-json')
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertEqual(set(run.call_args.kwargs['env']),{'PATH','PYTHONIOENCODING','PYTHONUTF8'})
        self.assertEqual(json.loads(run.call_args.kwargs['input']),self.payload())


if __name__ == '__main__':
    unittest.main()
