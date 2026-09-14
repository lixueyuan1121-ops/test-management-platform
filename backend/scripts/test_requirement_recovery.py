"""Requirement output/recovery regression. Fixtures contain synthetic requirements only."""
import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models import AiJob, RequirementAnalysis, RequirementAnalysisPart, RequirementSource, TestMission
from app.services import ai_jobs, requirement_analysis as review, claude_runner
from app.services.ai_progress import JobProgress
from app.services.requirement_output import OutputError, parse_complete_object
from app.services.requirement_pipeline import Parts, build_draft, exact_reference
from app.services.generators import deepseek_runner
from scripts import test_requirement_analysis as fixtures


class FormatTests(unittest.TestCase):
    def test_batch_capacity_cannot_overflow_final_draft_or_drop_criteria(self):
        for count in (100, 499, 500):
            with self.subTest(criteria=count):
                interpretation = fixtures.draft()
                template = interpretation['rules'][0]
                interpretation.update(rules=[], questions=[], scenarios=[])
                for offset in range(0, count, 16):
                    rule = copy.deepcopy(template)
                    rule.update(id=f'R{offset}', criteria=[{'id':f'C{i}', 'text':f'合成条件 {i}'}
                        for i in range(offset, min(offset + 16, count))])
                    interpretation['rules'].append(rule)
                class CapacityParts:
                    progress = Mock()
                    def run(self, key, title, engine, prompt, schema, validate, **kwargs):
                        if key == 'analysis':
                            return validate(interpretation)
                        data = json.loads(prompt.split('\n')[-1])
                        by_id = {c['id']: r['id'] for r in data['rules'] for c in r['criteria']}
                        limit = schema['properties']['scenarios']['maxItems']
                        ids = data['assigned_criteria']
                        scenes = [{'id':f'S{i}', 'rule_id':by_id[ids[i % len(ids)]],
                            'criterion_ids':[ids[i % len(ids)]], 'when':'合成操作', 'then':'观察合成条件'}
                            for i in range(limit)]
                        # A non-native provider must obey the same batch limit.
                        with self_test.assertRaises(OutputError):
                            validate({'scenarios':scenes + [scenes[0]]})
                        return validate({'scenarios':scenes})
                self_test = self
                result = build_draft(CapacityParts(), None, '保护目录删除必须确认', [], {})
                self.assertLessEqual(len(result.scenarios), 500)
                self.assertEqual({cid for s in result.scenarios for cid in s.criterion_ids}, {f'C{i}' for i in range(count)})

    def test_reference_formatting_only_when_unambiguous(self):
        self.assertEqual(exact_reference('R1-C1', {'R1C1', 'R1C2'}), 'R1C1')
        self.assertEqual(exact_reference('r1_c1', {'R1C1'}), 'R1C1')
        self.assertEqual(exact_reference('R1-C9', {'R1C1'}), 'R1-C9')
        self.assertEqual(exact_reference('r1_c1', {'R1C1', 'R1-C1'}), 'r1_c1')
        self.assertEqual(exact_reference('R1-C1', {'R1C1', 'R1-C1'}), 'R1-C1')

    def test_complete_envelopes_preserve_all_values(self):
        original = {'summary': '正文含逗号,} 与引号"以及\n换行', 'rules': [{'id': 'R1'}]}
        text = json.dumps(original, ensure_ascii=False)
        for raw in (text, '\ufeff'+text, '以下为结果：\n```json\n'+text+'\n```\n分析结束。',
                    json.dumps(text), text+'\n'+text):
            self.assertEqual(parse_complete_object(raw), original)
        self.assertEqual(parse_complete_object('{"a":"原样,}","b":[1,2,],}'), {'a':'原样,}', 'b':[1,2]})
        self.assertEqual(parse_complete_object('{"a":"行一\n行二"}'), {'a':'行一\n行二'})

    def test_ambiguous_incomplete_and_duplicate_fields_never_salvaged(self):
        for raw in ('{"rules":[{"id":"R1"}', '{"a":1,"a":2}', '{"a":1} {"b":2}',
                    '{"a":"未结束', '{"a":NaN}', '[{"a":1}]', '结果：[{"a":1}]'):
            with self.subTest(raw=raw), self.assertRaises(OutputError):
                parse_complete_object(raw)

    def test_transport_completion_and_token_limit_are_distinct_from_syntax(self):
        class Engine:
            def __init__(self, events): self.events=events
            def stream_generate(self,*a,**k): yield from self.events
        for event, code in (({'type':'result','text':'{"rules":[]}','finish_reason':'length'}, 'truncated'),
                            ({'type':'result','text':'{"rules":[]}','complete':False}, 'interrupted'),
                            ({'type':'delta','text':'{"rules":[]}'}, 'interrupted')):
            with self.assertRaises(OutputError) as err:
                review.collect(Engine([event]), 'synthetic')
            self.assertEqual(err.exception.code,code)
            self.assertEqual(err.exception.raw,'{"rules":[]}')

    def test_native_structured_payload_and_safe_partial_output(self):
        obj={'summary':'合成结构化返回','rules':[]}
        result=claude_runner._parse_line(json.dumps({'type':'result','result':'说明文字','structured_output':obj}))
        self.assertEqual(json.loads(result['text']),obj)
        state={'structured':True}
        def event(data): return claude_runner._parse_line(json.dumps({'type':'stream_event','event':data}),state)
        event({'type':'message_start','message':{'id':'a'}})
        event({'type':'content_block_start','index':0,'content_block':{'type':'tool_use','name':'Read'}})
        self.assertIsNone(event({'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'private tool input'}}))
        event({'type':'content_block_start','index':1,'content_block':{'type':'tool_use','name':'StructuredOutput'}})
        self.assertEqual(event({'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'{"summary":'}}),
                         {'type':'delta','text':'{"summary":','reset':True})
        self.assertEqual(deepseek_runner._body('JSON',True,output_schema={'type':'object'})['response_format'], {'type':'json_object'})
        self.assertNotIn('response_format',deepseek_runner._body('ordinary',True))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=create_engine('sqlite:///'+str(Path(self.tmp.name)/'test.db'),connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        with self.factory() as db:
            db.add(AiJob(id=1,kind='requirement_analysis',provider='claude',status='running',input='{"analysis_id":1}'))
            db.add(RequirementAnalysis(id=1,project_id=1,task_id=1,created_by=1,source_text='保护目录删除必须确认',source_hash='synthetic',provider='claude',job_id=1))
            db.commit()
        self.progress=JobProgress(self.factory,1)
        self.parts=Parts(self.factory,1,1,'claude',self.progress)

    def tearDown(self):
        self.engine.dispose(); self.tmp.cleanup()

    def test_malformed_then_valid_bounded_retry_keeps_both_outputs(self):
        class Engine:
            calls=0
            def stream_generate(self,*a,**k):
                self.calls+=1
                yield {'type':'result','text':'{"ok":tru}' if self.calls==1 else 'Result:\n```json\n{"ok":true,}\n```'}
        engine=Engine()
        result=self.parts.run('probe','合成阶段',engine,'synthetic',{'type':'object'},lambda x:x)
        self.assertEqual(result,{'ok':True}); self.assertEqual(engine.calls,2)
        with self.factory() as db:
            rows=db.query(RequirementAnalysisPart).order_by(RequirementAnalysisPart.id).all()
            self.assertEqual([r.status for r in rows],['failed','done'])
            self.assertEqual(rows[0].raw,'{"ok":tru}')
        self.assertEqual(self.parts.run('probe','合成阶段',engine,'synthetic',{'type':'object'},lambda x:x),result)
        self.assertEqual(engine.calls,2)

    def test_complete_saved_output_recovers_without_model_call(self):
        class Engine:
            def stream_generate(self,*a,**k): yield {'type':'result','text':'{"ok":true}'}
        self.parts.run('probe','阶段',Engine(),'same',{},lambda x:x)
        with self.factory() as db:
            row=db.query(RequirementAnalysisPart).one(); row.status='received'; row.value=None; db.commit()
        class Offline:
            def stream_generate(self,*a,**k): raise AssertionError('must recover locally')
        self.assertEqual(self.parts.run('probe','阶段',Offline(),'same',{},lambda x:x),{'ok':True})

    def test_failed_scene_batch_resume_preserves_rules_and_other_batches(self):
        interpretation=fixtures.draft(); interpretation['scenarios']=[]
        interpretation['rules'][0]['criteria']=[{'id':f'R1-C{i}','text':f'合成验收条件 {i}'} for i in range(1,14)]
        counts=Counter()
        class Engine:
            fail=True
            def stream_generate(inner,*a,**kw):
                prompt=kw['prompt_builder']()
                if not prompt.startswith('[需求具体场景]'):
                    counts['rules']+=1; obj=interpretation
                else:
                    before=prompt.split('\n响应结构(JSON Schema)：')[0]
                    data=json.loads(before.split('\n')[-1])
                    ids=data['assigned_criteria']; key=ids[0]; counts[key]+=1
                    if inner.fail and 'R1-C7' in ids:
                        yield {'type':'result','text':'{"scenarios":['}; return
                    obj={'scenarios':[{'id':f'S{i}','rule_id':'R1','criterion_ids':[cid], 'actor':'测试者',
                                      'given':'已有记录','when':'点击','then':f'观察条件 {cid}','kind':'normal','reviewed':True} for i,cid in enumerate(ids)]}
                yield {'type':'result','text':json.dumps(obj,ensure_ascii=False)}
        engine=Engine()
        with self.assertRaises(OutputError):
            build_draft(self.parts,engine,'保护目录删除必须确认',[],{})
        with self.factory() as db:
            self.assertIsNone(db.get(RequirementAnalysis,1).draft)
            self.assertTrue(db.query(RequirementAnalysisPart).filter_by(part_key='analysis',status='done').count())
        first=counts.copy(); engine.fail=False
        draft=build_draft(self.parts,engine,'保护目录删除必须确认',[],{})
        self.assertEqual(counts['rules'],1)
        self.assertEqual(counts['R1-C1'],first['R1-C1'])
        self.assertEqual(counts['R1-C13'],first['R1-C13'])
        self.assertEqual(sum(counts.values())-sum(first.values()),1)
        self.assertEqual({cid for s in draft.scenarios for cid in s.criterion_ids}, {f'R1-C{i}' for i in range(1,14)})
        self.assertTrue(all(not s.reviewed for s in draft.scenarios))
        self.assertTrue(all(r.status=='pending' for r in draft.rules))

    def test_changed_input_does_not_reuse_checkpoint(self):
        class Engine:
            calls=0
            def stream_generate(inner,*a,**k):
                inner.calls+=1; yield {'type':'result','text':'{"ok":true}'}
        engine=Engine()
        for prompt in ('old source','new source'):
            self.parts.run('probe','阶段',engine,prompt,{},lambda x:x)
        self.assertEqual(engine.calls,2)

    def test_real_model_style_reference_mismatch_preserves_content(self):
        interpretation=fixtures.draft(); interpretation['scenarios']=[]
        for criterion in interpretation['rules'][0]['criteria']:
            criterion['id']=criterion['id'].replace('-', '')
        original=fixtures.draft()['scenarios'][0]
        class Engine:
            calls=0
            def stream_generate(inner,*a,**kw):
                inner.calls+=1
                obj={'scenarios':[original]} if kw['prompt_builder']().startswith('[需求具体场景]') else interpretation
                yield {'type':'result','text':json.dumps(obj)}
        engine=Engine()
        result=build_draft(self.parts,engine,'保护目录删除必须确认',[],{})
        self.assertEqual(engine.calls,2, 'reference formatting must not trigger another model call')
        scene=result.scenarios[0]
        self.assertEqual(scene.criterion_ids,['R1C1','R1C2'])
        self.assertEqual((scene.given,scene.when,scene.then),(original['given'],original['when'],original['then']))

    def test_superseded_worker_cannot_write_checkpoints(self):
        with self.factory() as db:
            part=RequirementAnalysisPart(analysis_id=1,job_id=1,part_key='probe',input_hash='test',raw='old')
            db.add(part); db.commit(); part_id=part.id
            db.get(RequirementAnalysis,1).job_id=2; db.commit()
        with self.assertRaises(OutputError):
            self.parts.write(part_id,raw='late old output')
        with self.factory() as db:
            self.assertEqual(db.get(RequirementAnalysisPart,part_id).raw,'old')

    def test_retry_reuses_legacy_image_readings(self):
        with self.factory() as db:
            db.add(RequirementSource(id=1, created_by=1, text='合成图片', materials=json.dumps([
                {'id':'IMG1','location':'合成原型','mime_type':'image/png','data':'synthetic-not-sent'}])))
            row=db.get(RequirementAnalysis,1); row.source_id=1
            row.visual_readings=json.dumps([{'id':'IMG1','location':'合成原型','status':'uncertain','text':'保护目录必须确认','uncertainties':'脚注待核对'}])
            db.commit()
        class Engine:
            def stream_generate(self,*a,**kw):
                assert not kw.get('images'), 'legacy image must not be sent again'
                yield {'type':'result','text':json.dumps(fixtures.draft())}
        with patch('app.services.generators.get_provider',return_value=Engine()):
            ai_jobs.run_job(self.factory,1)
        with self.factory() as db:
            self.assertEqual(db.get(AiJob,1).status,'done')
            visual=json.loads(db.get(RequirementAnalysis,1).visual_readings)[0]
            self.assertEqual(visual['uncertainties'],'脚注待核对')
            self.assertEqual(visual['status'],'uncertain')

    def test_unsupported_schema_falls_back_once_with_validation(self):
        class Engine:
            calls=[]
            supports_structured_output=staticmethod(lambda:True)
            def stream_generate(self,*a,**kw):
                self.calls.append(kw)
                if kw.get('output_schema'):
                    yield {'type':'error','msg':'unsupported response_format'}
                else:
                    yield {'type':'result','text':'{"ok":true}'}
        engine=Engine()
        self.assertEqual(self.parts.run('probe','阶段',engine,'source',{'type':'object'},lambda x:x),{'ok':True})
        self.assertEqual(len(engine.calls),2)
        self.assertIn('JSON Schema',engine.calls[1]['prompt_builder']())

    def test_truncation_retries_once_and_keeps_no_draft(self):
        class Engine:
            calls=0
            def stream_generate(inner,*a,**k):
                inner.calls+=1; yield {'type':'result','text':'{"rules":[{"id":"R1"}'}
        engine=Engine()
        with self.assertRaises(OutputError) as exc:
            self.parts.run('probe','合成阶段',engine,'source',{},lambda x:x)
        self.assertEqual(engine.calls,2); self.assertEqual(exc.exception.code,'truncated')
        with self.factory() as db:
            self.assertEqual(db.query(RequirementAnalysisPart).filter_by(status='failed').count(),2)
            self.assertIsNone(db.get(RequirementAnalysis,1).draft)


class RetryAPITests(unittest.TestCase):
    setUp=fixtures.ReviewAPITests.setUp
    tearDown=fixtures.ReviewAPITests.tearDown

    def failed_analysis(self):
        response=self.client.post('/api/ai/requirements/analyze',json={'project_id':1,'task_id':1,'requirement':'合成需求'})
        ids=response.json()['data']
        self.db.expire_all(); job=self.db.get(AiJob,ids['job_id']); job.status='failed'; job.error='模型未完整返回'; self.db.commit()
        return ids

    def test_retry_idempotency_checkpoint_reads_and_project_permissions(self):
        ids=self.failed_analysis(); aid=ids['analysis_id']; old=ids['job_id']
        part=RequirementAnalysisPart(analysis_id=aid,job_id=old,part_key='analysis',input_hash='test',status='failed',raw='合成返回正文',error_code='truncated')
        self.db.add(part); self.db.commit(); part_id=part.id
        mission=TestMission(project_id=1,task_id=1,created_by=1,goal='合成目标',analysis_id=aid,active_job_id=old,phase='attention',resume_phase='analyzing',paused=True)
        self.db.add(mission); self.db.commit(); mid=mission.id
        url=f'/api/ai/requirements/analyses/{aid}'
        detail=self.client.get(url).json()['data']; self.assertTrue(detail['can_retry'])
        self.assertEqual(detail['saved_outputs'][0]['chars'],6)
        self.assertEqual(self.client.get(f'{url}/outputs/{part_id}').json()['data']['raw'],'合成返回正文')
        self.uid=2
        self.assertEqual(self.client.post(url+'/retry',json={}).status_code,403)
        self.assertEqual(self.client.get(f'{url}/outputs/{part_id}').status_code,403)
        self.uid=3; self.assertEqual(self.client.post(url+'/retry',json={}).status_code,403)
        self.uid=1
        first=self.client.post(url+'/retry',json={}).json()['data']
        second=self.client.post(url+'/retry',json={}).json()['data']
        self.assertEqual(first,second); self.assertNotEqual(first['job_id'],old)
        self.db.expire_all(); m=self.db.get(TestMission,mid)
        self.assertEqual(m.active_job_id,first['job_id']); self.assertEqual(m.phase,'analyzing'); self.assertTrue(m.paused)
        self.assertEqual(self.db.query(RequirementAnalysisPart).count(),1)
        self.assertEqual(self.db.get(AiJob,old).status,'failed')


if __name__=='__main__': unittest.main()
