# 平台接入与脚本约定

## 准备
Windows 需要 PowerShell 5.1+；登录脚本只使用内置能力。macOS 使用 Python 3.9+ 和 scripts/qalab.py，无第三方 Python 依赖；会话通过 Security.framework 的 SecItem API 保存在登录钥匙串中，按平台源地址隔离，不通过命令行参数或明文文件传递令牌。doctor 执行独立临时条目的读写删除自检，不访问平台；login 隐藏输入密码，status 检查项目设备，import --payload 提交后即返回，job --job-id 单次查询后台进度，logout 删除该平台的会话。其他系统可通过受支持的浏览器或正常获取的用户 bearer token 调用 API，不共享用户凭据。
线上项目、用户和执行机与 localhost 分离。GET /api/projects 查询有权访问的项目；GET /api/devices 查询自己的设备；GET /api/auth/me 验证身份。
线上需要提供 `POST /api/verified-imports/jobs`。服务启动会创建 verified_import_job / verified_import_item 两张暂存表并启动持久化后台消费者；部署需更新后端并重启，安装新版技能前确认接口已上线。

## 异步导入与后续处理

提交阶段不扫描用例库；校验身份、项目、设备和报告结构后将原始包及逐条任务一起保存，返回 HTTP 202。接收回执含 accepted=true、job_id、project_id、external_id、case_count、status_url。相同用户/项目/external_id 重传相同内容返回同一个任务，内容不同返回 409，不能换 ID 绕过。
成功接收后结束当前任务。不要 preview，不询问疑似重复，不轮询。后续处理由平台独立完成，即使 Codex 已关闭也会继续。
后台按条处理，状态 pending / needs_confirmation / done / failed；同批某条待确认不阻塞其他条目。明确重复复用用例、追加本次实测结果；新场景新增待评审用例，不自动标记回归，并关联当前项目统一的“codex导入用例”需求；不确定项留在暂存区，未确认前不进入正式用例库。当前判重为保守规则，不能保证识别全部语义改写。
项目管理员在 `/verified-imports?project_id=ID&job_id=JOB` 查看步骤、预期和候选，选择复用或不同场景新建并说明依据。后台复核候选版本，变化后重新待确认。重复确认/后台重试不会重复写入执行结果。原用例正文、脚本与评审状态不被复用操作覆盖。
`GET /api/verified-imports/jobs?project_id=ID&page=1&status=needs_confirmation`：项目导入任务，每页20条；status 可省略。
`GET /api/verified-imports/jobs/{job_id}`：单次查看进度与条目结果；每条完成结果含 case_id/run_id/batch_id/disposition。
`GET /api/verified-imports/jobs/{job_id}/items/{item_id}`：原始实测用例、候选及确认信息。
`POST .../items/{item_id}/resolve`：仅管理员，body 为 action(reuse/create)、case_id(reuse必填)、token(候选confirmation_token)、reason(至少5字)。技能不自动调用。
`POST .../items/{item_id}/retry`：仅管理员重试失败条目。数据库临时故障后台最多尝试3次，其余错误保留供管理员排查。
旧同步接口及 preview 仅为旧客户端兼容保留，新技能不得降级调用。

## 导入契约
POST /api/verified-imports/jobs，Authorization: Bearer 用户 access token。仅项目 admin/member 可写，runner_device_id 必须属于当前用户。JSON:
- project_id, runner_device_id：线上真实 ID。sub_product 可选，默认空；查重与选择器验证限制在同一项目、子产品和平台。
- external_id：此次验证的稳定唯一标识，8–100 位字母数字下划线或短横线。相同 ID+内容重复调用复用记录，内容不同返回 409。
- requirement：用户真实需求标题，最多 512 字符；保留在导入任务和执行记录中供追溯，不决定正式用例的关联需求名称。新增用例统一关联“codex导入用例”，同项目复用该需求。已有用例的需求关联和回归标记不因追加执行结果而更改。
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
返回接收回执，不返回最终用例编号。链接：`/verified-imports?project_id=ID&job_id=JOB`。
后台完成后，正式用例及执行记录可在平台查看。本次真实脚本和报告写入 ExecRun.payload / report，保留环境、提交者与设备；不能把原用例脚本当成本次执行证据。
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
