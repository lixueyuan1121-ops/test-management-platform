import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.db.session import get_db
from app.core.deps import get_current_user, assert_project_role
from app.core.enums import ProjectRole
from app.models import User, SelectorAuditSchedule, SelectorAuditRun, ProbeRequest
from app.services import selector_audit as service
from app.services.selector_device import owned_device
from app.schemas.common import ok

router=APIRouter(prefix='/api/selector-audits',tags=['selector-audits'])
READ=(ProjectRole.admin,ProjectRole.member,ProjectRole.guest)
WRITE=(ProjectRole.admin,ProjectRole.member)
class Config(BaseModel):
    project_id:int
    sub_product:str=''
    device_id:int
    enabled:bool=False
    daily_time:str=Field(default='09:00',pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')

def config_out(row):
    if not row:return {'enabled':False,'daily_time':'09:00','timezone':'Asia/Shanghai'}
    return {'id':row.id,'device_id':row.device_id,'owner_id':row.owner_id,'enabled':row.enabled,'daily_time':row.daily_time,
      'timezone':'Asia/Shanghai','next_run_at':row.next_run_at.isoformat()+'Z' if row.next_run_at else None,'error':row.error}

def obtain(db,user,body):
    from app.api.selectors import _valid_sub
    assert_project_role(db,user,body.project_id,WRITE)
    scope=_valid_sub(body.sub_product)
    owned_device(db,user,'',body.device_id)
    row=db.query(SelectorAuditSchedule).filter_by(project_id=body.project_id,sub_product=scope).with_for_update().first()
    if row and row.owner_id!=user.id:raise HTTPException(403,'此作用域巡检由其他成员配置，请由创建人修改')
    if not row:
        row=SelectorAuditSchedule(project_id=body.project_id,sub_product=scope,owner_id=user.id,device_id=body.device_id)
        db.add(row)
        try:db.flush()
        except IntegrityError:db.rollback();raise HTTPException(409,'巡检配置已由其他请求创建，请刷新重试')
    return row

@router.get('')
def state(project_id:int,sub_product:str='',db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    assert_project_role(db,user,project_id,READ)
    row=db.query(SelectorAuditSchedule).filter_by(project_id=project_id,sub_product=sub_product).first()
    runs=[]
    if row:
        for run in db.query(SelectorAuditRun).filter_by(schedule_id=row.id).order_by(SelectorAuditRun.id.desc()).limit(20):
            p=db.get(ProbeRequest,run.probe_id)
            runs.append({'id':run.id,'probe_id':p.id,'status':p.status,'error':p.error,'trigger':run.trigger,
                'created_at':run.created_at.isoformat()+'Z','summary':json.loads(run.summary)})
    return ok({'config':config_out(row),'runs':runs,'pages':[{'id':p['id'],'label':p['label']} for p in service.pages()]})

@router.put('/schedule')
def configure(body:Config,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    row=obtain(db,user,body)
    row.device_id=body.device_id;row.enabled=body.enabled;row.daily_time=body.daily_time
    row.next_run_at=service.next_daily(body.daily_time) if body.enabled else None
    row.error='';db.commit();return ok(config_out(row))

@router.post('/run')
def run_now(body:Config,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    row=obtain(db,user,body);row.device_id=body.device_id
    p=service.enqueue(db,row);db.commit();return ok({'probe_id':p.id})

@router.post('/{probe_id}/cancel')
def cancel(probe_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    run=db.query(SelectorAuditRun).filter_by(probe_id=probe_id).first()
    if not run:raise HTTPException(404,'巡检不存在')
    p=db.get(ProbeRequest,probe_id);assert_project_role(db,user,p.project_id,WRITE)
    if p.created_by!=user.id:raise HTTPException(403,'只能取消自己的巡检')
    if p.status=='pending':p.status='failed';p.error='用户取消待执行巡检'
    elif p.status=='running':
        params=json.loads(p.params);params['cancel_requested']=True;p.params=json.dumps(params,ensure_ascii=False)
    db.commit();return ok({'status':p.status})
