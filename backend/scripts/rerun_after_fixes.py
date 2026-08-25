"""修复后按用例批量「重生 script + 重新下发执行」的操作脚本(走线上 HTTP API,本机即可跑)。

配合 fix/exec-assert-classify-reset-heal 分支:后端部署新版后,对写反断言的用例重生 script
(改用 assert_absent/negate),再整批重新下发,让已更新的 runner 用新逻辑执行。

鉴权:enqueue/gen-script 需用户 JWT(runner token 不行)。凭据二选一:
  - 环境变量 PLATFORM_USER / PLATFORM_PASS
  - 命令行 --username / --password

用法(在 backend 目录):
  # 1) 重生 6 条写反断言用例 + 整批重新下发到 lili-win
  python -m scripts.rerun_after_fixes --base-url https://qalab.claw.qihoo.net \
      --project 1 --runner lili-win \
      --regen 673,662,660,680,670,665 \
      --enqueue 673,662,660,680,674,666,659,672,684,678,676,675,668,671,669,667,664,661,663,681,682

  # 只重生不下发:去掉 --enqueue;只下发不重生:去掉 --regen
  # --dry 只打印将要做什么,不实际调用
"""
import argparse
import os
import sys

import requests


def _unwrap(resp: requests.Response):
    """解 {code,msg,data} 信封;code!=0 或 HTTP 错误 → 抛。"""
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict) or body.get("code") != 0:
        raise RuntimeError(f"接口返回失败: {body}")
    return body.get("data")


def _ids(s: str) -> list[int]:
    return [int(x) for x in (s or "").replace("，", ",").split(",") if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="修复后重生 script + 重新下发执行")
    ap.add_argument("--base-url", required=True, help="平台后端站点根,如 https://qalab.claw.qihoo.net")
    ap.add_argument("--username", default=os.environ.get("PLATFORM_USER", ""))
    ap.add_argument("--password", default=os.environ.get("PLATFORM_PASS", ""))
    ap.add_argument("--project", type=int, required=True, help="项目 id(如 1)")
    ap.add_argument("--runner", default="lili-win", help="下发到的执行机 id")
    ap.add_argument("--regen", default="", help="要重生 script 的 test_case id(逗号分隔)")
    ap.add_argument("--enqueue", default="", help="要重新下发的 test_case id(逗号分隔)")
    ap.add_argument("--dry", action="store_true", help="只打印计划,不实际调用")
    args = ap.parse_args()

    if not args.username or not args.password:
        print("缺凭据:设 PLATFORM_USER/PLATFORM_PASS 或传 --username/--password", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    regen_ids, enq_ids = _ids(args.regen), _ids(args.enqueue)
    print(f"目标: base={base} project={args.project} runner={args.runner}")
    print(f"  重生 {len(regen_ids)} 条: {regen_ids}")
    print(f"  下发 {len(enq_ids)} 条: {enq_ids}")
    if args.dry:
        print("(--dry:不实际调用)")
        return 0

    s = requests.Session()
    token = _unwrap(s.post(f"{base}/api/auth/login",
                           json={"username": args.username, "password": args.password}))["access_token"]
    s.headers["Authorization"] = f"Bearer {token}"
    print("登录成功")

    ok, fail = 0, 0
    for cid in regen_ids:
        try:
            _unwrap(s.post(f"{base}/api/ai/testcases/{cid}/gen-script", timeout=180))
            ok += 1; print(f"  重生 {cid} ✓")
        except Exception as e:  # noqa: BLE001
            fail += 1; print(f"  重生 {cid} ✗ {e}")
    if regen_ids:
        print(f"重生完成: {ok} 成功 / {fail} 失败")

    if enq_ids:
        data = _unwrap(s.post(f"{base}/api/exec-queue/enqueue-cases",
                              json={"project_id": args.project, "runner": args.runner, "test_case_ids": enq_ids}))
        print(f"下发完成: batch={data.get('batch_id')} run_ids={data.get('run_ids')}")
        print(f"→ 在执行机 {args.runner} 上跑 run.cmd,已更新的 runner 会拉取并用新逻辑执行")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
