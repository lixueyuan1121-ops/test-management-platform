"""Async import regression using isolated file SQLite and independent connections."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy.exc import OperationalError
from app.models import User, ProjectMember, TestCase, ExecRun, Requirement, VerifiedImportJob, VerifiedImportItem
from app.core.enums import ProjectRole
from app.api.verified_import_jobs import router
from app.services.verified_import_jobs import process_one, drain_once
from scripts.test_verified_dedup_pagination import ApiTests, source


class AsyncTests(ApiTests):
    def setUp(self):
        super().setUp()
        self.client.app.include_router(router)
        with self.factory() as db:
            db.add_all([User(id=i, username=f"u{i}", name=f"u{i}", password_hash="fixture", is_platform_admin=i in (1,2)) for i in (1,2,3)])
            db.commit()

    def submit(self, body=None, user=1):
        return self.client.post('/api/verified-imports/jobs', json=body or source(), headers={"x-user": str(user)})

    def job(self, job_id):
        return self.client.get(f'/api/verified-imports/jobs/{job_id}').json()['data']

    def item(self, job_id, item_id):
        return self.client.get(f'/api/verified-imports/jobs/{job_id}/items/{item_id}').json()['data']

    def test_accept_returns_without_matching_and_later_finishes(self):
        body=source()
        with patch('app.api.verified_import.plans_for', side_effect=AssertionError('must not match on submit')):
            response=self.submit(body)
        self.assertEqual(response.status_code,202,response.text)
        receipt=response.json()['data']; self.assertTrue(receipt['accepted']);jid=receipt['job_id']
        self.assertEqual(self.count(TestCase),0);self.assertEqual(self.count(ExecRun),0)
        self.assertNotIn('payload',receipt)
        self.assertEqual(self.submit(body).json()['data']['job_id'],jid)
        changed=copy.deepcopy(body);changed['requirement']='changed'
        self.assertEqual(self.submit(changed).status_code,409)
        drain_once(self.factory)
        job=self.job(jid);self.assertEqual(job['status'],'completed',job)
        item=job['items'][0];self.assertEqual(item['receipt']['disposition'],'created')
        self.assertFalse(process_one(item['id'],self.factory));self.assertEqual(self.count(ExecRun),1)
        with self.factory() as db:
            payload=json.loads(db.query(ExecRun).one().payload)
            self.assertEqual(payload['verified_import']['external_id'],body['external_id'])
        self.assertEqual(self.submit(body).json()['data']['job_id'],jid)
        self.assertEqual(self.count(ExecRun),1)

    def test_concurrent_acceptance_and_workers_cross_user_reuse(self):
        body=source('same-retry')
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses=list(pool.map(lambda _: self.submit(body),range(6)))
        self.assertTrue(all(r.status_code==202 for r in responses),[r.text for r in responses])
        self.assertEqual(len({r.json()['data']['job_id'] for r in responses}),1)
        self.assertEqual(self.count(VerifiedImportJob),1)
        for i in range(5):self.submit(source(f'parallel-{i}',1+i%2),1+i%2)
        with self.factory() as db: ids=[i.id for i in db.query(VerifiedImportItem).all()]
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda iid: process_one(iid,self.factory),ids*2))
        self.assertEqual(self.count(TestCase),1);self.assertEqual(self.count(ExecRun),6)
        with self.factory() as db:self.assertTrue(all(i.status=='done' for i in db.query(VerifiedImportItem).all()))

    def test_one_ambiguous_does_not_block_siblings_and_admin_confirms(self):
        cid=self.baseline();body=self.ambiguous()
        extra=copy.deepcopy(body['cases'][0]);extra.update(title='余额支付',expected='订单付款完成',steps='提交付款')
        extra['script'][0]['target']['selector']='#pay';extra['script'][1]['target']['selector']='#paid'
        body['cases'].append(extra)
        jid=self.submit(body,2).json()['data']['job_id'];drain_once(self.factory)
        job=self.job(jid);self.assertEqual(job['counts']['needs_confirmation'],1);self.assertEqual(job['counts']['done'],1)
        waiting=job['items'][0];detail=self.item(jid,waiting['id'])
        choice={'action':'reuse','case_id':cid,'token':detail['plan']['confirmation_token'],'reason':'已确认相同业务场景'}
        url=f"/api/verified-imports/jobs/{jid}/items/{waiting['id']}/resolve"
        self.assertEqual(self.client.post(url,json=choice,headers={'x-user':'3'}).status_code,403)
        self.assertEqual(self.client.post(url,json=choice).status_code,200)
        self.assertEqual(self.client.post(url,json=choice).status_code,409)
        drain_once(self.factory)
        final=self.job(jid);self.assertEqual(final['status'],'completed',final)
        self.assertEqual(self.count(TestCase),2);self.assertEqual(self.count(ExecRun),3)
        with self.factory() as db:
            run=db.get(ExecRun,final['items'][0]['receipt']['run_id'])
            self.assertEqual(json.loads(run.payload)['script'],body['cases'][0]['script'])
            self.assertEqual(json.loads(run.payload)['verified_import']['resolved_by'],1)
            self.assertEqual(run.enqueued_by,2)

    def test_confirmation_rechecked_after_candidate_changes(self):
        cid=self.baseline();jid=self.submit(self.ambiguous(),2).json()['data']['job_id'];drain_once(self.factory)
        item=self.job(jid)['items'][0];detail=self.item(jid,item['id'])
        choice={'action':'reuse','case_id':cid,'token':detail['plan']['confirmation_token'],'reason':'管理员确认场景一致'}
        self.client.post(f"/api/verified-imports/jobs/{jid}/items/{item['id']}/resolve",json=choice)
        with self.factory() as db:db.get(TestCase,cid).expected='验收点已经改变';db.commit()
        drain_once(self.factory)
        self.assertEqual(self.job(jid)['items'][0]['status'],'needs_confirmation')
        self.assertEqual(self.count(ExecRun),1)

    def test_transaction_failure_rolls_back_case_run_and_receipt(self):
        jid=self.submit().json()['data']['job_id'];iid=self.job(jid)['items'][0]['id']
        from app.api.verified_import import perform_import
        def fail(*args,**kwargs):
            perform_import(*args,**kwargs)
            raise OperationalError('injected',None,Exception('simulated disconnect'))
        with patch('app.api.verified_import.perform_import',side_effect=fail):process_one(iid,self.factory)
        self.assertEqual(self.count(TestCase),0);self.assertEqual(self.count(ExecRun),0)
        item=self.job(jid)['items'][0];self.assertEqual(item['status'],'pending');self.assertEqual(item['attempts'],1)
        with self.factory() as db: db.get(VerifiedImportItem,iid).next_attempt_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
        drain_once(self.factory)
        self.assertEqual(self.job(jid)['status'],'completed');self.assertEqual(self.count(ExecRun),1)

    def test_failure_retry_permission_and_invalid_report(self):
        body=source();body['cases'][0]['report'][0]['ok']=False
        self.assertEqual(self.submit(body).status_code,422);self.assertEqual(self.count(VerifiedImportJob),0)
        body=source();body['cases'][0]['script'][0]['action']='unsupported';body['cases'][0]['report'][0]['action']='unsupported'
        jid=self.submit(body).json()['data']['job_id'];drain_once(self.factory)
        item=self.job(jid)['items'][0];self.assertEqual(item['status'],'failed')
        url=f"/api/verified-imports/jobs/{jid}/items/{item['id']}/retry"
        self.assertEqual(self.client.post(url,headers={'x-user':'3'}).status_code,403)
        self.assertEqual(self.client.post(url).status_code,200)
        self.assertEqual(self.client.post(url).status_code,409)
        self.assertEqual(self.client.get(f'/api/verified-imports/jobs/{jid}',headers={'x-user':'4'}).status_code,403)

    def test_restart_recovers_uncommitted_processing_and_retry_budget(self):
        jid=self.submit().json()['data']['job_id'];iid=self.job(jid)['items'][0]['id']
        from app.api.verified_import import perform_import
        def crash(*args,**kwargs):
            perform_import(*args,**kwargs)
            raise SystemExit('simulated worker process termination')
        with patch('app.api.verified_import.perform_import',side_effect=crash), self.assertRaises(SystemExit):
            process_one(iid,self.factory)
        self.assertEqual(self.count(ExecRun),0)
        self.assertEqual(self.job(jid)['items'][0]['status'],'pending')
        for n in range(3):
            with patch('app.api.verified_import.perform_import',side_effect=OperationalError('transient',None,Exception('retry fixture'))):
                process_one(iid,self.factory)
            with self.factory() as db:db.get(VerifiedImportItem,iid).next_attempt_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
        self.assertEqual(self.job(jid)['items'][0]['status'],'failed')
        self.assertEqual(self.job(jid)['items'][0]['attempts'],3)
        self.client.post(f'/api/verified-imports/jobs/{jid}/items/{iid}/retry')
        drain_once(self.factory);self.assertEqual(self.count(ExecRun),1)

    def test_non_admin_member_can_view_but_not_resolve(self):
        with self.factory() as db:db.add(ProjectMember(project_id=1,user_id=3,role=ProjectRole.member));db.commit()
        jid=self.submit().json()['data']['job_id']
        detail=self.client.get(f'/api/verified-imports/jobs/{jid}',headers={'x-user':'3'})
        self.assertEqual(detail.status_code,200);self.assertFalse(detail.json()['data']['can_manage'])

    def test_import_defaults_shared_requirement_and_preserves_manual_settings(self):
        first=source('default-first')
        second=source('default-second',2);second['requirement']='支付功能真实需求'
        second['cases'][0].update(title='余额支付完成',steps='提交订单付款',expected='支付成功')
        second['cases'][0]['script'][0]['target']['selector']='#pay'
        second['cases'][0]['script'][1]['target']['selector']='#paid'
        jids=[self.submit(first).json()['data']['job_id'],self.submit(second,2).json()['data']['job_id']]
        ids=[self.job(j)['items'][0]['id'] for j in jids]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda iid: process_one(iid,self.factory),ids))
        with self.factory() as db:
            cases=db.query(TestCase).all();self.assertEqual(len(cases),2)
            req=db.query(Requirement).one();self.assertEqual(req.title,'codex导入用例')
            self.assertTrue(all(c.requirement_id==req.id and not c.is_regression for c in cases))
            actual={json.loads(r.payload)['verified_import']['requirement'] for r in db.query(ExecRun).all()}
            self.assertEqual(actual,{first['requirement'],second['requirement']})
            # Existing manual decisions must survive a subsequent duplicate import.
            original=db.get(TestCase,self.job(jids[0])['items'][0]['receipt']['case_id'])
            manual=Requirement(project_id=1,title='人工维护的产品需求',created_by=1)
            db.add(manual);db.flush();original.requirement_id=manual.id;original.is_regression=True
            original_id,manual_id=original.id,manual.id;db.commit()
        retry=copy.deepcopy(first);retry['external_id']='later-execution'
        self.submit(retry);drain_once(self.factory)
        with self.factory() as db:
            original=db.get(TestCase,original_id)
            self.assertTrue(original.is_regression);self.assertEqual(original.requirement_id,manual_id)
            self.assertEqual(db.query(TestCase).count(),2);self.assertEqual(db.query(ExecRun).count(),3)

    def test_queue_pages_and_lightweight_responses(self):
        for i in range(21): self.submit(source(f'pages-{i:04}'))
        for page,count in ((1,20),(2,1)):
            response=self.client.get('/api/verified-imports/jobs',params={'project_id':1,'page':page})
            data=response.json()['data'];self.assertEqual(data['total'],21);self.assertEqual(len(data['items']),count)
            self.assertNotIn('payload',response.text);self.assertNotIn('report',response.text)
        self.assertEqual(self.client.get('/api/verified-imports/jobs',params={'project_id':1,'status':'bad'}).status_code,422)


if __name__=='__main__': unittest.main(defaultTest='AsyncTests')
