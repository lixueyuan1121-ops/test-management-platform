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
        payload.update(product_name='不应传给AI的产品名',project_name='不应传给AI的项目名',filter_summary='不应传给AI的筛选条件')
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
        self.assertTrue(report['title'].startswith('不应传给AI的产品名 '))
        self.assertIn('不应传给AI的版本',report['content'])
        self.assertNotIn('不应传给AI的姓名',report['content'])
        self.assertNotIn('不应传给AI的说明',report['content'])
        html=service.render_task_digest_html(report)
        self.assertIn('不应传给AI的版本',html)
        self.assertNotIn('不应传给AI的姓名',html)
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
        self.assertIn('2. **PDF 支持编辑**\n   提供 PDF 内容编辑能力',report['content'])
        self.assertIn('1. **适配安全卫士 AI 版相关优化**',report['content'])
        self.assertLess(report['content'].index('适配安全卫士 AI 版相关优化'),report['content'].index('PDF 支持编辑'))
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
            self.assertIn(task['title'],content)
        self.assertNotIn('测试：',content)
        self.assertNotIn('平台甲',content)
        self.assertNotIn('飞书原说明',content)
        self.assertNotIn('https://example.test/share/abc',content)
        self.assertNotIn('版本：桌面版',content)
        self.assertNotIn('**测试中（',content)
        expected = [tasks[i]['title'] for i in [2,1,0,3,4,8,7,5,6,9,10]]
        self.assertEqual([line.split('. ',1)[1] for line in content.splitlines() if __import__('re').match(r'^\d+\. ',line)], expected)
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
            self.assertIn(task['title'],html)
        self.assertIn('手工标题 &lt;script&gt;', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)
        self.assertNotIn('<img', html)
        self.assertIn('人工补充', html)
        self.assertIn('href="https://example.test/?a=1&amp;b=2"', html)
        self.assertIn('</a>（第 3 行）', html)
        self.assertIn('data-report-type="daily"',html)
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
        self.assertEqual(upgraded['platform_adapter_version'], 10)
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
        self.assertIn('### 测试中',report['content'])
        self.assertNotIn('### 已上线',report['content'])
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

    def test_versions_choose_layout_and_sort_completed_statuses_first(self):
        tasks = [dict(id=1,title='版本九上线项',target_version='3.9.0',status='online'),
                 dict(id=2,title='版本十测试项',target_version='3.10.0',status='testing'),
                 dict(id=3,title='版本九完成项',target_version=' 3.9.0 ',status='ready_online'),
                 dict(id=4,title='版本十完成项',target_version='3.10.0',status='ready_online'),
                 dict(id=5,title='版本十上线项',target_version='3.10.0',status='online'),
                 dict(id=6,title='未规划项',target_version=None,status='ready_online'),
                 dict(id=7,title='版本十待修项',target_version='3.10.0',status='waiting_fix')]
        report=service.generate_task_digest(self.payload(tasks))
        self.assertEqual(report['title'],'版本更新与测试日报')
        content=report['content']
        self.assertLess(content.index('## 3.10.0 测试日报'),content.index('## 3.9.0 版本更新说明'))
        self.assertLess(content.index('## 3.9.0 版本更新说明'),content.index('## 未填写规划版本 测试日报'))
        self.assertLess(content.index('版本十完成项'),content.index('版本十上线项'))
        self.assertLess(content.index('版本十上线项'),content.index('版本十测试项'))
        self.assertLess(content.index('版本十测试项'),content.index('版本十待修项'))
        self.assertLess(content.index('版本九完成项'),content.index('版本九上线项'))
        self.assertIn('1. 版本九完成项',content)
        self.assertIn('2. 版本九上线项',content)
        html=service.render_task_digest_html(report)
        self.assertEqual(html.count('data-report-type="release"'),1)
        self.assertEqual(html.count('data-report-type="daily"'),2)
        self.assertNotIn('class="status-tag"',html)
        self.assertIn('测试完成 1 项 / 已上线 1 项', html)
        self.assertIn('<h5>测试中</h5>',html)
        for task in tasks: self.assertIn(task['title'],html)
        self.assertEqual(html.count('class="upd"'),len(tasks))

    def test_only_completed_or_online_versions_use_release_layout(self):
        statuses=['ready_online','online','testing','regressing','waiting_fix','blocked','at_risk',
                  'pending','unassigned','paused','closed','future_status',None]
        tasks=[dict(id=i,title=f'需求{i}',target_version=f'版本{i}',status=status) for i,status in enumerate(statuses,1)]
        tasks.append(dict(id=99,title='旧队列无状态需求',target_version='旧版本'))
        content=service.generate_task_digest(self.payload(tasks))['content']
        for i,status in enumerate(statuses,1):
            mode='版本更新说明' if status in ('ready_online','online') else '测试日报'
            self.assertIn(f'## 版本{i} {mode}',content)
        self.assertIn('## 旧版本 测试日报',content)
        self.assertIn('### 状态未记录',content)

    def test_single_release_and_daily_titles_and_html_remain_editable(self):
        tasks=[dict(id=1,title='编辑功能',target_version='3.27.0',status='ready_online')]
        report=service.generate_task_digest(self.payload(tasks))
        self.assertEqual(report['title'],'3.27.0 版本更新说明')
        html=service.render_task_digest_html(report)
        self.assertIn('RELEASE NOTES',html)
        self.assertIn('<h1>3.27.0 版本更新说明</h1>',html)
        self.assertNotIn('TEST DAILY',html)
        self.assertNotIn('状态：已上线',report['content'])
        tasks[0]['status']='testing'
        report=service.generate_task_digest(self.payload(tasks))
        self.assertEqual(report['title'],'3.27.0 测试日报')
        report['content']+='\n\n### 手工补充\n人工补充仍可编辑'
        html=service.render_task_digest_html(report)
        self.assertIn('TEST DAILY',html)
        self.assertIn('人工补充仍可编辑',html)
        self.assertIn('class="report-header mode-daily"',html)

    def test_product_prefix_and_custom_title_keep_one_header_for_single_version(self):
        for status, kind, label in [('ready_online', '版本更新说明', 'RELEASE NOTES'),
                                    ('testing', '测试日报', 'TEST DAILY')]:
            with self.subTest(status=status):
                tasks = [dict(id=1, title='功能甲', target_version='3.27.0', status=status)]
                report = service.generate_task_digest(dict(self.payload(tasks), product_name='  纳米Work PC端  '))
                expected = f'纳米Work PC端 3.27.0 {kind}'
                self.assertEqual(report['title'], expected)
                for title in (expected, f'纳米Work PC端 V3.27.0 {kind}', '人工标题 <说明>'):
                    report['title'] = title
                    html = service.render_task_digest_html(report)
                    from html import escape
                    self.assertEqual(html.count('<header '), 1)
                    self.assertEqual(html.count('<h1>'), 1)
                    self.assertIn(f'<h1>{escape(title)}</h1>', html)
                    self.assertIn(f'<footer>{escape(title)}</footer>', html)
                    self.assertIn(label, html)
                    self.assertIn('class="status-pill"', html)
                    self.assertIn('所选条目：1', html)
                    self.assertIn('功能甲', html)
                    self.assertNotIn('class="version-head"', html)

    def test_product_prefix_keeps_multiple_version_headers(self):
        tasks = [dict(id=1, title='功能甲', target_version='3.27.0', status='ready_online'),
                 dict(id=2, title='功能乙', target_version='3.28.0', status='testing')]
        report = service.generate_task_digest(dict(self.payload(tasks), product_name='另一产品'))
        self.assertEqual(report['title'], '另一产品 版本更新与测试日报')
        report['title'] = '另一产品 本期汇总'
        html = service.render_task_digest_html(report)
        self.assertEqual(html.count('<h1>'), 1)
        self.assertEqual(html.count('class="version-head"'), 2)
        self.assertIn('<h2>3.27.0 版本更新说明</h2>', html)
        self.assertIn('<h2>3.28.0 测试日报</h2>', html)

    def test_prompt_enrichment_preserves_subitems_platforms_issues_and_structure(self):
        tasks=[dict(id=1,title='三端产物编辑：a. 超限预览；b. 大于20MB仍打开本地编辑器；c. 选择云端或本地版本',status='ready_online',target_version='3.30.0'),
               dict(id=2,title='其他需求：微信转发，开发跟进中',status='pending',target_version='3.30.0'),
               dict(id=3,title='主要问题：msix签名流程阻塞',status='blocked',target_version='3.30.0')]
        result=dict(overview='产物编辑与其他需求。',items=[
            dict(id=1,headline='三端产物编辑优化',summary='完善产物编辑体验。',tags=['体验优化'],platforms=['三端'],bullets=['a. 超限预览','b. 大于20MB仍打开本地编辑器','c. 选择云端或本地版本'],section='updates'),
            dict(id=2,headline='微信转发',summary='开发跟进中。',tags=[],platforms=[],bullets=[],section='other'),
            dict(id=3,headline='msix 签名流程阻塞',summary='阻塞原因是 msix 签名流程。',tags=[],platforms=[],bullets=[],section='issues')])
        with patch('app.services.task_digest_summary.collect',return_value=result) as model:
            report=service.generate_task_digest(self.payload(tasks))
        review=json.loads(model.call_args.args[1].split('输入数据：\n',1)[1])['review_candidate']
        self.assertEqual(review['items'][0]['bullets'],result['items'][0]['bullets'])
        self.assertIn('不可缩成 Windows',model.call_args.args[1])
        html=service.render_task_digest_html(report)
        self.assertIn('涉及平台：三端',html)
        self.assertNotIn('Windows',html)
        self.assertIn('<ul class="subitems"><li>a. 超限预览</li>',html)
        self.assertLess(html.index('a. 超限预览'),html.index('b. 大于20MB'))
        self.assertLess(html.index('b. 大于20MB'),html.index('c. 选择云端'))
        self.assertIn('<h3>其他需求</h3>',html)
        self.assertIn('<h3>遗留问题 / 主要问题</h3>',html)
        self.assertIn('⚠',html)
        self.assertIn('msix 签名流程',html)
        self.assertIn('class="notes"',html)
        self.assertIn('class="status-pill"',html)
        self.assertIn('class="type-tag"',html)
        import runpy
        validate=runpy.run_path(str(self.skill/'scripts/fetch_daily_report.py'))['validate_html']
        self.assertEqual(validate(html),html)
        for invalid in ['<html><body></body><head><style></style></head></html>',
                        '<html><head><style>p{color:red}</head><body></body></html>',
                        '<html><head><style></style></head><body><div></body></html>']:
            with self.assertRaises(ValueError): validate(invalid)

    def test_quality_review_repairs_missing_descriptions_and_title_paraphrases(self):
        import copy
        tasks=[dict(id=1,title='优化打点',status='ready_online',target_version='3.27.0'),
               dict(id=2,title='修复安审漏洞',status='ready_online',target_version='3.27.0'),
               dict(id=3,title='称呼与记忆统一连通',status='ready_online',target_version='3.27.0')]
        good=dict(overview='本次聚焦数据采集、安全修复与个性化能力连通。',
            conclusion='通过完善数据采集、修复安全审核漏洞及连通称呼和记忆，为功能分析与连贯的个性化表达提供支持。',items=[
                dict(id=1,headline='优化打点',summary='优化埋点采集与上报，便于分析功能使用情况，为产品改进提供数据参考。',tags=['体验优化']),
                dict(id=2,headline='修复安审漏洞',summary='修复安全审核中发现的漏洞问题，完善相关安全防护，降低潜在安全风险。',tags=['安全修复']),
                dict(id=3,headline='称呼与记忆统一连通',summary='打通称呼与记忆之间的信息关联，让称呼在记忆相关场景中一致使用，保持表达连贯。',tags=['能力打通'])])
        bad=copy.deepcopy(good)
        bad['items'][0]['summary']=''
        bad['items'][1]['summary']='针对安审漏洞进行修复。'
        bad['items'][2]['tags']=[]
        with patch('app.services.task_digest_summary.collect',side_effect=[good,bad,good]) as model:
            report=service.generate_task_digest(self.payload(tasks))
        self.assertEqual(model.call_count,3)
        retry=model.call_args.args[1]
        self.assertIn('条目 1 缺少说明',retry)
        self.assertIn('条目 2 的说明只是标题同义复述',retry)
        self.assertIn('条目 3 缺少贴切',retry)
        self.assertIn(good['conclusion'],report['content'])
        self.assertNotIn('本期重点：优化打点；',report['content'])
        html=service.render_task_digest_html(report)
        self.assertIn('<ul class="release-notes"><li>',html)
        self.assertIn('安全修复',html)
        self.assertNotIn('class="status-tag"',html)
        with patch('app.services.task_digest_summary.collect',return_value=bad):
            with self.assertRaises(HTTPException) as caught:
                service.generate_task_digest(self.payload(tasks))
        self.assertIn('内容质量',caught.exception.detail)

    def test_version_composition_receives_only_its_titles_and_reviewed_prose(self):
        from app.services import task_digest_summary as summaries
        tasks=[dict(id=i,title=f'功能{i}',status='testing',target_version='私密版本甲' if i<3 else '私密版本乙',
                    owners=['不得传入的人名']) for i in range(1,5)]
        with patch.object(summaries,'collect',wraps=summaries.collect) as model:
            report=service.generate_task_digest(self.payload(tasks))
        self.assertEqual(model.call_count,4)
        for call in model.call_args_list:
            prompt=call.args[1]
            self.assertNotIn('私密版本',prompt)
            self.assertNotIn('不得传入的人名',prompt)
        compositions=[json.loads(call.args[1].split('输入数据：\n',1)[1]) for call in model.call_args_list[2:]]
        self.assertEqual([[t['id'] for t in part['tasks']] for part in compositions],[[1,2],[3,4]])
        for part in compositions:
            ids={t['id'] for t in part['tasks']}
            self.assertEqual(ids,{i['id'] for i in part['batch_summaries'][0]['items']})
        self.assertIn('版本概览：',report['content'])
        self.assertIn('私密版本甲',report['content'])

    def test_multiversion_overview_has_exact_selected_counts_and_safe_navigation(self):
        from html.parser import HTMLParser
        tasks = [dict(id=1, title='完成项', target_version='3.27.0', status='ready_online'),
                 dict(id=2, title='上线项', target_version='3.27.0', status='online'),
                 dict(id=3, title='测试项', target_version='3.28.0', status='testing'),
                 dict(id=4, title='阻塞项', target_version='3.28.0', status='blocked'),
                 dict(id=5, title='未规划项', target_version=None, status='pending'),
                 dict(id=6, title='安全标题项', target_version='<script>alert(1)</script>', status=None)]
        report = service.generate_task_digest(dict(self.payload(tasks), product_name='纳米Work PC端'))
        html = service.render_task_digest_html(report)
        self.assertIn('本次筛选共 6 项 · 4 个归属组', html)
        self.assertIn('不代表整个版本的测试或发布结论', html)
        self.assertIn('测试完成 1 项 / 已上线 1 项', html)
        self.assertIn('测试中 1 项 / 阻塞 1 项', html)
        self.assertIn('状态未记录 1 项', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(html.count('class="upd"'), 6)
        self.assertNotIn('### 发布说明', report['content'])
        self.assertNotIn('### 日报小结', report['content'])
        self.assertNotIn('本次更新已上线', report['content'])
        self.assertNotIn('class="status-tag"', html)
        class Links(HTMLParser):
            def __init__(self):
                super().__init__(); self.ids=[]; self.links=[]
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if 'id' in attrs: self.ids.append(attrs['id'])
                if tag=='a' and attrs.get('href','').startswith('#'): self.links.append(attrs['href'][1:])
        parsed=Links(); parsed.feed(html)
        self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
        self.assertEqual(len(parsed.links), 8)  # One entry and return link per version.
        self.assertTrue(all(link in parsed.ids for link in parsed.links))
        self.assertLess(html.index('id="version-overview"'), html.index('id="version-1"'))
        self.assertLess(report['content'].index('3.28.0 测试日报'), report['content'].index('3.27.0 版本更新说明'))

    def test_edited_and_legacy_reports_preserve_notes_without_inventing_state_counts(self):
        report=dict(title='手工改名',content='\n'.join([
            '**功能概览**', '人工概览', '## 3.27.0 版本更新说明',
            '状态：测试完成 / 已上线', '条目数：99', '### 更新内容', '1. 原功能甲', '2. 原功能乙',
            '### 发布说明', '- 人工补充：仍需等待发布通知。',
            '## 3.28.0 测试日报', '状态：测试中 / 阻塞', '状态分布：测试中 8 项 / 阻塞 2 项',
            '条目数：10', '### 测试进展', '#### 测试中', '1. 原功能丙',
            '### 日报小结', '- 人工补充：msix 签名流程阻塞。']))
        html=service.render_task_digest_html(report)
        self.assertIn('本次筛选共 3 项 · 2 个归属组', html)
        self.assertIn('未记录分项数量', html)
        self.assertIn('分项数量待核对', html)
        self.assertNotIn('测试中 8 项', html)
        self.assertNotIn('所选条目：99', html)
        self.assertIn('人工补充：仍需等待发布通知', html)
        self.assertIn('人工补充：msix 签名流程阻塞', html)

    def test_multiple_versions_keep_followups_without_repeating_feature_descriptions(self):
        tasks=[dict(id=1,title='功能甲',status='testing',target_version='3.28.0'),
               dict(id=2,title='主要问题：msix签名流程阻塞',status='blocked',target_version='3.27.0')]
        summary=dict(overview='本次包括功能调整与签名问题。',items=[
            dict(id=1,headline='功能甲',summary='明确功能说明。',section='updates'),
            dict(id=2,headline='msix 签名流程阻塞',summary='签名问题仍需跟进。',section='issues')])
        with patch('app.services.task_digest_summary.collect',return_value=summary):
            report=service.generate_task_digest(self.payload(tasks))
        self.assertEqual(report['content'].count('明确功能说明。'),1)
        self.assertEqual(report['content'].count('签名问题仍需跟进。'),1)
        self.assertIn('本组另含 1 项其他需求或问题，详见对应小节',report['content'])
        self.assertNotIn('版本概览：', report['content'])  # Single-item groups need no duplicate intro.
        self.assertIn('⚠',service.render_task_digest_html(report))

    def test_html_partitions_attribution_without_modifying_saved_report(self):
        from copy import deepcopy
        from html import escape
        from html.parser import HTMLParser
        labels = ['待定', '未填写规划版本', 'v3.28.0-beta.1', '前端', '前端+接口',
                  '桌面版', '3.27.0', '<script>归属</script>']
        report = dict(title='纳米Work PC端 人工标题', content='\n'.join(
            f'## {label} 测试日报\n状态：测试中\n状态分布：测试中 1 项\n'
            f'### 测试进展\n#### 测试中\n{i}. 功能{i}\n   原有说明{i}，不可丢失。'
            for i, label in enumerate(labels, 1)))
        original = deepcopy(report)
        html = service.render_task_digest_html(report)
        self.assertEqual(report, original)

        class Articles(HTMLParser):
            def __init__(self):
                super().__init__(); self.articles = []; self.ids = []; self.links = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs: self.ids.append(attrs['id'])
                if tag == 'article': self.articles.append((attrs['id'], attrs['data-attribution']))
                if tag == 'a' and attrs.get('href', '').startswith('#'): self.links.append(attrs['href'][1:])
        parsed = Articles(); parsed.feed(html)
        self.assertEqual(parsed.articles, [('version-3', 'version'), ('version-7', 'version'),
            ('version-1', 'other'), ('version-4', 'other'), ('version-5', 'other'),
            ('version-6', 'other'), ('version-8', 'other'), ('version-2', 'unset')])
        self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
        self.assertTrue(all(link in parsed.ids for link in parsed.links))
        self.assertLess(html.index('id="region-version"'), html.index('id="region-other"'))
        self.assertLess(html.index('id="region-other"'), html.index('id="region-unset"'))
        self.assertEqual(html.count('<h1>'), 1)
        self.assertEqual(html.count('class="upd"'), len(labels))
        for i, label in enumerate(labels, 1):
            self.assertIn(escape(label), html)
            self.assertIn(f'原有说明{i}，不可丢失。', html)
            self.assertIn(f'<span class="no">{i}</span>', html)
        self.assertIn('<h3>前端+接口</h3>', html)
        self.assertNotIn('前端+接口 测试日报</h3>', html)
        self.assertNotIn('<script>', html)

    def test_single_other_or_unset_group_preserves_one_page_header(self):
        for label, region in [('前端', 'other'), ('未填写规划版本', 'unset')]:
            with self.subTest(label=label):
                report = dict(title='人工标题', content=f'## {label} 测试日报\n状态：测试中\n### 测试进展\n1. 功能甲')
                html = service.render_task_digest_html(report)
                self.assertIn(f'id="region-{region}"', html)
                self.assertIn('原归属：' + label, html)
                self.assertEqual(html.count('<header '), 1)
                self.assertNotIn('class="version-head"', html)
                self.assertNotIn('返回总览 ↑</a>', html)
                self.assertIn('<h3>测试进展</h3>', html)

    def test_only_other_attributions_get_their_own_region(self):
        report = dict(title='纳米Work PC端 需求汇总', content=(
            '## 前端 版本更新说明\n状态：已上线\n### 更新内容\n1. 首页优化\n'
            '## 桌面版 测试日报\n状态：测试中\n### 测试进展\n1. 文档划词'))
        html = service.render_task_digest_html(report)
        self.assertIn('id="region-other"', html)
        self.assertNotIn('id="region-version"', html)
        self.assertNotIn('id="region-unset"', html)
        self.assertIn('<h3>前端</h3>', html)
        self.assertIn('<h3>桌面版</h3>', html)
        self.assertNotIn('前端 版本更新说明</h3>', html)

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
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['tasks'],[{'id':1,'title':'待测试功能','status':'testing','target_version':None}])
        self.assertIn('summary',json.loads(run.call_args.kwargs['input']))
        self.assertEqual(run.call_args_list[0].args[0][-1], '--summary-prompt-json')


if __name__ == '__main__':
    unittest.main()
