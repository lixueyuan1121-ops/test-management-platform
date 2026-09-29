"""Device DOM audit: persistent schedule, idempotent reconciliation and conservative retirement."""
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import update
from app.models import SelectorAuditSchedule, SelectorAuditRun, SelectorAuditObservation, SelectorKey, ProbeRequest, RunnerDevice, User
from app.services.selector_history import revision, remember
from app.services.selector_ranking import candidate_identity, normalize_candidate, is_active_candidate
from app.services.selector_description import compose_description, split_description, normalize_description

log = logging.getLogger('test_platform')
_stop = threading.Event()
_thread = None

def pages():
    return json.loads((Path(__file__).resolve().parents[3] / 'tools/qalab-runner/dom-audit-pages.json').read_text(encoding='utf-8'))

def next_daily(clock, now=None):
    now = now or datetime.utcnow()
    local = now + timedelta(hours=8)
    h, m = map(int, clock.split(':'))
    due = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due - timedelta(hours=8)

def enqueue(db, schedule, trigger='manual'):
    from app.services.selector_device import owned_device, assert_idle
    from app.core.deps import assert_project_role
    from app.core.enums import ProjectRole
    owner = db.get(User, schedule.owner_id)
    if not owner:
        raise HTTPException(400, '巡检创建人已不存在')
    assert_project_role(db, owner, schedule.project_id, (ProjectRole.admin, ProjectRole.member))
    device = owned_device(db, owner, '', schedule.device_id)
    assert_idle(db, device.id)
    # Locking the device also serializes multiple scopes on MySQL.
    db.query(ProbeRequest).filter(ProbeRequest.id.in_(db.query(SelectorAuditRun.probe_id)),
        ProbeRequest.runner_device_id==device.id, ProbeRequest.status=='pending',
        ProbeRequest.created_at < datetime.utcnow()-timedelta(minutes=30)).update(
            {ProbeRequest.status:'failed',ProbeRequest.error:'设备 30 分钟未领取巡检，请更新并启动 Runner'},synchronize_session=False)
    pending = db.query(ProbeRequest).filter(ProbeRequest.runner_device_id == device.id, ProbeRequest.status.in_(['pending', 'running'])).first()
    if pending:
        raise HTTPException(409, '设备已有待执行或执行中的探测，请等待完成')
    if not device.last_seen_at or datetime.utcnow() - device.last_seen_at > timedelta(minutes=3):
        raise HTTPException(409, '设备离线，请启动新版 Runner 后重试')
    r = ProbeRequest(project_id=schedule.project_id, sub_product=schedule.sub_product, runner=device.runner_id,
        runner_device_id=device.id, created_by=owner.id, status='pending', params=json.dumps({'mode':'dom_audit', 'version':1, 'pages':pages()}, ensure_ascii=False))
    db.add(r); db.flush()
    baseline = {str(row.id):revision(row) for row in db.query(SelectorKey).filter_by(project_id=schedule.project_id,sub_product=schedule.sub_product).all()}
    db.add(SelectorAuditRun(schedule_id=schedule.id,probe_id=r.id,trigger=trigger,snapshot=json.dumps(baseline)))
    schedule.last_probe_id = r.id
    schedule.error = ''
    return r


def element_description(spec, element, primary, collection, *, legacy=False):
    kinds = {'button':'按钮','input':'输入框','textarea':'输入框','select':'选择框','a':'链接',
             'tab':'选项卡','heading':'标题','container':'容器'}
    kind = element.get('tag') if legacy else element.get('control_kind') or element.get('tag')
    label = (primary['value'] if collection else element.get('text') or primary['value'])[:65]
    label += kinds.get(kind, '控件') + ('（列表集合，多个）' if collection else '')
    return compose_description([spec['tab'],spec['label'],spec['label']+'浏览',label])


def reconcile(db, probe, result):
    run = db.query(SelectorAuditRun).filter_by(probe_id=probe.id).with_for_update().first()
    if not run:
        raise HTTPException(409, '巡检记录不存在，禁止同步')
    if result.get('audit_version') != 1 or not isinstance(result.get('pages'), list):
        raise HTTPException(422, '设备 Runner 尚不支持 DOM 巡检，请更新 Runner')
    # Old versions did not remove observation rows when a selector was deleted.
    # Purge these before SQLite can reuse an ID for a newly discovered selector.
    db.query(SelectorAuditObservation).filter(~SelectorAuditObservation.key_id.in_(db.query(SelectorKey.id))).delete(synchronize_session=False)
    baseline = json.loads(run.snapshot)
    rows = db.query(SelectorKey).filter_by(project_id=probe.project_id, sub_product=probe.sub_product).with_for_update().all()
    index = {}
    for row in rows:
        for c in json.loads(row.candidates or '[]'):
            if is_active_candidate(c): index.setdefault((row.frame,candidate_identity(c)),set()).add(row.id)
    by_id = {r.id:r for r in rows}
    row_pages = {}
    observations = {(o.key_id,o.page_token):o for o in db.query(SelectorAuditObservation).filter(SelectorAuditObservation.key_id.in_(list(by_id))).all()}
    for observation in observations.values():
        row_pages.setdefault(observation.key_id,set()).add(observation.page_token)
    def on_page(rid,spec):
        return by_id[rid].page==spec['label'] or spec['id'] in row_pages.get(rid,set())
    changed = set(); seen = set(); complete = set()
    counts = dict(new=0,updated=0,retired=0,superseded=0,unchanged=0,conflicts=0,unverified=0,scanned=0,merged=0,collections=0,xpath=0,css=0,testid=0)
    registered = set()
    details = []
    manifest = {p['id']:p for p in pages()}
    for report in result['pages']:
        spec = manifest.get(report.get('id'))
        if not spec: continue
        if report.get('complete') is True: complete.add(spec['id'])
        if not report.get('ready'): continue
        for el in report.get('elements', [])[:2000]:
            counts['scanned']+=1
            raw_frame=el.get('frame')
            aliases={raw_frame} | {alias for alias,target in (report.get('frameAliases') or {}).items() if target==raw_frame}
            # A repeated/non-unique control is still present; ambiguity is not obsolescence.
            for candidate in el.get('observed',[])[:6]:
                c=normalize_candidate(candidate)
                if c:
                    for name in aliases:
                        seen.update(rid for rid in index.get((name,candidate_identity(c)),set()) if on_page(rid,spec) or c['by']=='testid')
            collection = bool(el.get('collections'))
            raw_candidates = el.get('collections') if collection else el.get('verified',[])
            candidates = [normalize_candidate(c) for c in raw_candidates[:6]]
            candidates = [c for c in candidates if c and c['by'] in ('testid','css','xpath')]
            if not candidates: counts['unverified']+=1; continue
            frame = el.get('frame')
            if not isinstance(frame,str) or not frame or len(frame)>2048: continue
            # Dynamic IDs are data instances, not reusable page controls.
            stable = next((c for c in candidates if c['by']=='testid' and not re.search(r'(?:[0-9a-f]{16,}|\d{6,}|chat-sidebar-item-)',c['value'],re.I)),None)
            frame_names = {frame} | {alias for alias,target in (report.get('frameAliases') or {}).items() if target==frame}
            primary = stable or next((c for c in candidates if c['by']=='css'), candidates[0])
            # CSS/XPath and collections are page scoped. A positional fallback may
            # never join controls merely because two pages have the same DOM shape.
            global_identity = bool(stable) and not collection
            matches = set().union(*(index.get((name,candidate_identity(primary)),set()) for name in frame_names))
            matches = {rid for rid in matches if (global_identity or on_page(rid,spec))
                and any((c.get('src')=='audit_collection')==collection and candidate_identity(c)==candidate_identity(primary)
                    for c in json.loads(by_id[rid].candidates or '[]'))}
            if len(matches)>1: counts['conflicts']+=1; continue
            created = False
            if matches:
                row = by_id[next(iter(matches))]
                if row.id not in changed and baseline.get(str(row.id)) != revision(row): counts['conflicts']+=1; continue
            else:
                # Hash the semantic locator + frame, independent of navigation order and user.
                scope = '' if global_identity else spec['id'] + ('/collection' if collection else '/single')
                key = 'dom_' + re.sub(r'[^A-Za-z0-9_]', '_',primary['value'])[:40] + '_' + hashlib.sha256((scope+frame+candidate_identity(primary)).encode()).hexdigest()[:10]
                if any(r.key==key for r in by_id.values()): counts['conflicts']+=1; continue
                desc = element_description(spec, el, primary, collection)
                row = SelectorKey(project_id=probe.project_id,sub_product=probe.sub_product,key=key,frame=frame,page=spec['label'],desc=desc,candidates='[]',change_status='new',updated_by=probe.created_by)
                db.add(row);db.flush();by_id[row.id]=row;created=True
            existing=json.loads(row.candidates or '[]')
            ids={candidate_identity(c) for c in existing if is_active_candidate(c)}
            additions=[c for c in candidates if candidate_identity(c) not in ids]
            # Revisited legacy rows need the same description contract as new rows.
            # Valid user-authored descriptions and grouping are preserved.
            desc = row.desc
            if not split_description(desc):
                desc = normalize_description(desc, row.page or spec['label'], row.key,
                    navigation=spec['tab'], scene=(row.page or spec['label'])+'浏览')
            # Upgrade only an unchanged audit-generated label, not a user-authored description.
            if (el.get('control_kind') and any(o.key_id==row.id and o.managed for o in observations.values())
                    and desc == element_description(spec, el, primary, collection, legacy=True)):
                desc = element_description(spec, el, primary, collection)
            description_changed = desc != row.desc
            if created or additions or description_changed or row.change_status=='retired':
                if not created: remember(db,row,probe.created_by)
                row.desc = desc
                row.candidates=json.dumps((additions+existing)[:16],ensure_ascii=False)
                row.change_status='new' if created else 'updated'
                row.updated_at=datetime.utcnow();row.updated_by=probe.created_by
                counts['new' if created else 'updated']+=1;changed.add(row.id)
                details.append({'key':row.key,'status':row.change_status,'page':spec['label']})
            if row.id in registered: counts['merged']+=1
            else:
                registered.add(row.id)
                counts['collections' if collection else primary['by']]+=1
            for c in candidates: index.setdefault((frame,candidate_identity(c)),set()).add(row.id)
            seen.add(row.id)
            if collection and isinstance(el.get('member_xpath'),str):
                member={'by':'xpath','value':el['member_xpath']}
                old_ids=set().union(*(index.get((name,candidate_identity(member)),set()) for name in frame_names))
                for old_id in old_ids:
                    old=by_id[old_id]
                    old_cands=json.loads(old.candidates or '[]')
                    # Preserve manually maintained/fallback chains and concurrent edits.
                    if old_id==row.id or not on_page(old_id,spec) or old.change_status=='retired': continue
                    if baseline.get(str(old_id))!=revision(old): continue
                    if not old_cands or not all(c.get('src')=='audit_xpath' for c in old_cands): continue
                    if not any(o.key_id==old_id and o.managed for o in observations.values()): continue
                    remember(db,old,probe.created_by);old.change_status='retired';old.updated_at=datetime.utcnow();old.updated_by=probe.created_by
                    changed.add(old_id);counts['retired']+=1;counts['superseded']+=1
                    details.append({'key':old.key,'status':'retired','page':old.page,'reason':'已归并为列表集合','replacement':row.key})
            row_pages.setdefault(row.id,set()).add(spec['id'])
            identity=(row.id,spec['id'])
            ob=observations.get(identity)
            if ob is None:
                ob=SelectorAuditObservation(key_id=row.id,page_token=spec['id'],misses=0,managed=created)
                db.add(ob);observations[identity]=ob
            else: ob.misses=0
    # Only controls created by the audit can retire automatically, after two complete visits.
    # Unknown/failed pages, inherited rows and manually maintained keys remain untouched.
    for ob in observations.values():
        row=by_id[ob.key_id]
        if ob.key_id in seen: ob.misses=0;continue
        if ob.managed and ob.page_token in complete and baseline.get(str(row.id))==revision(row):
            ob.misses+=1
            if ob.misses>=2 and row.change_status!='retired':
                remember(db,row,probe.created_by);row.change_status='retired';row.updated_at=datetime.utcnow();row.updated_by=probe.created_by
                counts['retired']+=1;details.append({'key':row.key,'status':'retired','page':row.page})
    counts['unchanged']=len(registered-changed)
    counts['registered']=len(registered)
    summary={**counts,'pages':[{k:p.get(k) for k in ('id','label','ready','complete','error','count')} for p in result['pages']], 'changes':details}
    run.summary=json.dumps(summary,ensure_ascii=False)
    return summary

def tick():
    from app.db.session import SessionLocal
    now=datetime.utcnow()
    with SessionLocal() as db:
        due=db.query(SelectorAuditSchedule.id).filter(SelectorAuditSchedule.enabled.is_(True),SelectorAuditSchedule.next_run_at<=now).all()
    for (sid,) in due:
        with SessionLocal() as db:
            schedule=db.get(SelectorAuditSchedule,sid)
            due_at=schedule.next_run_at
            # Atomic lease: safe if two app processes tick together.
            claimed=db.execute(update(SelectorAuditSchedule).where(SelectorAuditSchedule.id==sid,SelectorAuditSchedule.enabled.is_(True),SelectorAuditSchedule.next_run_at==due_at).values(next_run_at=next_daily(schedule.daily_time,now)))
            if not claimed.rowcount: db.rollback();continue
            try:
                enqueue(db,schedule,'daily')
                db.commit()
            except Exception as exc:
                db.rollback()
                # Offline/busy devices retry in five minutes, without accumulating queued jobs.
                db.execute(update(SelectorAuditSchedule).where(SelectorAuditSchedule.id==sid,SelectorAuditSchedule.next_run_at==due_at).values(error=str(getattr(exc,'detail',exc))[:500],next_run_at=now+timedelta(minutes=5)))
                db.commit()

def start():
    global _thread
    if _thread and _thread.is_alive(): return
    _stop.clear()
    def loop():
        while not _stop.wait(30):
            try: tick()
            except Exception: log.exception('DOM audit scheduler failed')
    _thread=threading.Thread(target=loop,name='selector-audit-scheduler',daemon=True);_thread.start()

def stop():
    _stop.set()
    if _thread: _thread.join(timeout=3)
