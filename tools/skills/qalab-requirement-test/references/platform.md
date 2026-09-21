# 平台接入与脚本约定

## 准备
Windows 需要 PowerShell 5.1+；登录脚本只使用内置能力。其他系统可以通过受支持的浏览器登录或使用正常获取的用户 bearer token 调用相同 API，不共享用户凭据。
线上项目、用户和执行机与 localhost 分离。GET /api/projects 查询有权访问的项目；GET /api/devices 查询自己的设备；GET /api/auth/me 验证身份。
线上缺少 POST /api/verified-imports 时需要发布仓库配套接口（backend/app/api/verified_import.py、schemas/verified_import.py 及 router.py 注册）；不需要数据库迁移。

## 导入契约
POST /api/verified-imports，Authorization: Bearer 用户 access token。仅项目 admin/member 可写，runner_device_id 必须属于当前用户。JSON:
- project_id, runner_device_id：线上真实 ID。
- external_id：此次验证的稳定唯一标识，8–100 位字母数字下划线或短横线。相同 ID+内容重复调用复用记录，内容不同返回 409。
- requirement：需求标题，最多 512 字符。
- cases：1–20 条通过的用例，每条字段：
  - title, category（功能/边界等）, priority（P0–P3）, page。
  - exec_kind（gui/e2e/api）, steps（步骤文本）, expected（预期文本）, precondition（可空）。
  - script：平台结构化 DSL 数组，必须含业务断言。脚本需平台校验通过，跨平台迁移选择器 key 必须在目标项目存在；已有真实原始 selector 可以使用，不能编造 key。
  - verdict 固定 pass。
  - report：每个 script 步骤对应一项，action 与脚本相同、ok 必须 true；断言带 check 实际值。每条 report/script JSON 不超过 60000 UTF-8 字节，大截图单独作为证据处理，不塞 base64。
  - executor：真实执行器，比如 QA Lab StepExecutor + Playwright；provider=codex 只代表来源。
  - environment：操作系统、应用地址/版本等，不放密码。
  - scope：实际验收范围与未覆盖项。
  - finished_at：真实结束时间，含时区 ISO8601；duration_ms：实际耗时。
返回 data.project_id, requirement_id, batch_id, records[{case_id, run_id, title}], reused。
GET /api/ai/testcases/{id} 校验脚本及正文；GET /api/exec-queue/history?project_id=...&batch_id=... 可查看外部执行报告。
链接：/case-library?project_id=ID；/exec-results?project_id=ID&batch_id=BATCH。
仅登录用户提交的外部实测报告属于来源声明，平台不能独立证明客户端是否真实执行，因此记录显式标注“外部实测导入”，不伪装线上 Runner 领取执行。

## 平台 Runner
仓库工具 tools/qalab-runner/runner.mjs 使用 Node.js，GUI/E2E 由 step-executor.mjs 调用 gui-mcp/gui-core.mjs，通过 Playwright 连接应用。
含非空 precondition 时先调用 Claude Code 导航；含 judge 或不支持的步骤可能调用模型。确定性脚本不应为无必要的环境文字引入 Claude 依赖。
用例库执行/重试需线上设备 Runner 在线。重试读取最新用例，新增执行记录。外部回填不等价于已验证这一完整链路。

## 执行终止
执行列表对排队项直接取消，对运行项发送终止请求。Runner 每 5 秒检查心跳返回的 cancel_requested，杀掉本条执行子进程及其工具后回写，再释放设备锁；默认整条执行超时 15 分钟（EXEC_TIMEOUT_MS）。旧 Runner 需要更新并重启才能响应终止；不能仅把服务端状态改成结束后继续让旧进程操作。

## Namiwork 已验证的示例
首页导航 [data-testid="nav-home"]；首页写文档入口 [data-testid="home-skill-chip"] + has_text=写文档；
输入 [data-testid="message-input"]；选中技能 [data-kind="skill"][data-skill-name="writing-router"]；
发送 [data-testid="send-button"]。
独立任务入口 [data-testid="aside-new-task-btn"]；点击后先断言 home-greeting-title 可见、chat-user-query 不存在、message-input 文本为空，再选技能并发送。
路由验收结束后，使用 click + selector=[data-testid="send-button"]:has(img[src*="pause"]) + args.if_visible=true 停止本次仍在生成的回复，再 assert_absent 验证停止并返回首页。if_visible 需更新后的 Runner；它只跳过当前不可见目标，非法或重复匹配仍失败。
输入用 click → press End → type 追加，不能 fill 擦除技能标签。
实际工具区域 .chat-inline-tool-event__detail.format-code 读取 writing-router/SKILL.md，随后返回 name: writing-router / 全能写作工作站。仅当需求为技能路由时以这些作为验收，生成文档需求还需验证交付物。
writing-router 合理调用 huibao-writer 等下级写作技能不等于路由错误。选择器随版本变化，执行前须现场核实。

## 示例调用
`$qalab-requirement-test`
需求：首页选择写文档快捷入口，发送 query 后应调用所选技能。
应用：D:\Program Files\namiwork\Namiwork.exe
项目：纳米Work桌面版
回填：https://qalab.claw.qihoo.net/case-library
优先主流程，少量相关边界。
