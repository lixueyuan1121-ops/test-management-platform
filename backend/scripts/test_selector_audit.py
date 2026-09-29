import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db.session import Base, get_db
from app.core.deps import get_current_user, require_runner_ctx, RunnerCtx
from app.models import User, Project, RunnerDevice, ProbeRequest, SelectorKey, SelectorAuditSchedule
from app.services.selector_audit import next_daily, tick

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine);self.factory=sessionmaker(bind=self.engine,autoflush=False);self.db=self.factory()
        self.user=User(id=1,username='audit',name='测试',password_hash='x',is_platform_admin=True)
        self.device=RunnerDevice(id=1,owner_id=1,runner_id='audit',name='设备',token='test-secret',last_seen_at=datetime.utcnow())
        self.db.add_all([self.user,Project(id=1,code='p',name='测试'),self.device]);self.db.commit()
        def db_session():
            with self.factory() as db:yield db
        app.dependency_overrides[get_db]=db_session
        app.dependency_overrides[get_current_user]=lambda:self.user
        app.dependency_overrides[require_runner_ctx]=lambda:RunnerCtx(self.device)
        self.client=TestClient(app)
        self.body={'project_id':1,'device_id':1,'enabled':True,'daily_time':'09:00'}
    def tearDown(self):
        app.dependency_overrides.clear();self.client.close();self.db.close();self.engine.dispose()
    def start(self):
        r=self.client.post('/api/selector-audits/run',json=self.body);self.assertEqual(r.status_code,200,r.text)
        pid=r.json()['data']['probe_id']
        claimed=self.client.get('/api/probe/pending?audit_version=1').json()['data'];self.assertEqual(claimed[0]['id'],pid)
        return pid
    def report(self,pid,elements=None,complete=True):
        data={'audit_version':1,'pages':[{'id':'home','label':'首页','ready':True,'complete':complete,'elements':elements or []}]}
        r=self.client.patch(f'/api/probe/{pid}',json={'result':data});self.assertEqual(r.status_code,200,r.text);return data
    def element(self):return {'frame':'shell','tag':'button','text':'返回首页','verified':[{'by':'testid','value':'home-static-control'}]}
    def test_queue_excludes_old_runner_and_prevents_duplicate(self):
        r=self.client.post('/api/selector-audits/run',json=self.body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/probe/pending').json()['data'],[])
        self.assertEqual(self.client.post('/api/selector-audits/run',json=self.body).status_code,409)
        self.assertEqual(len(self.client.get('/api/probe/pending?audit_version=1').json()['data']),1)
    def test_sync_idempotent_and_no_false_retirement_on_incomplete(self):
        p=self.start();data=self.report(p,[self.element()])
        self.assertEqual(self.client.patch(f'/api/probe/{p}',json={'result':data}).status_code,200)
        self.db.expire_all();row=self.db.query(SelectorKey).one();self.assertEqual(row.change_status,'new');self.assertTrue(row.desc.startswith('[首页]-[首页]'))
        self.report(self.start(),complete=False);self.report(self.start(),complete=False)
        self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).one().change_status,'new')
        self.report(self.start());self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).one().change_status,'new')
        self.report(self.start());self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).one().change_status,'retired')
        self.report(self.start(),[self.element()]);self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).one().change_status,'updated')
    def test_existing_legacy_description_repaired_with_history_and_no_locator_changes(self):
        from app.models import SelectorRevision, SelectorAuditRun
        row=SelectorKey(project_id=1,key='old_home_button',page='首页',frame='shell',desc='返回首页按钮',
            candidates=json.dumps(self.element()['verified']),change_status='new')
        self.db.add(row);self.db.commit();key_id=row.id;original=row.candidates
        self.report(self.start(),[self.element()]);self.db.expire_all()
        row=self.db.get(SelectorKey,key_id)
        self.assertEqual(row.desc,'[首页]-[首页]-[首页浏览]-[返回首页按钮]')
        self.assertEqual(row.key,'old_home_button');self.assertEqual(json.loads(row.candidates),json.loads(original))
        self.assertEqual(row.change_status,'updated')
        self.assertEqual(json.loads(self.db.query(SelectorRevision).filter_by(key_id=key_id).one().snapshot)['desc'],'返回首页按钮')
        self.report(self.start(),[self.element()]);self.db.expire_all()
        summary=json.loads(self.db.query(SelectorAuditRun).order_by(SelectorAuditRun.id.desc()).first().summary)
        self.assertEqual((summary['new'],summary['updated']),(0,0))
        self.assertEqual(self.db.query(SelectorRevision).filter_by(key_id=key_id).count(),1)
        row=self.db.get(SelectorKey,key_id);row.desc='[自定义导航]-[自定义页面]-[自定义场景]-[返回按钮]';self.db.commit()
        self.report(self.start(),[self.element()]);self.db.expire_all()
        self.assertEqual(self.db.get(SelectorKey,key_id).desc,'[自定义导航]-[自定义页面]-[自定义场景]-[返回按钮]')

    def test_ambiguous_but_present_controls_are_not_retired(self):
        self.report(self.start(),[self.element()])
        element=self.element();element['observed']=element['verified'];element['verified']=[]
        self.report(self.start(),[element]);self.report(self.start(),[element])
        self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).one().change_status,'new')

    def test_css_xpath_and_collection_import_and_idempotency(self):
        elements=[{'frame':'shell','tag':'button','verified':[{'by':'css','value':'.only-save'}]},
            {'frame':'shell','tag':'button','verified':[{'by':'xpath','value':'/html/body/button[2]'}]},
            *[{'frame':'shell','tag':'button','collections':[{'by':'testid','value':'card-open','src':'audit_collection'}]} for _ in range(4)]]
        self.report(self.start(),elements)
        self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).count(),3)
        self.assertTrue(any(json.loads(r.candidates)[0]['by']=='xpath' for r in self.db.query(SelectorKey)))
        self.report(self.start(),elements);self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).count(),3)
        from app.models import SelectorAuditRun
        summary=json.loads(self.db.query(SelectorAuditRun).order_by(SelectorAuditRun.id.desc()).first().summary)
        self.assertEqual((summary['new'],summary['registered'],summary['merged'],summary['collections']),(0,3,3,1))

    def test_identical_css_on_different_pages_must_not_merge(self):
        pid=self.start();el={'frame':'shell','tag':'button','verified':[{'by':'css','value':'.same-layout-button'}]}
        data={'audit_version':1,'pages':[{'id':page,'ready':True,'complete':True,'elements':[el]} for page in ['home','tasks']]}
        r=self.client.patch(f'/api/probe/{pid}',json={'result':data});self.assertEqual(r.status_code,200,r.text)
        self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).count(),2)
        self.assertEqual({r.page for r in self.db.query(SelectorKey)},{'首页','任务列表'})

    def test_group_rename_keeps_audit_identity(self):
        el={'frame':'shell','tag':'button','verified':[{'by':'css','value':'.rename-group'}]}
        self.report(self.start(),[el]);self.db.expire_all()
        row=self.db.query(SelectorKey).one();row.page='自定义页面';self.db.commit()
        self.report(self.start(),[el]);self.db.expire_all()
        self.assertEqual(self.db.query(SelectorKey).count(),1)
        self.assertEqual(self.db.query(SelectorKey).one().page,'自定义页面')

    def test_deleted_registry_can_be_discovered_again_without_old_observations(self):
        from app.models import SelectorAuditObservation
        self.db.add(SelectorAuditObservation(key_id=999,page_token='home',managed=True));self.db.commit()
        self.report(self.start(),[self.element()]);self.db.expire_all()
        self.assertEqual(self.db.query(SelectorAuditObservation).count(),1)
        key=self.db.query(SelectorKey).one().id
        r=self.client.delete(f'/api/selectors/{key}');self.assertEqual(r.status_code,200,r.text)
        self.db.expire_all();self.assertEqual(self.db.query(SelectorAuditObservation).count(),0)
        self.report(self.start(),[self.element()]);self.db.expire_all()
        key=self.db.query(SelectorKey).one().id
        r=self.client.post('/api/selectors/batch-delete',json={'ids':[key]});self.assertEqual(r.status_code,200,r.text)
        self.db.expire_all();self.assertEqual(self.db.query(SelectorAuditObservation).count(),0)
        self.report(self.start(),[self.element()]);self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).count(),1)

    def test_confirmed_collection_supersedes_only_audit_positional_locators(self):
        xpath='/html/body/section/article[1]/button'
        el={'frame':'shell','tag':'button','verified':[{'by':'xpath','value':xpath,'src':'audit_xpath'}]}
        self.report(self.start(),[el]);self.db.expire_all();old_id=self.db.query(SelectorKey).one().id
        collection={'frame':'shell','tag':'button','collections':[{'by':'css','value':'.result-card button','src':'audit_collection'}],'member_xpath':xpath}
        self.report(self.start(),[collection]);self.db.expire_all()
        self.assertEqual(self.db.get(SelectorKey,old_id).change_status,'retired')
        self.assertEqual(self.db.query(SelectorKey).filter(SelectorKey.change_status!='retired').count(),1)
        self.report(self.start(),[collection]);self.db.expire_all();self.assertEqual(self.db.query(SelectorKey).count(),2)

    def test_running_cancel_keeps_device_locked_until_runner_ack(self):
        p=self.start();self.assertEqual(self.client.post(f'/api/selector-audits/{p}/cancel').status_code,200)
        self.assertTrue(self.client.post(f'/api/probe/{p}/heartbeat').json()['data']['cancel_requested'])
        self.assertEqual(self.client.post('/api/selector-audits/run',json=self.body).status_code,409)
        r=self.client.patch(f'/api/probe/{p}',json={'error':'用户已终止巡检'});self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.post('/api/selector-audits/run',json=self.body).status_code,200)
    def test_schedule_persists_and_tick_dispatches_once(self):
        r=self.client.put('/api/selector-audits/schedule',json=self.body);self.assertEqual(r.status_code,200,r.text)
        self.db.expire_all();s=self.db.query(SelectorAuditSchedule).one();s.next_run_at=datetime.utcnow()-timedelta(minutes=1);self.db.commit()
        with patch('app.db.session.SessionLocal',self.factory):tick();tick()
        self.db.expire_all();self.assertEqual(self.db.query(ProbeRequest).count(),1)
        self.assertGreater(self.db.query(SelectorAuditSchedule).one().next_run_at,datetime.utcnow())
    def test_offline_does_not_queue_and_foreign_device_rejected(self):
        self.device.last_seen_at=datetime.utcnow()-timedelta(hours=1);self.db.commit()
        self.assertEqual(self.client.post('/api/selector-audits/run',json=self.body).status_code,409)
        self.assertEqual(self.client.post('/api/selector-audits/run',json={**self.body,'device_id':99}).status_code,400)
        self.assertEqual(self.db.query(ProbeRequest).count(),0)
    def test_daily_timezone(self):
        self.assertEqual(next_daily('09:00',datetime(2026,9,29,0)),datetime(2026,9,29,1))
        self.assertEqual(next_daily('09:00',datetime(2026,9,29,2)),datetime(2026,9,30,1))

if __name__=='__main__':unittest.main()
