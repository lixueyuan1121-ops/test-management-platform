# 默认流程：Codex 实测后导入，生成电脑无 Runner

## 1. 平台与场景清单

通过 `qalab.ps1 -Action status -BaseUrl <origin>` 或 `python3 qalab.py status --base-url <origin>` 查询项目。默认线上，按本次账号获取 project_id。生成端不要求注册设备。导入命令自动核对 `GET /api/verified-imports/capabilities` 中的 import_modes 包含 codex，codex_evidence_protocol 为 qalab-codex-v1；旧服务器需先更新，不能改包冒充旧模式。

plan.json：`import_mode: "codex"`、platform、project_id、application、requirement，以及 cases 数组（id、title、acceptance）。不填 runner_device_id。

```sh
node scripts/delivery.mjs init --plan plan.json --manifest delivery.json
```

## 2. Codex 实测与观察包

不要求使用某个 Runner 目录、脚本或本地注册表。Codex 用当前实际可用工具执行并观察，每步实时保存证据。以下是协议结构，不是可直接上传的成功样例；所有选择器、观察和引用必须来自本次真实执行。

顶层：import_mode 固定 codex；project_id；稳定 external_id（8–100位字母数字下划线横线）；requirement；可选 task_name（默认 codex导入用例）、sub_product；cases（1–20条）。不得填其他电脑的 runner_device_id。

每条 case：

- title、category、priority、page、exec_kind(gui/e2e/api)、steps、expected、完整 script。GUI/E2E 参数同平台 DSL；前置导航合入 script，precondition 留空。
- executor 固定 codex；environment 写实际应用、版本/角色等；scope 写本条已覆盖及未执行范围。
- verdict 为 pass 或 fail；真实 finished_at（带时区）、duration_ms。
- report：每条包含 no（从1开始连续）、action（对应 script）、ok（布尔）、observation（实际观察）、evidence_ref（引用下面 artifacts 的 id）。不要记录密码、token 或无关敏感内容。
- 所有业务断言报告包含 check: {actual, expected, pass}。assert_text 的 expected 对应 args.expected 或先前 get_text 实际捕获值；get_text 报告包含 text。可见性实际值是是否可见的布尔值：assert_visible 预期 true，assert_absent 预期 false。失败与实际值矛盾的报告会被拒绝。
- codex_evidence: {version: "qalab-codex-v1", tool: "本次实际使用的工具", artifacts: [{id: "唯一引用", kind: "log/trace/screenshot", reference: "真实工具调用ID或保存的证据地址"}]}。reference 是来源引用，平台不会自动访问 A 的本地文件。需团队访问的截图使用平台支持的附件渠道；不要声称本地路径已上传。

通过报告必须覆盖所有步骤且全部 ok=true。业务失败报告可以是脚本前缀，最后一项必须是实际失败的业务断言，ok=false、check.pass=false、fail_kind=business、error 写具体原因；之前步骤必须成功。完整 script 保留失败之后原计划动作供修复后回归，report 不为未执行步骤编造结果。一个失败断言后先停止该条正式报告；收尾单独记录日志，不伪装为已执行后续业务步骤。

可执行脚本相同的重复尝试保留新旧原始证据；不得改 expected 或 actual 来把产品失败变成通过。连接、权限、定位失败不符合此业务失败契约，记 blocked 并说明原因。

## 3. 静态校验与封存

使用 `script-flow-v1` 时，按 [数据准备契约](data-preparation.md) 记录完整序列、分支跳过和收尾；这是上节“失败仅前缀”的明确例外。执行过的业务失败仍原样保留，未执行主流程标记 previous_failure，收尾单独标记 phase。条件与 phase 参与脚本指纹，至少执行一个业务断言。

Windows `scripts/qalab.ps1 -Action lint -BaseUrl <origin> -PayloadPath observed.json`；macOS `python3 scripts/qalab.py lint --base-url <origin> --payload observed.json`。lint 可在实测前检查草稿，不代表通过。

```sh
node scripts/seal-codex.mjs --input observed.json --output import.json
node scripts/delivery.mjs record --manifest delivery.json --id main --status tested --packet import.json
```

封存器只对已有观察检查协议并计算 script/report 指纹；不启动应用、不生成报告、不执行 Runner。输入需有真实报告与 artifacts；已封存包不允许重新盖章，输出不能覆盖。修改最终脚本后重新实测，保存新的观察记录。不能把临时工具操作与最终不同脚本一并盖章。

tested 代表已有 Codex 实测且可交付，具体结果由 verdict 区分 pass/fail。blocked 或 failed 状态用于无法交付的场景并要求 reason；业务失败但完整可回归的包应登记 tested、verdict=fail，不能漏掉。

## 4. 回填

Windows：
```powershell
& ./scripts/qalab.ps1 -Action validate -BaseUrl https://qalab.claw.qihoo.net -PayloadPath import.json
& ./scripts/qalab.ps1 -Action import -BaseUrl https://qalab.claw.qihoo.net -PayloadPath import.json -ManifestPath delivery.json
```
macOS：
```sh
python3 scripts/qalab.py validate --base-url https://qalab.claw.qihoo.net --payload import.json
python3 scripts/qalab.py import --base-url https://qalab.claw.qihoo.net --payload import.json --manifest delivery.json
```
本地平台时将同一批次所有命令改为用户选定 origin。接口仍是 POST /api/verified-imports/jobs。Codex 模式只检查账号/项目权限、完整脚本/注册 key、实测报告一致性，不检查本机 Runner 或设备归属。平台后台保留原有去重与人工确认流程。

平台新增/复用正式用例，并写入首次 ExecRun，来源 Codex、runner_device_id 为空。业务失败写 failed/fail；通过写 passed/pass。导入“处理失败”表示导入任务异常，与“已入库且实测失败”分开展示。疑似重复需确认的仍可能暂未入库，不宣称必然立即入库。

接收回执后登记，按 [测试报告规范](test-report.md) 生成或更新本地 `report.html` 再结束，不轮询。报告覆盖完整场景清单、逐条结果/未导入原因和已知导入任务链接；无法提交或全部阻塞也必须交付报告。

## 5. B 电脑平台回归

导入完成后，按平台现有的用例下发流程执行，不新增设备选择入口或修改派发逻辑。B 的 Runner 负责连接对应待测应用，使用平台中的完整 script；无需 A 在线、不使用 A 的 token/应用路径/本地文件。首次 Codex 实测不授予“Runner 双次验证”标识。平台回归记录与首次实测记录分别保留。
