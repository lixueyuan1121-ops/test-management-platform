"""分片并行生成 端到端验证(**真调 claude CLI**,非自测,不进回归)。
运行: cd backend && python -m scripts.verify_shard_live

打印:各片墙钟/条数、合并去重后总数、kind 分布、e2e steps 粒度抽样、场景组合抽样。
用于人工确认 prompt 改造是否真的产出了想要的东西(自测只能锁 prompt 文本,锁不住模型行为)。
"""
import json
import time

from app.services import generators
from app.services.claude_runner import plan_shards
from app.services.generators.sharded import generate_sharded

REQ = """纳米AI客户端首页对话功能：
用户在首页底部输入框输入问题，可点击「@专家」按钮从浮层中选择一个专家，
选中后输入框上方出现该专家的 Chip 标签，点击发送后进入该专家的会话页并得到回复。
专家分为已安装与未安装两种状态：选择未安装的专家并发送时，会先弹出安装确认、
显示安装进度，安装完成后自动进入该专家会话并把刚才输入的问题带过去发出。
输入框为空时发送按钮不可点击；单条消息最多 2000 字。"""


def main():
    engine = generators.get_provider("claude")
    if not engine.is_available():
        print("SKIP: claude 引擎不可用")
        return
    shards = plan_shards(None)
    print(f"排产分片({len(shards)}):{[s['id'] for s in shards]}\n")

    t0 = time.monotonic()
    res = generate_sharded(engine, REQ, project_id=None, shards=shards)
    wall = time.monotonic() - t0

    print(f"=== 墙钟 {wall:.1f}s ===")
    for st in res["shard_stats"]:
        print(f"  {st['shard']:<10} {st['count']:>3} 条  {st['error'] or ''}")
    print(f"合计 {len(res['cases'])} 条(去重丢弃 {res['dropped_dup']} 条)")
    print(f"meta: {res['meta']}\n")

    kinds = {}
    for c in res["cases"]:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"kind 分布:{kinds}\n")

    e2es = [c for c in res["cases"] if c["kind"] == "e2e"]
    if e2es:
        c = max(e2es, key=lambda x: len(x["steps"] or ""))
        print("=== e2e steps 抽样(最长的一条)===")
        print(f"[{c['priority']}] {c['title']}")
        print(c["steps"])
        print(f"预期:{c['expected']}")
        sc = json.loads(c["script"]) if c["script"] else []
        print(f"script {len(sc)} 步:{[s['action'] for s in sc]}\n")

    pre = [c for c in res["cases"] if "前置" in (c["steps"] or "")]
    print(f"=== 含「前置：」的场景分支用例 {len(pre)} 条 ===")
    for c in pre[:3]:
        print(f"  [{c['kind']}] {c['title']}")
        print(f"     {(c['steps'] or '').splitlines()[0]}")
    print()
    apis = [c for c in res["cases"] if c["kind"] == "api"]
    print(f"=== api 用例 {len(apis)} 条 ===")
    for c in apis[:3]:
        print(f"  {c['title']}  script={'有' if c['script'] else '无'}")


if __name__ == "__main__":
    main()
