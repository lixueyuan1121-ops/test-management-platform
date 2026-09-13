"""Isolated integration tests: API + durable coordinator + real queue handlers, fake models."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from scripts import test_requirement_analysis as fixtures
draft = fixtures.draft
from app.models import (TestMission, MissionRun, AiJob, TestCase, ExecRun, RequirementCaseLink, RunnerDevice, ProjectMember)
from app.core.enums import ExecStatus, ProjectRole
from app.services import test_missions as svc, ai_jobs
from app.services.requirement_analysis import encode


class MissionTests(unittest.TestCase):
    setUp = fixtures.ReviewAPITests.setUp
    tearDown = fixtures.ReviewAPITests.tearDown
    analyze = fixtures.ReviewAPITests.analyze
    detail = fixtures.ReviewAPITests.detail
    save = fixtures.ReviewAPITests.save
    confirm = fixtures.ReviewAPITests.confirm

    def get(self, mid):
        r=self.client.get(f'/api/test-missions/{mid}')
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['data']

    def tick(self):
        svc.tick(self.Session); self.db.expire_all()
        for job in self.db.query(AiJob).filter_by(kind="mission_evidence",status="pending").all():
            ai_jobs.run_job(self.Session,job.id)
        svc.tick(self.Session); self.db.expire_all()

    def decide(self,m,action,**kw):
        return self.client.post(f"/api/test-missions/{m['id']}/decisions",json={'revision':m['revision'],'action':action,**kw})

    def confirm_analysis(self,aid):
        a=self.detail(aid); a['draft']['rules'][0]['status']='confirmed'
        a['draft']['rules'][1].update(status='excluded',review_note='下期回收站')
        a=self.save(a); self.assertEqual(self.confirm(a).status_code,200)

    def plan(self):
        r=self.client.post('/api/test-missions',json={'project_id':1,'goal':'验证保护目录删除流程','requirement':{
            'project_id':1,'task_id':1,'requirement':'保护目录删除必须确认'}})
        self.assertEqual(r.status_code,200,r.text); m=r.json()['data']
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'clarifying')
        self.confirm_analysis(m['analysis_id']); self.tick(); m=self.get(m['id'])
        self.assertEqual(m['phase'],'generating')
        generated={'raw':'[]','meta':{'cost_usd':.05},'errors':[],'cases':[{
            'title':'保护文件删除','steps':'点击删除后取消','expected':'确认弹窗，取消后文件保留','precondition':'保护目录内有文件',
            'priority':'P0','category':'功能','kind':'gui','criterion_ids':['R1-C1','R1-C2']}]}
        with patch('app.services.requirement_analysis.generate_from_baseline',return_value=generated):
            ai_jobs.run_job(self.Session,m['active_job_id'])
        self.tick(); m=self.get(m['id']); self.assertEqual(m['phase'],'planning')
        options=svc.candidates(self.db,self.db.get(TestMission,m['id']))
        self.fake.response={'summary':'优先保护目录和取消分支','selected':[{'id':options[0]['id'],'reason':'验证确认与保留'}],'risks':[]}
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick(); m=self.get(m['id'])
        self.assertEqual(m['phase'],'awaiting_approval',m.get('error'))
        self.assertEqual(self.db.query(ExecRun).count(),0)
        return m

    def device(self):
        if not self.db.query(RunnerDevice).first():
            self.db.add(RunnerDevice(owner_id=1,runner_id='test-mac',name='隔离设备',token='not-a-live-token',platform='web')); self.db.commit()

    def approve(self,m,retries=0):
        self.device()
        r=self.decide(m,'approve',reviewed=True,runner='test-mac',case_ids=[c['id'] for c in m['plan']['cases']],max_retries=retries)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['data']

    def test_end_to_end_restore_idempotence_and_metrics(self):
        m=self.plan(); self.approve(m)
        self.assertEqual(self.decide(m,'approve',reviewed=True,runner='test-mac',case_ids=[m['plan']['cases'][0]['id']]).status_code,409)
        for _ in range(3): self.tick()
        self.assertEqual(self.db.query(ExecRun).count(),1)
        run=self.db.query(ExecRun).one(); run.status=ExecStatus.passed; run.report=encode([{'ok':True,'action':'assert_text','desc':'确认弹窗，取消后文件保留','check':{'actual':'显示确认弹窗，取消后文件保留','expected':'显示确认弹窗，取消后文件保留','mode':'equals'}}])
        self.db.commit(); self.tick(); done=self.get(m['id'])
        self.assertEqual(done['phase'],'completed'); self.assertEqual((done['report']['verified'],done['report']['total']),(2,2))
        self.assertEqual(done['report']['verdict'],'ready_for_review')
        metrics=self.client.get('/api/test-missions/metrics').json()['data']
        self.assertEqual((metrics['total'],metrics['ready_for_review'],metrics['avg_interventions']),(1,1,2))
        self.assertNotIn('payload',json.dumps(done['report'])); self.assertNotIn('api_env',json.dumps(done))
        run.payload = encode({'api_env': {'auth': 'private-test-secret'}}); self.db.commit()
        evidence = self.client.get(f"/api/test-missions/{m['id']}/runs/{run.id}")
        self.assertEqual(evidence.status_code, 200)
        self.assertNotIn('private-test-secret', evidence.text)
        self.assertEqual(self.client.get(f"/api/test-missions/{m['id']}/runs/99999").status_code, 404)
        tc=self.db.get(TestCase,run.test_case_id); tc.expected='直接删除'; self.db.commit()
        changed=self.get(m['id']); self.assertEqual(changed['report']['verified'],0); self.assertEqual(changed['final_report']['verified'],2)

    def test_model_cannot_invent_ids_and_restart_recovers(self):
        m=self.plan(); r=self.decide(m,'replan'); self.assertEqual(r.status_code,200,r.text); m=r.json()['data']
        self.fake.response={'selected':[{'id':99999}],'summary':'伪造'}
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick(); m=self.get(m['id'])
        self.assertEqual(m['phase'],'attention'); self.assertEqual(self.db.query(ExecRun).count(),0)
        r=self.decide(m,'resume'); self.assertEqual(r.status_code,200,r.text); m=r.json()['data']
        self.fake.response={'selected':[{'id':self.db.query(TestCase).first().id}],'summary':'恢复方案'}
        ai_jobs.run_job(self.Session,m['active_job_id'])
        self.db.expire_all(); job=self.db.get(AiJob,m['active_job_id']); job.status='failed'; self.db.commit(); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'awaiting_approval')

    def test_permissions_and_changed_scope(self):
        m=self.plan(); self.uid=2
        self.assertEqual(self.client.get(f"/api/test-missions/{m['id']}").status_code,403)
        self.assertEqual(self.client.get('/api/test-missions/metrics').json()['data']['total'],0)
        self.uid=3; self.assertEqual(self.decide(m,'pause').status_code,403)
        self.uid=1; self.assertEqual(self.decide(m,'approve',reviewed=True,runner='unknown',case_ids=[999]).status_code,422)
        a=self.detail(m['analysis_id']); a['draft']['rules'][0]['expected']='变化'; self.save(a)
        self.assertTrue(self.get(m['id'])['stale'])
        self.assertEqual(self.decide(m,'approve',reviewed=True,runner='test-mac',case_ids=[m['plan']['cases'][0]['id']]).status_code,409)
        self.assertEqual(self.db.query(ExecRun).count(),0)

    def test_case_edit_blocks_approval_atomically(self):
        m=self.plan(); self.device(); tc=self.db.query(TestCase).one(); tc.steps='改变步骤'; self.db.commit()
        r=self.decide(m,'approve',reviewed=True,runner='test-mac',case_ids=[tc.id]); self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.db.query(ExecRun).count(),0); self.db.expire_all(); self.assertFalse(self.db.get(TestCase,tc.id).adopted)

    def test_no_evidence_cannot_turn_green(self):
        m=self.approve(self.plan()); run=self.db.query(ExecRun).one(); run.status=ExecStatus.passed; self.db.commit(); self.tick()
        report=self.get(m['id'])['report']; self.assertEqual(report['verified'],0)
        self.assertEqual(report['criteria'][0]['state'],'no_evidence'); self.assertEqual(report['total'],2)

    def test_retry_bounded_preserves_original_failure(self):
        m=self.approve(self.plan(),retries=1); original=self.db.query(ExecRun).one()
        original.status=ExecStatus.blocked; original.reason='连接超时'; self.db.commit(); self.tick()
        link=self.db.query(MissionRun).one(); self.fake.response={'kind':'environment','confidence':.95,'reason':'连接中断','suggestion':'复测'}
        ai_jobs.run_job(self.Session,link.triage_job_id); self.tick(); self.tick()
        runs=self.db.query(ExecRun).order_by(ExecRun.id).all(); self.assertEqual(len(runs),2)
        self.assertEqual(runs[1].retry_of,runs[0].id); self.assertEqual(runs[1].payload,runs[0].payload)
        runs[1].status=ExecStatus.passed; runs[1].report='[{"ok":true}]'; self.db.commit(); self.tick()
        m=self.get(m['id']); self.assertEqual(m['phase'],'completed'); self.assertEqual(m['report']['verified'],0)
        self.assertEqual(m['report']['criteria'][0]['state'],'flaky'); self.assertEqual(len(m['report']['runs']),2)
        self.assertEqual(self.db.get(ExecRun,runs[0].id).status,ExecStatus.blocked)

    def test_business_failure_never_auto_retries(self):
        m=self.approve(self.plan(),retries=1); run=self.db.query(ExecRun).one(); run.status=ExecStatus.failed
        run.reason='文件被误删'; self.db.commit(); self.tick(); link=self.db.query(MissionRun).one()
        self.fake.response={'kind':'environment','confidence':1,'reason':'模型误判','suggestion':'复测'}
        ai_jobs.run_job(self.Session,link.triage_job_id); self.tick()
        self.assertEqual(self.db.query(ExecRun).count(),1); self.assertEqual(self.get(m['id'])['phase'],'completed')
        self.assertEqual(self.get(m['id'])['report']['criteria'][0]['state'],'failed')

    def test_pause_and_budget(self):
        m=self.plan(); paused=self.decide(m,'pause').json()['data']; self.tick()
        self.assertEqual(self.db.query(ExecRun).count(),0); self.assertTrue(self.get(m['id'])['paused'])
        m=self.decide(paused,'resume').json()['data']; m=self.approve(m)
        row=self.db.get(TestMission,m['id']); row.authorized_at=datetime.now()-timedelta(hours=2); self.db.commit(); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'completed'); self.assertEqual(self.db.query(ExecRun).one().status,ExecStatus.blocked)

    def test_permission_revocation_stops_followup(self):
        m=self.plan(); member=self.db.query(ProjectMember).filter_by(user_id=1,project_id=1).one(); member.role=ProjectRole.guest
        row=self.db.get(TestMission,m['id']); row.phase='planning'; self.db.commit(); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'attention'); self.assertEqual(self.db.query(ExecRun).count(),0)

    def test_history_reuse_preserves_old_links(self):
        self.approve(self.plan()); old=[(l.id,l.baseline_id,l.criterion_id) for l in self.db.query(RequirementCaseLink).all()]
        self.fake.response=draft(); a=self.analyze(); self.confirm_analysis(a['id'])
        r=self.client.post('/api/test-missions',json={'project_id':1,'goal':'复用已确认业务行为','analysis_id':a['id']})
        self.assertEqual(r.status_code,200,r.text); mid=r.json()['data']['id']; self.tick(); m=self.get(mid)
        self.assertEqual(m['phase'],'planning'); opts=svc.candidates(self.db,self.db.get(TestMission,mid)); self.assertTrue(opts[0]['reused'])
        self.fake.response={'selected':[{'id':opts[0]['id']}],'summary':'复用'}
        ai_jobs.run_job(self.Session,m['active_job_id']); self.tick(); self.approve(self.get(mid))
        self.assertEqual([(l.id,l.baseline_id,l.criterion_id) for l in self.db.query(RequirementCaseLink).all()],old)
        self.assertEqual(self.db.query(TestCase).count(),1)

    def test_paused_execution_still_obeys_budget(self):
        m=self.approve(self.plan()); self.decide(m,'pause')
        row=self.db.get(TestMission,m['id']); self.db.refresh(row)
        row.authorized_at=datetime.now()-timedelta(hours=2); self.db.commit(); self.tick()
        self.assertEqual(self.get(m['id'])['phase'],'completed')
        self.assertEqual(self.db.query(ExecRun).one().status,ExecStatus.blocked)

    def test_unexecuted_planned_branch_remains_missing(self):
        m=self.plan(); row=self.db.get(TestMission,m['id']); plan=svc.unpack(row.plan)
        plan['cases'].append({**plan['cases'][0], 'id':99999, 'kind':'manual', 'criterion_ids':['R1-C1']})
        row.plan=encode(plan); self.db.commit()
        self.approve(m)
        run=self.db.query(ExecRun).one(); run.status=ExecStatus.passed; run.report=encode([{'ok':True,'action':'assert_text','check':{'actual':'显示确认弹窗，取消后文件保留','expected':'显示确认弹窗，取消后文件保留','mode':'equals'}}]); self.db.commit(); self.tick()
        report=self.get(m['id'])['report']
        self.assertEqual(report['total'],2); self.assertEqual(report['verified'],1)
        self.assertEqual(report['criteria'][0]['state'],'missing')
        self.assertEqual(report['verdict'],'needs_attention')

    def test_retry_budget_exhausted_even_if_ai_keeps_suggesting_retry(self):
        m=self.approve(self.plan(),retries=1)
        for attempt in (1,2):
            run=self.db.query(ExecRun).order_by(ExecRun.id.desc()).first()
            self.assertEqual(run.attempt,attempt)
            run.status=ExecStatus.blocked; run.reason='仍然超时'; self.db.commit(); self.tick()
            link=self.db.query(MissionRun).filter_by(run_id=run.id).one()
            self.fake.response={'kind':'environment','confidence':.99,'reason':'连接超时','suggestion':'继续重试'}
            ai_jobs.run_job(self.Session,link.triage_job_id); self.tick()
        self.assertEqual(self.db.query(ExecRun).count(),2)
        self.assertEqual(self.get(m['id'])['phase'],'completed')

if __name__=='__main__': unittest.main()
