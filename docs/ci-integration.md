# CI/CD 集成：流水线触发测试计划 + 质量门禁

平台提供两个无人值守钩子端点，让流水线做到「发版前自动回归、不达标不上线」。

## 前置配置

1. 服务端 `backend/.env` 配置 `CI_HOOK_TOKEN=<长随机串>`（留空则钩子整条关闭）并重启后端。
2. 平台上建好测试计划（功能测试 → 测试计划），把要跑的回归用例加入计划、指定执行设备。
3. 流水线侧把 token 存入密钥管理，请求带 `X-CI-Token` 头。

## 一、触发计划执行

```bash
curl -sS -X POST "$PLATFORM/api/hooks/run-plan" \
  -H "X-CI-Token: $CI_HOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_code": "nami-work",
    "plan_name": "发版前冒烟",
    "note": "pipeline #1234 commit abc123"
  }'
```

- 定位计划：给 `plan_id`，或 `project_code + plan_name`（推荐，流水线配置里可读）。
- 可选 `runner` 临时换执行机；`note` 写流水线号/commit 便于留痕（记服务端日志）。
- 成功返回：`data.batch_id`（后续轮询门禁用）、`run_ids`、`case_count`。
- 计划内 manual 用例自动跳过；无可自动化用例返回 400。

## 二、轮询质量门禁

```bash
curl -sS "$PLATFORM/api/hooks/gate?batch_id=$BATCH_ID&min_pass_rate=100" \
  -H "X-CI-Token: $CI_HOOK_TOKEN"
```

返回 `data.gate`：

| gate | 含义 | 流水线动作 |
|---|---|---|
| `pending` | 批次还有排队/执行中 | sleep 后继续轮询 |
| `pass` | 已完成且通过率 ≥ min_pass_rate | 放行 |
| `fail` | 已完成且未达标 | 阻断，`data.failures` 有失败用例摘要（标题/fail_kind/原因） |

口径说明：
- `pass_rate = passed / (passed + failed)`，与平台「执行结果」页一致。
- `blocked`（选择器/环境阻塞）默认**不**拖垮门禁；要求更严时加 `strict=1` 让 blocked 也算失败。
- `min_pass_rate` 默认 100（零失败才放行），可按计划风险放宽如 `min_pass_rate=90`。

## 三、流水线脚本样例（轮询直到出结果）

```bash
BATCH_ID=$(curl -sS -X POST "$PLATFORM/api/hooks/run-plan" \
  -H "X-CI-Token: $CI_HOOK_TOKEN" -H "Content-Type: application/json" \
  -d "{\"project_code\":\"nami-work\",\"plan_name\":\"发版前冒烟\",\"note\":\"$CI_PIPELINE_ID\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['batch_id'])")

for i in $(seq 1 60); do   # 最多等 30 分钟
  GATE=$(curl -sS "$PLATFORM/api/hooks/gate?batch_id=$BATCH_ID" -H "X-CI-Token: $CI_HOOK_TOKEN" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['gate'])")
  [ "$GATE" != "pending" ] && break
  sleep 30
done

echo "gate=$GATE batch=$BATCH_ID"
[ "$GATE" = "pass" ] || { echo "质量门禁未通过，阻断发布"; exit 1; }
```

## 其他说明

- CI 触发的批次 `trigger=ci`，测试计划页「执行历史」可见来源；批次完成且有失败会推飞书告警
  （与定时批次同策略，需配置 `FEISHU_WEBHOOK_URL`）。
- 执行机离线时批次会停在 `pending`——门禁一直 `pending` 到轮询超时，流水线按超时失败处理即可；
  平台侧另有设备离线飞书告警 + exec_run 2 小时超时自动收口兜底。
- 超时兜底后 run 记 `failed`（reason 带「自动收口」），门禁会转 `fail`，不会永远 pending。
