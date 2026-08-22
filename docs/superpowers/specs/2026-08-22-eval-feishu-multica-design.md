# 设计:对话测评链路 · 子项 4 —— 飞书导出 + multica 推送

- 日期:2026-08-22
- 状态:已评审(用户 /goal 授权"剩余子项按推荐执行完";决策由 AI 自主拍板并记录于此供审)
- 所属大工程:对话测评链路(生成→下发/执行/回写→判定→**回填/推送**)。**本子项是最后一块。**
- 本 spec 范围:①把 eval_run 结果(分享链接/耗时/算力豆/正文 + 判定 verdict/is_abnormal)**导出到飞书表**;②异常会话(is_abnormal=true)**推送 multica**(可插拔适配器,契约占位待用户填)。
- 依赖:子项 0-3(eval_run 有全部结果+判定字段,均已合入 main)。
- 关联代码:`backend/app/services/feishu.py`(现有只读取文,本子项加写回)、`backend/app/core/config.py`(FEISHU_*/新增 MULTICA_*)、`backend/app/models/ai_eval.py`(eval_run 的 pushed_multica/multica_ref 列子项0 已建)、CLI `src/feishu-sheet.js`(写回样板,移植到平台 Python)。

## 1. 背景与问题

判定(子项3)后 eval_run 有完整结果 + 三维判定 + is_abnormal,但**结果只在平台**。业务同学习惯看飞书表;异常会话需推 multica 走详细分析。本子项补这两个出口。

**关键架构事实(探查确认,决定飞书路线)**:eval_query 是**平台 AI 生成**的(子项1),**不是从飞书读的**;eval_query/eval_run **没有"飞书来源行"锚点**(无 spreadsheet_token/sheet_id/row)。故"回填飞书原表某行"语义**不成立**——那需要改 eval_query 支持"从飞书导入 query"+ 加来源锚点(大改、与子项1"平台生成"矛盾)。**天然形态是"导出到飞书表"**:平台自己有行序(eval_run 列表),按行写到用户指定的目标表。复用 CLI feishu-sheet.js 的 PUT/values+分区间+token 重试样板,**不改 eval 数据模型**。

平台现有 `feishu.py` **只读取文、零写回**;CLI `feishu-sheet.js` 有完整写回(列字母↔序号转换、非连续列分区间写、token 失效刷新重试)。本子项把写回能力移植到平台 Python。

## 2. 目标与非目标

**目标**
- 平台新增飞书**写回**能力(移植 CLI 的 PUT /values + 分区间 + token 重试),把某项目(或某批次)的 eval_run 导出到用户指定飞书表:每条一行,列含 分享链接/产物链接/耗时/算力豆/正文 + 判定 verdict/verdict_reason/is_abnormal。
- 端点 `POST /api/eval-export/feishu`(用户 JWT,指定 project_id + 目标表 URL + 可选 batch_id/仅异常)。
- multica 推送:**可插拔适配器**(定义"推送异常会话"接口),读 is_abnormal=true 的 run,调 multica(API 或 CLI,**契约占位可配**),成功回写 eval_run.pushed_multica/multica_ref(子项0 已建列,防重推)。端点 `POST /api/eval-export/multica`(批量推项目异常会话)。
- 前端:EvalResults 页加"导出飞书""推送异常到 multica"按钮(最小)。

**非目标(YAGNI)**
- **不做"回填飞书原表"**(缺来源锚点,见 §1;要它须改 eval_query 来源,大改,不在本子项)。
- 不改 eval 数据模型(pushed_multica/multica_ref 子项0 已建;不加飞书锚点)。
- 不做飞书表的自动创建/表头写入(用户提供已建好表头的目标表;或本子项写数据行不写表头——见 §5)。
- 不做 multica 契约的完整实现(**契约待用户提供**;本子项做适配器 + 占位配置,契约就位即用)。
- 不改 exec_queue/gen_testcases/eval 生成·下发·判定链路。

## 3. 关键决策(AI 自主拍板)

| # | 决策 | 选择与理由 |
|---|---|---|
| 1 | 飞书路线 | **导出到飞书表**(非回填原表)。eval_query 平台生成无飞书锚点,回填原表须大改;导出新表复用 CLI 样板、不改模型。 |
| 2 | 飞书写回落点 | 平台 `services/feishu.py` **加写回函数**(`write_sheet_rows` 等),移植 CLI 的列转换/分区间/token 重试。与现有只读函数并存。 |
| 3 | 导出触发 | 端点 `POST /api/eval-export/feishu`(用户 JWT + assert_project_role),入参 {project_id, sheet_url, batch_id?, abnormal_only?, start_row?}。 |
| 4 | 导出列 | 复用 CLI 五列语义(分享链接C/产物D/耗时E/算力豆F/正文H)+ 平台判定列(verdict/verdict_reason/is_abnormal,列可配)。列映射可配,默认沿用。 |
| 5 | multica 对接 | **可插拔适配器 `services/multica.py`**:定义 `push_abnormal_run(run) -> ref`;实现两种后端(http webhook / cli 命令),由 config(MULTICA_MODE/URL/CLI 模板/TOKEN)选。**契约占位**:默认发"对话分享链接 + run 摘要"到可配 endpoint;用户填真实契约即用。 |
| 6 | multica 触发 | 端点 `POST /api/eval-export/multica`(批量推项目 is_abnormal 且未 pushed 的 run),成功回写 pushed_multica=true+multica_ref。防重推。 |
| 7 | 防重推 | eval_run.pushed_multica(子项0 已建)为 true 的跳过;multica_ref 记 multica 侧任务 id/链接。 |
| 8 | 安全 | 飞书 sheet_url 解析 token 沿用现有 parse 逻辑;share_link 推 multica 前校验 scheme(补子项3 遗留的写入侧校验)。 |

## 4. 飞书写回(`services/feishu.py` 加)

移植 CLI `feishu-sheet.js` 的写回,Python 化:
- `_col_to_num(col)` / `_num_to_col(n)`:列字母↔序号(多字母 26 进制)。
- `_api_put(path, body)`:PUT 封装(仿现有 `_api_get`),token 失效码 {99991663,99991661,99991668} 刷新重试一次。
- `parse_sheet_url(url) -> (spreadsheet_token, sheet_id)`:从飞书 sheets/wiki 链接解析(wiki 需 get_node 换 obj_token,CLI feishu-sheet.js:67-125 有样板)。
- `write_sheet_rows(sheet_url, rows: list[dict], col_map: dict, start_row: int)`:每行 rows[i] 按 col_map(字段→列字母)分连续列区间写 PUT /values(非连续列分区间,不碰中间列),token 重试。answer 截断 45000。

## 5. 导出端点(`api/eval_export.py`,新建)

`POST /api/eval-export/feishu`:
- 入参 `EvalExportFeishuIn{project_id, sheet_url, batch_id?, abnormal_only=false, start_row=2}`。
- 鉴权 assert_project_role(admin/member)。
- 查 eval_run(project_id [+ batch_id] [+ is_abnormal if abnormal_only]),按 id 排序。
- 每条 run → row dict:{share_link, artifact_share_link, reported_duration, bean_cost, answer, verdict, verdict_reason, is_abnormal(是/否), title(from eval_query)}。
- 调 feishu.write_sheet_rows,从 start_row 起逐行写。
- 返回 {exported: n, sheet_url}。
- is_configured() 检查(FEISHU_APP_ID/SECRET),未配报 400。

`POST /api/eval-export/multica`:
- 入参 {project_id, batch_id?}。查 is_abnormal=true 且 pushed_multica=false 的 run。
- 逐条 multica.push_abnormal_run(run):成功 → run.pushed_multica=true, run.multica_ref=ref;失败记错不断批。
- 返回 {pushed: n, skipped: m, results:[...]}。

`GET /api/eval-export/multica-pending`(可选):查待推(is_abnormal 且未 pushed)数量,前端提示。

router 注册。

## 6. multica 适配器(`services/multica.py`,新建,契约占位)

```
push_abnormal_run(run) -> str|None:
  组装 payload:{share_link, artifact_share_link, session_id, verdict_reason, verdict_dims, project_id, run_id}。
  按 config.MULTICA_MODE:
    "http": POST config.MULTICA_URL(Bearer MULTICA_TOKEN),body=payload → 解析返回的任务 id/链接作 ref。
    "cli":  subprocess 跑 config.MULTICA_CLI_TEMPLATE.format(link=share_link, ...) → stdout 取 ref。
    "off"/未配: 不推,返回 None(或记"multica 未配置")。
  share_link 推前校验 http(s) scheme(补子项3 XSS 遗留:写入/外发侧也校验)。
  失败抛异常(端点捕获,不断批)。
```
- config 加:`MULTICA_MODE`(off/http/cli,默认 off)、`MULTICA_URL`、`MULTICA_TOKEN`、`MULTICA_CLI_TEMPLATE`。**契约占位**:用户填真实 URL/命令即用;off 时端点返回"multica 未配置,请在 .env 配 MULTICA_*"。
- **契约待用户细化**:payload 具体字段名、URL/method/认证、CLI 命令格式——用户提供后调整 push_abnormal_run 的组装与解析。当前按"发分享链接 + run 摘要到可配 endpoint"通用形态占位。

## 7. 前端(最小)

- EvalResults.vue 加工具栏按钮:"导出到飞书"(弹窗填 sheet_url + 选 batch/仅异常 → 调 feishu 导出)、"推送异常到 multica"(调 multica 端点,提示 pushed/skipped)。
- api/index.js:exportEvalFeishu(payload) / pushEvalMultica(payload)。
- share_link 展示已有 safeUrl(子项3);本子项前端仅加两按钮。

## 8. 迁移与 schema

- **无 schema 变更**:pushed_multica/multica_ref 子项0 已建;不加飞书锚点(导出路线不需要)。
- config 加 MULTICA_* 项(.env.example 补占位 + 注释说明契约待填)。

## 9. 影响面与风险

- **隔离**:feishu.py 加写回(与只读并存)、新建 eval_export/multica service+api,不改 eval 生成/下发/判定。
- **风险1(飞书写回)**:PUT /values 覆盖目标表数据。缓解:导出到用户**指定的目标表**(非原用例表),文档提示用空表/专用表;分区间只写映射列。
- **风险2(multica 契约未定)**:占位适配器可能与真实 multica 不符。缓解:适配器接口清晰(push_abnormal_run),契约就位只改组装/解析一处;off 默认不误推。
- **风险3(飞书写权限)**:目标表需共享给飞书应用(可编辑)。文档说明(同 CLI 部署手册)。
- **风险4(真验证)**:飞书导出需真飞书应用+表;multica 需真 multica。本子项交付"链路通、样板对";真导出/推送待配置环境。脱机验列转换/分区间/payload 组装/防重推逻辑。

## 10. 验证方式(本仓库无测试框架)

1. feishu 写回:脱机验 _col_to_num/_num_to_col 往返、_column_groups 分区间(非连续列 C/D/E/F + H 分组)、write_sheet_rows 组装的 valueRange 格式(mock _api_put 捕获请求)。
2. 导出端点:插 eval_run(含判定)→ mock feishu.write_sheet_rows → 断言 row dict 字段映射正确(share_link→C 等 + verdict 列);abnormal_only 过滤。
3. multica 适配器:mock http/cli → 断言 payload 组装(含 share_link 校验非 http 被挡)、ref 解析;off 模式不推;pushed_multica 防重推(已推的跳过)。
4. 端点:multica 批量推 → 落 pushed_multica/multica_ref;单条失败不断批。
5. 前端:两按钮 npm build 过。
6. 端到端(真飞书+真multica,有环境时):导出看飞书表、推 multica 看任务。本机待验记录。

## 11. 交付清单

- [ ] services/feishu.py:加 _col_to_num/_num_to_col/_api_put/parse_sheet_url/write_sheet_rows(移植 CLI 样板)
- [ ] services/multica.py:push_abnormal_run(http/cli/off 适配器,契约占位,share_link scheme 校验)
- [ ] config.py + .env.example:MULTICA_MODE/URL/TOKEN/CLI_TEMPLATE(占位+注释)
- [ ] api/eval_export.py:POST /feishu、POST /multica、GET /multica-pending + router 注册 + schemas
- [ ] 前端:EvalResults 两按钮 + api exportEvalFeishu/pushEvalMultica
- [ ] 手动/脚本验证(§10)

## 12. 大工程收官

子项 4 完成后,对话测评链路端到端闭环:**平台 AI 生成 query → 下发指定设备 → CLI 执行+抓 WS 轨迹 → 回写 → 大模型判定三维 → 飞书导出 + 异常推 multica**。
**遗留(全工程,非本子项阻塞)**:①真引擎/真环境端到端联调(本机 claude 被 hook 污染,飞书/multica 需真配置)——各子项已记;②multica 真实契约(用户提供后填实占位);③CLI 平台模式附件下载(现 fail-closed)、多轮不串联、非 namiwork 被测引擎(codex/claude CLI subprocess)——后续增量。
