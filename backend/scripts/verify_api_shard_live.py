"""api 分片 端到端验证(**真调 claude CLI**,非自测,不进回归)。
运行: cd backend && python -m scripts.verify_api_shard_live

用假契约 monkeypatch _load_api_contract,验证「项目配了 api 契约时 api 用例是否真的恢复产出」,
以及产出的 script 是否能过 _validate_api_script(即能否下发到设备确定性执行)。
"""
import json

from app.services import claude_runner
from app.services.claude_runner import _validate_api_script

FAKE_CONTRACT = {
    "base_url": "https://qa.example.com",
    "contract": """GET  /api/projects            列出项目(query: page,size)
POST /api/projects            创建项目(body: name*, code*, status)
GET  /api/projects/{id}       项目详情
PUT  /api/projects/{id}       更新项目(body: name, status)
DELETE /api/projects/{id}     删除项目
POST /api/auth/login          登录(body: username*, password*) → data.access_token""",
}

REQ = """项目管理接口：支持项目的增删改查。
创建项目时 name 与 code 必填，code 全局唯一（重复返回业务错误码）；name 最长 64 字符。
仅项目管理员可删除项目，普通成员删除返回 403。列表接口支持分页 page/size。"""


def main():
    engine = claude_runner
    if not engine.is_available():
        print("SKIP: claude 引擎不可用")
        return
    # 假装项目已配 api 契约
    claude_runner._load_api_contract = lambda project_id=None: FAKE_CONTRACT

    shards = claude_runner.plan_shards(1)
    print(f"有契约时排产分片({len(shards)}):{[s['id'] for s in shards]}")
    api_shard = next(s for s in shards if s["id"] == "api")

    raw = ""
    for evt in engine.stream_generate(
        REQ, project_id=1,
        prompt_builder=lambda: engine.build_testcase_prompt(REQ, 1, None, api_shard),
    ):
        if evt.get("type") == "delta":
            raw += evt.get("text") or ""
        elif evt.get("type") == "result" and evt.get("text"):
            raw = evt["text"]
        elif evt.get("type") == "error":
            print("ERROR:", evt.get("msg"))

    cases = engine.parse_testcases(raw, project_id=1)
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"\n产出 {len(cases)} 条,kind 分布:{kinds}")

    ok_script = 0
    for c in cases:
        if c["kind"] == "api" and c["script"]:
            _, err = _validate_api_script(json.loads(c["script"]))
            if err is None:
                ok_script += 1
    print(f"api 用例中 script 可下发执行的:{ok_script} 条\n")

    for c in cases[:4]:
        print(f"[{c['priority']}][{c['kind']}] {c['title']}")
        print(f"  steps: {(c['steps'] or '')[:160]}")
        if c["script"]:
            sc = json.loads(c["script"])
            print(f"  script {len(sc)} 步: "
                  + " → ".join(f"{s['request']['method']} {s['request']['path']}" for s in sc))
            print(f"  断言: {[a for s in sc for a in s['asserts']][:3]}")
        print()


if __name__ == "__main__":
    main()
