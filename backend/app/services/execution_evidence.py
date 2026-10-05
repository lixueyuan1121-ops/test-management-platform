"""Execution evidence is consistency checking, not proof of a trusted client."""
import hashlib
import math
import struct


def fingerprint(value):
    h = hashlib.sha256()
    def visit(v):
        if v is None: h.update(b'n')
        elif isinstance(v, bool): h.update(b't' if v else b'f')
        elif isinstance(v, (int, float)):
            if not math.isfinite(v): raise ValueError('非有限数字不能进入执行证据')
            h.update(b'd' + struct.pack('>d', float(v) if v else 0.0).hex().encode())
        elif isinstance(v, str):
            raw = v.encode('utf-8'); h.update(f's{len(raw)}:'.encode() + raw)
        elif isinstance(v, list):
            h.update(b'[')
            for item in v: visit(item)
            h.update(b']')
        elif isinstance(v, dict):
            h.update(b'{')
            for key in sorted(v, key=lambda k: k.encode('utf-8')): visit(key); visit(v[key])
            h.update(b'}')
        else: raise ValueError('执行证据必须是 JSON 数据')
    visit(value)
    return h.hexdigest()


def normalize_script(script):
    if not isinstance(script, list) or not script: raise ValueError('需要非空平台 script 数组')
    return [dict(action=str(s.get('action') or '').strip(), target=s.get('target') or {},
                 args=s.get('args') or {}, desc=str(s.get('desc') or '')[:200]) for s in script]


def execution_contract(payload):
    return dict(script=normalize_script(payload['script']), precondition=(payload.get('precondition') or '').strip(),
                selector_registry=payload.get('selector_registry') or {})


def validate_evidence(evidence, payload, report):
    if evidence['script_sha256'] != fingerprint(normalize_script(payload['script'])):
        raise ValueError('脚本已改变，与实际执行证据不一致；请重跑最终脚本')
    if evidence['contract_sha256'] != fingerprint(execution_contract(payload)):
        raise ValueError('前置条件或选择器快照与实际执行证据不一致；请重新验证')
    if evidence['report_sha256'] != fingerprint(report):
        raise ValueError('逐步报告与执行证据不一致；请使用 Runner 原始回填包')


def readiness(runs, current_contract):
    """Only consecutive queue passes with unchanged contract AND runtime count.

    Imports, manual corrections and older runners cannot certify regression.
    A later failure or script version change interrupts the consecutive streak.
    """
    import json
    count, runtime, environment = 0, None, None
    for run in runs:
        payload = json.loads(run.payload or '{}')
        if payload.get('verified_import'): continue
        if getattr(run.status, 'value', run.status) in ('pending', 'running'): continue
        ev = payload.get('execution_evidence') or {}
        if (run.verdict != 'pass' or (run.reason or '').startswith('[人工纠偏]')
                or ev.get('contract_sha256') != current_contract or ev.get('mode') != 'script'
                or not ev.get('strict_replay') or not ev.get('reset') or not ev.get('runtime_sha256')): break
        if runtime and runtime != ev['runtime_sha256']: break
        current_env = (ev.get('platform'), ev.get('node_version'))
        if environment and environment != current_env: break
        environment = current_env
        runtime = ev['runtime_sha256']; count += 1
        if count == 2: break
    return {'state': 'verified' if count >= 2 else 'pending', 'consecutive_passes': count,
            'required_passes': 2, 'contract_sha256': current_contract, 'runtime_sha256': runtime,
            'label': '当前版本已通过回归验证' if count >= 2 else '当前版本待回归验证'}


def case_readiness(db, tc):
    from app.api.exec_queue import _payload_of
    from app.models import ExecRun
    try: payload = _payload_of(tc, db)
    except (ValueError, TypeError, AttributeError): payload = {}
    current = payload.get('execution_contract_sha256')
    if not current:
        return {'state': 'pending', 'label': '缺少完整脚本，待回归验证', 'consecutive_passes': 0, 'required_passes': 2}
    from sqlalchemy.orm import defer
    runs = (db.query(ExecRun).options(defer(ExecRun.report)).filter(ExecRun.test_case_id == tc.id, ExecRun.project_id == tc.project_id)
            .order_by(ExecRun.id.desc()).limit(50).all())
    return readiness(runs, current)
