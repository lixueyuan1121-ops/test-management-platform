"""Adversarial integration tests for consent, independent review and grounded evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import test_test_missions as missions, test_requirement_analysis as fixtures
from app.models import AiJob, ExecRun, MissionAssessment, TestCase
from app.services import ai_jobs, mission_quality as quality, test_missions as svc
from app.services.requirement_analysis import encode
from app.core.enums import ExecStatus

class QualityTests(unittest.TestCase):
    for name in ('setUp','tearDown','analyze','detail','save','confirm','get','tick','decide','confirm_analysis','plan','device','approve'):
        locals()[name] = getattr(missions.MissionTests, name)

    def raw_confirm(self, a):
        return self.client.post(f"/api/ai/requirements/analyses/{a['id']}/confirm",json={'revision':a['revision'],'source_hash':a['source_hash'],'scope_reviewed':True})

    def reviewed(self, m, findings):
        c=m['plan']['cases'][0]
        self.fake.quality_response={'cases':[{'case_id':c['id'],'checked_criterion_ids':c['criterion_ids'],'findings':findings}]}
        m=self.decide(m,'replan').json()['data']; self.fake.response={'selected':[{'id':c['id']}],'summary':'待修订'}
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick()
        return self.get(m['id'])

    def test_legacy_analysis_upgrades_without_rewriting_baseline(self):
        from app.models import RequirementAnalysis, RequirementBaseline
        a=self.analyze(); self.confirm_analysis(a['id'])
        self.db.expire_all(); row=self.db.get(RequirementAnalysis,a['id']); old=json.loads(row.draft)
        old.pop('scenarios',None); old.pop('scenario_review_required',None); row.draft=encode(old)
        baseline=self.db.query(RequirementBaseline).one(); baseline.payload=encode(old); self.db.commit()
        previous=baseline.payload
        response=self.client.post('/api/test-missions',json={'project_id':1,'goal':'复用旧需求并补充场景','analysis_id':row.id})
        self.assertEqual(response.status_code,200,response.text); m=response.json()['data']
        self.assertNotEqual(m['analysis_id'],row.id)
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'clarifying')
        self.db.expire_all(); self.assertEqual(self.db.get(RequirementBaseline,baseline.id).payload,previous)

    def test_scenario_confirmation_cannot_be_forged_or_removed(self):
        self.fake.response['scenarios'][0]['reviewed']=True
        a=self.analyze(); self.assertFalse(a['draft']['scenarios'][0]['reviewed'])
        a['draft']['rules'][0]['status']='confirmed'; a=self.save(a)
        self.assertEqual(self.raw_confirm(a).status_code,422)
        a['draft']['scenarios'][0]['reviewed']=True; a=self.save(a); self.assertEqual(self.raw_confirm(a).status_code,200)
        a['draft']['scenarios'][0]['then']='取消后删除文件'; a=self.save(a)
        self.assertFalse(a['draft']['scenarios'][0]['reviewed'])
        a['draft']['scenario_review_required']=False; a['draft']['scenarios']=[]; a=self.save(a)
        self.assertTrue(a['draft']['scenario_review_required']); self.assertEqual(self.raw_confirm(a).status_code,422)

    def test_repair_bounded_versioned_and_rechecked(self):
        m=self.reviewed(self.plan(),[{'kind':'missing_branch','reason':'缺少取消后检查文件的步骤','criterion_ids':['R1-C2'],'suggested_steps':'点击删除，检查弹窗，取消后检查原文件仍存在'}])
        self.assertEqual(m['plan']['quality']['status'],'blocked'); c=m['plan']['cases'][0]; self.device()
        self.assertEqual(self.decide(m,'approve',reviewed=True,runner='test-mac',case_ids=[c['id']]).status_code,422)
        self.assertEqual(self.db.query(ExecRun).count(),0); self.uid=3
        self.assertEqual(self.decide(m,'apply_repair',repair_case_id=c['id']).status_code,403); self.uid=1
        r=self.decide(m,'apply_repair',repair_case_id=c['id']); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.decide(m,'apply_repair',repair_case_id=c['id']).status_code,409)
        self.db.expire_all(); copies=self.db.query(TestCase).order_by(TestCase.id).all()
        self.assertEqual(len(copies),2); self.assertEqual(copies[0].steps,c['steps']); self.assertFalse(copies[1].adopted)
        self.assertIn('检查原文件',copies[1].steps)
        m=r.json()['data']; self.fake.response={'selected':[{'id':copies[1].id}],'summary':'修订副本'}
        self.fake.quality_response={'cases':[{'case_id':copies[1].id,'checked_criterion_ids':c['criterion_ids'],'findings':[]}]}
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick(); m=self.get(m['id'])
        self.assertEqual(m['plan']['quality']['status'],'passed'); self.assertEqual(self.db.query(ExecRun).count(),0)
        self.assertEqual(self.decide(m,'apply_repair',repair_case_id=copies[1].id).status_code,422)
        self.approve(m); self.assertEqual(self.db.query(ExecRun).one().test_case_id,copies[1].id)

    def test_incomplete_review_fails_closed(self):
        m=self.plan(); c=m['plan']['cases'][0]
        self.fake.quality_response={'cases':[{'case_id':c['id'],'checked_criterion_ids':['R1-C1'],'findings':[]}]}
        m=self.decide(m,'replan').json()['data']; self.fake.response={'selected':[{'id':c['id']}],'summary':'遗漏审查'}
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'attention'); self.assertEqual(self.db.query(ExecRun).count(),0)

    def test_ambiguity_cannot_be_repaired_into_product_decision(self):
        m=self.reviewed(self.plan(),[{'kind':'clarification','reason':'业务预期有歧义','criterion_ids':['R1-C1'],'suggested_expected':'静默删除'}])
        self.assertEqual(m['plan']['quality']['cases'][0]['findings'][0]['suggested_expected'],'')
        self.assertEqual(self.decide(m,'apply_repair',repair_case_id=m['plan']['cases'][0]['id']).status_code,422)

    def passed_run(self, report):
        m=self.approve(self.plan()); run=self.db.query(ExecRun).one(); run.status=ExecStatus.passed; run.report=encode(report); self.db.commit()
        return m,run

    def test_pass_flag_and_url_alone_never_count_as_evidence(self):
        m,run=self.passed_run([{'ok':True,'action':'click','desc':'取消后文件保留','shot':'https://example.test/success.png'}])
        self.tick(); r=self.get(m['id'])['report']; self.assertEqual(r['verified'],0)
        self.assertEqual(r['criteria'][0]['state'],'insufficient'); self.assertEqual(self.db.query(AiJob).filter_by(kind='mission_evidence').count(),0)

    def test_false_success_recheck_preserves_snapshot(self):
        m,run=self.passed_run([{'ok':True,'action':'assert_text','check':{'actual':'文件已删除','expected':'文件保留','mode':'equals'}}])
        self.tick(); done=self.get(m['id']); self.assertEqual(done['report']['criteria'][0]['state'],'contradicted'); self.assertEqual(done['report']['verified'],0)
        run.report=encode([{'ok':True,'action':'assert_text','check':{'actual':'文件保留','expected':'文件保留'}}]); self.db.commit()
        self.assertEqual(self.get(m['id'])['report']['verified'],0)
        r=self.decide(done,'recheck_evidence'); self.assertEqual(r.status_code,200,r.text)
        self.tick(); now=self.get(m['id']); self.assertEqual(now['report']['verified'],2)
        self.assertEqual(now['final_report']['verified'],0); self.assertEqual(self.db.query(ExecRun).count(),1); self.assertEqual(self.db.query(MissionAssessment).count(),2)

    def test_evidence_changes_during_model_call(self):
        m,run=self.passed_run([{'ok':True,'action':'assert_text','check':{'actual':'保留','expected':'保留'}}])
        svc.tick(self.Session); self.db.expire_all(); job=self.db.query(AiJob).filter_by(kind='mission_evidence').one(); original=quality.collect
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            with self.Session() as db: db.get(ExecRun,run.id).report='[]'; db.commit()
            return result
        with patch.object(quality,'collect',side_effect=changed): ai_jobs.run_job(self.Session,job.id)
        self.db.expire_all(); self.assertEqual(self.db.get(AiJob,job.id).status,'failed')
        self.assertEqual(self.db.query(MissionAssessment).one().result,'{}'); self.assertEqual(self.get(m['id'])['report']['verified'],0)

    def test_image_grounding_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(quality,'SHOT_ROOT',Path(temp)):
            m,run=self.passed_run([{'ok':True,'action':'assert_visible','shot':'placeholder'}])
            folder=Path(temp)/str(run.id); folder.mkdir(); (folder/'1.png').write_bytes(fixtures.png())
            run.report=encode([{'ok':True,'action':'assert_visible','shot':f'/uploads/execs/{run.id}/1.png'}]); self.db.commit()
            self.fake.evidence_response={'criteria':[{'criterion_id':cid,'verdict':'supported','reason':'模型测试替身观察','refs':[{'step_no':1,'kind':'image','region':'右侧文件区域','quote':'测试图片观察'}]} for cid in ['R1-C1','R1-C2']]}
            self.tick(); done=self.get(m['id']); self.assertEqual(done['report']['verified'],2)
            self.assertEqual(done['report']['runs'][0]['assessment']['images_read'],1)
            (folder/'1.png').unlink(); now=self.get(m['id']); self.assertEqual(now['report']['verified'],0); self.assertEqual(now['final_report']['verified'],2)

class GroundingTests(unittest.TestCase):
    def test_invalid_refs_and_non_assertion_observations(self):
        data={'criteria':[{'id':'C1'}],'steps':[{'step_no':1,'action':'click','ok':True,'check':{'actual':'x','expected':'x'},'check_pass':True}]}
        obj={'criteria':[{'criterion_id':'C1','verdict':'supported','reason':'通过','refs':[{'step_no':1,'kind':'check','quote':'x'}]}]}
        self.assertEqual(quality.validate_assessment(obj,data,[])['criteria'][0]['verdict'],'insufficient')
        for ref in ({'step_no':99,'kind':'check','quote':'x'}, {'step_no':1,'kind':'image','quote':'完成','region':'右上'}, {'step_no':1,'kind':'check','quote':'不存在的值'}):
            obj['criteria'][0]['refs']=[ref]
            with self.assertRaises(ValueError): quality.validate_assessment(obj,data,[])

    def test_image_paths_confined_to_current_run(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(quality,'SHOT_ROOT',Path(temp)):
            p=Path(temp); (p/'1').mkdir(); (p/'2').mkdir(); (p/'2'/'1.png').write_bytes(fixtures.png()); (p/'1'/'1.png').symlink_to(p/'2'/'1.png')
            for url in ('https://localhost/a.png','/uploads/execs/2/1.png','/uploads/execs/1/../2/1.png','/uploads/execs/1/1.png'):
                self.assertIsNone(quality.safe_shot(1,url))


class EvaluationHarnessTests(unittest.TestCase):
    def test_invalid_model_output_is_preserved_without_passing_evaluation(self):
        from scripts import eval_ai_native as harness
        class BrokenJSON:
            def stream_generate(self, *args, **kwargs):
                yield {'type': 'delta', 'text': '{partial'}
                yield {'type': 'result', 'text': '{invalid', 'cost_usd': 0.01}
        sample = next(s for s in harness.load(harness.DATASET)['samples'] if s['id'] == 'synthetic-counter-understanding')
        meter = harness.Meter(BrokenJSON())
        with self.assertRaises(ValueError): harness.evaluate(sample, meter)
        self.assertEqual(meter.calls[0]['raw_output'], '{invalid')
        self.assertFalse(meter.calls[0]['raw_output_truncated'])
        self.assertIn(sample['source'], meter.calls[0]['prompt'])
        self.assertEqual(meter.calls[0]['cost_usd'], 0.01)

    def test_dataset_labels_and_no_observation_match_production(self):
        from scripts import eval_ai_native as harness
        data=harness.load(harness.DATASET)
        self.assertEqual(len(data['samples']),8)
        self.assertTrue(all(s['label_status']=='synthetic' for s in data['samples']))
        self.assertEqual(sum(s['label_status']=='reviewed' for s in data['samples']),0)
        class NeverCall:
            def stream_generate(self,*a,**kw): raise AssertionError('资料无观察值，不应调用模型')
        sample=next(s for s in data['samples'] if s['id']=='evidence-no-observation')
        result=harness.evaluate(sample,NeverCall())
        self.assertTrue(result['matches_label']); self.assertEqual(result['prediction']['verdict'],'insufficient')
        summary=harness.summarize([{'label_status':'draft','matches_label':True}])
        self.assertEqual(summary['human_reviewed']['samples'],0); self.assertEqual(summary['provisional']['matches'],1)

if __name__=='__main__': unittest.main()
