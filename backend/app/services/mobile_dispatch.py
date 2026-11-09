"""Verified native dialog scenarios, signed-device presence and immutable dispatch snapshots."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from app.services.mobile_script import stored_script
from app.models import AiTask, ExecRun, Project, RunnerDevice, TestCase, TestPlanCase


SCENARIOS = {
    "send": {"title": "发送消息并接收回复", "steps": "新建会话，输入文本并发送，等待本轮回复完成。",
             "expected": "本轮用户消息可见，产生新的非空助手回复，输入框恢复可用。"},
    "followup": {"title": "在同一会话内追问", "steps": "新建会话，发送第一条消息并等待完成，在该会话追问。",
                 "expected": "原消息保留，第二条用户消息与本轮新的非空回复可见。"},
    "stop": {"title": "停止正在生成的回复", "steps": "新建会话并发送文本，在回复生成时点击停止。",
             "expected": "生成状态结束，输入框恢复可用。"},
    "history": {"title": "从历史重新打开会话", "steps": "新建会话并完成一轮对话，离开会话，从历史记录重开该会话。",
                "expected": "显示原会话的用户消息和助手回复。"},
}
PLATFORMS = ("android", "ios")


class MobilePresence(BaseModel):
    platform: Literal["android", "ios"]
    device_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_id: str = Field(min_length=1, max_length=255)
    build: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    os_version: str = Field(min_length=1, max_length=128)
    physical: Literal[True]
    online: bool


def device_target(device):
    if not device or not device.mobile_presence:
        return None
    target = json.loads(device.mobile_presence)
    target.update(runner_id=device.runner_id, online=bool(target["online"] and
                  device.platform == target["platform"] and device.mobile_seen_at and
                  device.mobile_seen_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=180)))
    return target


def record_presence(db, device, body):
    if not device:
        raise HTTPException(403, "真机上报须使用设备专属令牌")
    device = db.query(RunnerDevice).filter_by(id=device.id).with_for_update().populate_existing().one()
    if device.platform != body.platform or device.mobile_device_id not in (None, body.device_id):
        raise HTTPException(409, "注册的平台或绑定的物理手机不匹配")
    device.mobile_device_id = body.device_id
    device.mobile_presence = json.dumps(body.model_dump(), ensure_ascii=False)
    device.mobile_seen_at = device.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if body.online:
        device.last_exec_at = device.mobile_seen_at
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该手机已绑定其他执行器")
    return device_target(device)


def validate_config(targets, builds=None):
    if not isinstance(targets, dict) or set(targets) != set(PLATFORMS):
        raise HTTPException(422, "双真机执行须同时指定 Android 和 iOS 设备")
    for runner in targets.values():
        if not isinstance(runner, str) or not runner.strip() or runner.strip() != runner or len(runner) > 64:
            raise HTTPException(422, "请填写已登记的真机执行器标识")
    if not isinstance(builds or {}, dict) or any(p not in PLATFORMS or not isinstance(v, str)
            or not v.strip() or len(v) > 128 for p, v in (builds or {}).items()):
        raise HTTPException(422, "目标构建号须按 android、ios 提供字符串")


def owned_targets(db, owner_id, targets, builds=None, *, require_online=True):
    validate_config(targets, builds)
    devices = []
    for platform in PLATFORMS:
        device = db.query(RunnerDevice).filter_by(owner_id=owner_id, runner_id=targets[platform],
                                                  platform=platform).first()
        if not device:
            raise HTTPException(400, f"{platform} 设备未登记或不属于绑定用户")
        if require_online:
            target = device_target(device)
            if not target or not target["online"]:
                raise HTTPException(409, f"{platform} 真机离线或尚未探测到已安装的 App")
            if (builds or {}).get(platform) and target["build"] != builds[platform]:
                raise HTTPException(409, f"{platform} 已安装构建号与本次要求不一致")
        devices.append(device)
    return devices


def scenario_hash(tc, scenario):
    data = [scenario, tc.title, tc.precondition, tc.steps, tc.expected, tc.exec_kind, tc.script]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def bind_scenario(tc, scenario):
    if scenario == "script":
        try:
            stored_script(tc)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif scenario not in SCENARIOS:
        raise HTTPException(422, "不支持的真机对话场景")
    tc.mobile_scenario = scenario
    tc.mobile_case_hash = scenario_hash(tc, scenario)


def mobile_payloads(db, tc, owner_id, mobile_targets, mobile_builds=None, *, scenario=None):
    if tc.exec_kind not in ("gui", "e2e"):
        raise HTTPException(422, f"用例 {tc.id} 需为已确认的真机 GUI 场景")
    selected = scenario or tc.mobile_scenario
    if (selected != "script" and selected not in SCENARIOS) or selected != tc.mobile_scenario or tc.mobile_case_hash != scenario_hash(tc, selected):
        raise HTTPException(422, f"用例 {tc.id} 缺少与当前步骤一致的真机场景映射，请重新核对")
    extra = {}
    if selected == "script":
        try:
            extra["mobile_script"] = stored_script(tc)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return [(d, {**extra, "test_case_id": tc.id, "title": tc.title, "steps": tc.steps, "expected": tc.expected,
                 "precondition": tc.precondition, "project_id": tc.project_id, "mobile_target": device_target(d),
                 "mobile_scenario": selected}) for d in owned_targets(db, owner_id, mobile_targets, mobile_builds)]


def matches_snapshot(probe, target):
    return bool(probe and target and probe["online"] and all(probe[k] == target.get(k)
                for k in ("platform", "runner_id", "device_id", "app_id", "build")))


def block_lost_targets(db, db_time):
    """Queued work must not wait indefinitely for a different build or a lost phone."""
    from app.services.run_activity import live_run_filter

    work = db.query(ExecRun).filter(ExecRun.native_mobile.is_(True), ExecRun.status == "pending").all()
    live = {did for (did,) in db.query(ExecRun.runner_device_id).filter(
        ExecRun.native_mobile.is_(True), live_run_filter(ExecRun, db_time)).distinct()} if work else set()
    blocked = []
    for row in work:
        device = db.get(RunnerDevice, row.runner_device_id) if row.runner_device_id else None
        probe = device_target(device)
        target = json.loads(row.payload)["mobile_target"]
        # A live task can hold the driver long enough for the previous idle probe to expire.
        if device and device.id in live and probe and not probe["online"] and json.loads(device.mobile_presence)["online"]:
            continue
        if matches_snapshot(probe, target):
            continue
        changed = db.query(ExecRun).filter_by(id=row.id, status="pending").update(
            {ExecRun.status: "blocked", ExecRun.verdict: "blocked", ExecRun.fail_kind: "selector",
             ExecRun.finished_at: func.now(), ExecRun.reason: "真机已离线或当前 App 构建与派单快照不一致"},
            synchronize_session=False)
        if changed:
            blocked.append(row)
    return blocked


def assert_matches(device, target):
    if not matches_snapshot(device_target(device), target):
        raise HTTPException(409, "真机离线或手机、App、构建号与派单快照不匹配")


def seed_smoke(db, plan, user_id):
    # Serialize project-level reuse, including requests for different plans on SQLite and MySQL.
    db.execute(update(Project).where(Project.id == plan.project_id).values(id=Project.id))
    task = db.query(AiTask).filter_by(project_id=plan.project_id, kind="mobile_dialog_smoke").first()
    if not task:
        task = AiTask(project_id=plan.project_id, user_id=user_id, kind="mobile_dialog_smoke",
                      provider="builtin", input_type="text", status="done", case_count=len(SCENARIOS))
        db.add(task)
        db.flush()
    existing = {tc.mobile_scenario: tc for tc in db.query(TestCase).filter_by(ai_task_id=task.id)}
    linked = {cid for (cid,) in db.query(TestPlanCase.case_id).filter_by(plan_id=plan.id)}
    for scenario, spec in SCENARIOS.items():
        tc = existing.get(scenario)
        if not tc:
            tc = TestCase(project_id=plan.project_id, ai_task_id=task.id, provider="builtin", platform="mobile",
                          precondition="测试账号已登录且处于 App 主界面", exec_kind="gui", category="功能",
                          priority="P0", page="对话", adopted=True, review_status="adopted", **spec)
            bind_scenario(tc, scenario)
            db.add(tc)
            db.flush()
        if tc.id not in linked:
            db.add(TestPlanCase(plan_id=plan.id, case_id=tc.id))
