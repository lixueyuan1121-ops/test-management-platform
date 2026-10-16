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


class SummaryEngine:
    """Network-free model double; the real skill still prepares, validates and renders."""
    def is_available(self): return True
    def stream_generate(self, requirement, **kwargs):
        prompt = kwargs['prompt_builder']()
        payload, _ = json.JSONDecoder().raw_decode(prompt.split('输入数据：\n', 1)[1])
        items = []
        for task in payload['tasks']:
            items.append({'id': task['id'], 'summary': task['title']})
        result = {'overview': '本次涵盖多项功能需求。', 'items': items}
        yield {'type':'result', 'text':json.dumps(result, ensure_ascii=False), 'complete':True}


class DailyReportSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.skill, self.archive, self.manifest = install_test_skill(self.temp.name)
        self.path_patch = patch.object(service, 'get_skill_dir', return_value=self.skill)
        self.path_patch.start()
        self.engine_patch = patch('app.services.task_digest_summary.generators.get_provider', return_value=SummaryEngine())
        self.engine_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.engine_patch.stop()
        self.temp.cleanup()

    def payload(self, tasks=None):
        return {'project_name': '验证项目', 'report_date': '2026-10-16', 'filter_summary': '多选筛选',
                'tasks': tasks if tasks is not None else [dict(id=1, title='待测试功能', status='testing', owners=['平台姓名'])]}

    def test_final_report_uses_fact_review_result(self):
        import copy
        first = dict(overview='第一稿',risks=[],next_steps=[],items=[dict(id=1,headline='PDF 支持编辑',summary='PDF 编辑功能已上线')])
        reviewed = copy.deepcopy(first)
        reviewed['items'][0]['summary'] = '提供 PDF 内容编辑能力，便于对文档进行调整与完善。'
        with patch('app.services.task_digest_summary.collect', side_effect=[first, reviewed]) as model:
            report = service.generate_task_digest(self.payload([dict(id=1,title='pdf支持编辑',status='testing')]))
            self.assertEqual(model.call_count, 2)
            self.assertIn('事实审校', model.call_args.args[1])
            self.assertIn('便于对文档进行调整与完善', report['content'])
            self.assertNotIn('已上线', report['content'])

    def test_all_model_stages_receive_titles_only_even_for_legacy_payloads(self):
        from app.services import task_digest_summary as summaries
        tasks = [dict(id=i,title=f'功能需求{i}',status='testing',owners=['不应传给AI的姓名'],
                 target_version='不应传给AI的版本',priority='p1',description='不应传给AI的说明',
                 progress_note='不应传给AI的进度',daily_feedback='不应传给AI的日报反馈',
                 next_action='不应传给AI的下一步',risk_level='不应传给AI的风险',
                 requirement_url='不应传给AI的链接',developer='不应传给AI的开发') for i in range(1,33)]
        payload = self.payload(tasks)
        payload.update(project_name='不应传给AI的项目名',filter_summary='不应传给AI的筛选条件')
        with patch.object(summaries, 'collect', wraps=summaries.collect) as model:
            report = service.generate_task_digest(payload)
        self.assertEqual(model.call_count, 5)  # two summary/review batches and final overview
        for call in model.call_args_list:
            prompt = call.args[1]
            self.assertNotIn('不应传给AI的', prompt)
            prepared = json.loads(prompt.split('输入数据：\n',1)[1])
            self.assertTrue(all(set(task) == {'id','title'} for task in prepared['tasks']))
            for derived in ([prepared['review_candidate']] if 'review_candidate' in prepared else prepared.get('batch_summaries', [])):
                self.assertTrue(all(set(item) == {'id','summary'} for item in derived['items']))
        self.assertNotIn('不应传给AI的',json.dumps(report,ensure_ascii=False))
        html=service.render_task_digest_html(report)
        self.assertNotIn('不应传给AI的',html)
        self.assertNotIn('class="stats"',html)
        # The skill itself enforces the same boundary, even if called directly.
        prepared, _ = service._invoke_skill(payload, '--summary-prompt-json')
        self.assertNotIn('不应传给AI的', prepared['prompt'])

    def test_polished_headlines_details_and_sparse_items_render_safely(self):
        tasks = [dict(id=1,title='pdf支持编辑',status='testing'),
                 dict(id=2,title='适配安全卫士AI版相关优化版',status='ready_online')]
        result = dict(overview='本次涵盖文档编辑与产品适配需求。',risks=[],next_steps=[],items=[
            dict(id=1,headline='PDF 支持编辑',summary='提供 PDF 内容编辑能力，便于调整与完善文档。'),
            dict(id=2,headline='适配安全卫士 AI 版相关优化',summary='')])
        with patch('app.services.task_digest_summary.collect',return_value=result) as model:
            report=service.generate_task_digest(self.payload(tasks))
        self.assertIn('1. **PDF 支持编辑**\n   提供 PDF 内容编辑能力',report['content'])
        self.assertTrue(report['content'].endswith('2. **适配安全卫士 AI 版相关优化**'))
        prompt=model.call_args.args[1]
        self.assertIn('不只是复制标题',prompt)
        self.assertIn('事实审校同时保留自然、清晰的润色',prompt)
        reviewed=json.loads(prompt.split('输入数据：\n',1)[1])['review_candidate']
        self.assertEqual(reviewed['items'][0]['headline'],'PDF 支持编辑')
        report['report_date']='2026-10-16'
        report['content']+='\n\n**手工备注**\n1. **<script>alert(1)</script>**'
        html=service.render_task_digest_html(report)
        self.assertIn('<strong>PDF 支持编辑</strong>',html)
        self.assertIn('<p class="desc">提供 PDF 内容编辑能力',html)
        self.assertIn('<strong>适配安全卫士 AI 版相关优化</strong>',html)
        self.assertNotIn('测试：',html)
        self.assertIn('<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong>',html)
        self.assertNotIn('<script>',html)

    def test_real_entry_keeps_all_features_without_other_columns(self):
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
        self.assertNotIn('共 11 个任务',content)
        for task in tasks:
            self.assertEqual(content.count(task['title']),1)
        self.assertNotIn('测试：',content)
        self.assertNotIn('平台甲',content)
        self.assertNotIn('飞书原说明',content)
        self.assertNotIn('https://example.test/share/abc',content)
        self.assertNotIn('版本：桌面版',content)
        self.assertNotIn('**测试中（',content)
        self.assertEqual([line.split('. ',1)[1] for line in content.splitlines() if __import__('re').match(r'^\d+\. ',line)], [t['title'] for t in tasks])
        self.assertNotIn('goodcase：无',content)

    def test_install_is_idempotent_and_keeps_original(self):
        self.assertEqual(install(self.archive,self.skill),self.manifest)
        import hashlib
        original=(self.skill/'scripts/fetch_daily_report.upstream.py').read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(),self.manifest['upstream_script_sha256'])
        with self.assertRaises(ValueError):
            install(self.archive,Path(self.temp.name)/'wrong',expected_sha256='wrong')

    def test_html_uses_reference_style_and_preserves_features_edits_and_safe_text(self):
        tasks = [dict(id=i, title='任务-' + status, status=status, owners=['平台甲'])
                 for i, status in enumerate(['online','ready_online','testing','regressing','waiting_fix',
                     'pending','unassigned','at_risk','blocked','paused','closed'])]
        report = service.generate_task_digest(self.payload(tasks))
        report.update(report_date='2026-10-16', title='手工标题 <script>')
        report['content'] += '\n\n**补充说明**\n<img src=x onerror=alert(1)>\n人工补充 https://example.test/?a=1&b=2（第 3 行）'
        html = service.render_task_digest_html(report)
        style = (self.skill/'references/platform.css').read_text()
        self.assertIn(style, html)
        for task in tasks:
            self.assertEqual(html.count(task['title']), 1)
        self.assertIn('手工标题 &lt;script&gt;', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)
        self.assertNotIn('<img', html)
        self.assertIn('人工补充', html)
        self.assertIn('href="https://example.test/?a=1&amp;b=2"', html)
        self.assertIn('</a>（第 3 行）', html)
        self.assertIn('class="report-header"',html)
        self.assertIn('linear-gradient(135deg',html)
        self.assertIn('class="no"',html)
        self.assertNotIn('class="stats"',html)
        self.assertNotIn('2026-10-16',html)

    def test_template_integrity_and_upgrade_keeps_backup(self):
        manifest_path = self.skill/'platform-install.json'
        old = dict(self.manifest, adapter_sha256='previous-version', platform_adapter_version=1)
        manifest_path.write_text(json.dumps(old))
        with self.assertRaises(ValueError): install(self.archive, self.skill)
        upgraded = install(self.archive, self.skill, upgrade=True)
        self.assertEqual(upgraded['platform_adapter_version'], 5)
        backups = list(self.skill.parent.glob('installed.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads((backups[0]/'platform-install.json').read_text()), old)
        (self.skill/'references/template.html').write_text('modified')
        with self.assertRaises(HTTPException) as caught: service.generate_task_digest(self.payload())
        self.assertEqual(caught.exception.status_code, 503)

    def test_semantic_output_is_required_and_coverage_errors_fail(self):
        from app.services import task_digest_summary as summaries
        payload = self.payload()
        valid = {'overview':'完成核心场景验证，持续推进联调。', 'risks':[], 'next_steps':[],
                 'items':[{'id':1,'status':'testing','summary':'核心场景已验证，正在联调异常链路。'}]}
        with patch.object(summaries, 'collect', return_value=valid) as model:
            report = service.generate_task_digest(payload)
            self.assertTrue(report['generator']['summarized'])
            self.assertIn('核心场景已验证，正在联调异常链路。',report['content'])
            self.assertNotIn('风险等级：',report['content'])
            self.assertIn('提炼', model.call_args.args[1])
        for invalid in [dict(valid, items=[]),
                        dict(valid, items=[dict(id=999,summary='不存在任务')])]:
            with patch.object(summaries, 'collect', return_value=invalid):
                with self.assertRaises(HTTPException): service.generate_task_digest(payload)

    def test_model_extra_metadata_does_not_enter_review_or_output(self):
        model_result = dict(overview='功能概览',risks=[{'task_ids':[1],'summary':'额外风险'}],
            next_steps=[{'task_ids':[1],'summary':'额外安排'}],items=[dict(id=1,status='online',summary='功能摘要',owners=['额外人员'])])
        with patch('app.services.task_digest_summary.collect',return_value=model_result) as model:
            report = service.generate_task_digest(self.payload())
        self.assertNotIn('测试中',report['content'])
        self.assertNotIn('已上线',report['content'])
        for word in ('额外风险','额外安排','额外人员'):
            self.assertNotIn(word,report['content'])
            self.assertNotIn(word,model.call_args.args[1])
        validated,_=service._invoke_skill({**self.payload(), 'summary': model_result}, '--validate-summary-json')
        self.assertEqual(validated['summary'],dict(overview='功能概览',items=[dict(id=1,summary='功能摘要')]))

    def test_style_integrity_is_checked_and_upgrade_restores_it(self):
        (self.skill/'references/platform.css').write_text('tampered')
        with self.assertRaises(HTTPException) as caught:
            service.render_task_digest_html(dict(title='标题',content='正文'))
        self.assertEqual(caught.exception.status_code,503)
        with self.assertRaises(ValueError): install(self.archive,self.skill)
        install(self.archive,self.skill,upgrade=True)
        self.assertIn('linear-gradient',service.render_task_digest_html(dict(title='标题',content='正文')))

    def test_legacy_saved_report_content_remains_renderable(self):
        report=dict(title='历史测试日报',report_date='2026-10-15',content='**测试中（1 项）**\n1. 老任务（测试：甲；版本：3.26.0）')
        html=service.render_task_digest_html(report)
        self.assertIn('测试中（1 项）',html)
        self.assertIn('老任务（测试：甲；版本：3.26.0）',html)

    def test_multiple_batches_are_summarized_without_losing_tasks(self):
        tasks = [dict(id=i,title=f'条目{i}',status='testing',owners=['平台甲']) for i in range(1,33)]
        report = service.generate_task_digest(self.payload(tasks))
        self.assertNotIn('共 32 个任务',report['content'])
        self.assertNotIn('**测试中（32 项）**',report['content'])
        self.assertEqual(len(__import__('re').findall(r'^\d+\. ',report['content'],__import__('re').M)),32)

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
        with patch('app.services.task_digest_summary.summarize', return_value={'items':[]}), patch.object(service.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'title':'标题','content':'x'*60001}),'')):
            with self.assertRaises(HTTPException) as caught: service.generate_task_digest(self.payload())
            self.assertEqual(caught.exception.status_code,422)

    def test_child_process_receives_only_task_json_and_safe_environment(self):
        real_run = subprocess.run
        with patch.object(service.subprocess,'run',wraps=real_run) as run:
            service.generate_task_digest(self.payload())
        command = run.call_args.args[0]
        self.assertEqual(command[-1],'--tasks-json')
        self.assertNotIn('shell',run.call_args.kwargs)
        expected_env = {'PATH','PYTHONIOENCODING','PYTHONUTF8'}
        if os.name == 'nt' and os.environ.get('SystemRoot'):
            expected_env.add('SystemRoot')
            self.assertEqual(run.call_args.kwargs['env']['SystemRoot'], os.environ['SystemRoot'])
        self.assertEqual(set(run.call_args.kwargs['env']), expected_env)
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['tasks'],[{'id':1,'title':'待测试功能'}])
        self.assertIn('summary',json.loads(run.call_args.kwargs['input']))
        self.assertEqual(run.call_args_list[0].args[0][-1], '--summary-prompt-json')


if __name__ == '__main__':
    unittest.main()
