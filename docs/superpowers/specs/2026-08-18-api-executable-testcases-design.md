# 可执行 API 测试用例体系 —— 设计稿

- 日期:2026-08-18
- 状态:待评审
- 关联:`tools/qalab-runner/runner.mjs`(执行分流)、`tools/qalab-runner/step-executor.mjs`(gui 确定性执行,同构参照)、`backend/app/services/claude_runner.py`(生成 prompt + 校验)、`backend/app/services/generators/`(多引擎复用)、`backend/app/api/ai.py`、`backend/app/models/ai.py`
- 前身:`docs/superpowers/specs/2026-08-13-typed-executable-testcases-design.md` §5.2 动作表**规划过** api 的 `http`/`assert_status`/`assert_json`,但从未实现(现 `_VALID_ACTIONS` 与 `step-executor.mjs` 只有 gui 动作)。本设计是该处未竟规划的**正式落地**,并改用"请求-断言-提取原子"形态(见 §5 决策说明)。

## 1. 背景与根因

平台的 api 类型用例目前**没有结构化可执行脚本**:

- 生成侧:`build_testcase_prompt` 明确要求"script **仅 gui/e2e 需要**,api 一律给 `[]`"。api 用例只有 `steps`/`expected` 自然语言。
- 执行侧:`runner.mjs` 对 `kind==api` 的处理是"给 Claude 开 `Bash`,让它照着 steps/expected **临场拼 curl**"。打哪个 URL、带什么鉴权、断言什么,全靠 LLM 猜。

**根因**:api 用例是"LLM 即兴执行",而非"可被机器确定性执行的自动化用例"。由此带来四个症状:
1. **不可复现**——同一用例两次执行,LLM 拼的请求/断言可能不同;
2. **慢 & 贵**——每条都起 Claude 子进程;
3. **断言松**——LLM 自己决定"算不算通过",易假阳性;
4. **卡第一步**——鉴权、baseURL、参数只要缺一样,LLM 就瞎编 → 直接 4xx/401。

对照 gui/e2e:它们有结构化 `script` + `step-executor.mjs` 确定性执行,所以稳。api 缺的正是这一套。

## 2. 目标 / 非目标

**目标**
- 为 api 用例定义**结构化可执行 script**(请求-断言-提取原子),生成时即产出;
- 在 runner 新增**确定性 api 执行器**(Node 原生 `fetch`,无 LLM、无第三方依赖),逐步执行、变量传递、断言判定;
- 支持**单用例内多步链式**:登录取 token、创建取 id、写操作后清理,变量在步骤间传递;
- 生成侧注入**项目级 api 契约**(base_url + 接口清单),契约来源支持 **OpenAPI/Swagger 导入 / 手写 / 粘贴 curl**;
- 校验器把非法 script 挡在生成阶段(降级 manual,不派坏 script 到执行机);
- **反推需求录入规范**:明确"生成可执行 api 用例,需求要满足什么"。

**非目标**
- 不做**跨用例依赖**(depends_on / 跨 exec_run 共享变量 / 拓扑排序)。只做单用例内多步。
- 不改 `exec_queue` 调度、dispatch/claim/回写、数据库 `test_case` 结构(api script 复用现有 `script` TEXT 字段)。
- 不支持 multipart/文件上传、cookie 鉴权、GraphQL、XML/二进制响应断言(遇到告警,先不强解)。
- 不追求 100% 自动生成可执行用例;无契约可依时允许降级 manual。

## 3. 核心决策(已定,均经确认)

1. **执行=路线 A(结构化 + 确定性执行器)**:api 用例走新 `api-executor.mjs`,Node 原生 fetch,不起 Claude;script 缺失/非法时才回落旧 LLM 兜底路径。
2. **被测=外部业务系统**:baseURL/鉴权是**项目级**外部配置,生成器与执行器都需知道。
3. **鉴权两种都支持**:`fixed`(项目配固定 token/header)+ `login`(用例内登录步骤 extract token)。
4. **链式范围=单用例内多步**:变量表在一条用例执行周期内存活;跨用例依赖不做。
5. **契约来源=两者结合**:OpenAPI/Swagger 导入 + 手写 + 粘贴 curl,三条路并存。
6. **写操作强制自带清理**:含 POST/PUT/PATCH/DELETE 的 script 必须有 `cleanup` 步骤,否则校验降级。
7. **script 形态=请求-断言-提取原子(变体 A)**:每步是一个完整 HTTP 请求单元,自带 asserts 与 extract。**不**采用把 http/assert/extract 拆成独立 action 的形态(变体 B,即 2026-08-13 spec 当年的设想)——那样请求与其断言被拆开、靠隐式状态传递,难读易错。
8. **断言 op 全集 + 点路径**:op 取 `eq/neq/exists/contains/gt/lt/regex/type`;变量/断言路径用简单点路径(`data.list.0.id`),自实现无依赖,契合 `{code,msg,data}` 返回结构。

## 4. 架构总览(3 层,复用现有骨架)

```
录入层                       生成层                          执行层
┌──────────────┐           ┌─────────────────────┐        ┌────────────────────┐
│ api_env 表    │           │ build_testcase_     │        │ runner.mjs          │
│ · base_url    │─注入prompt→│ prompt (改造)        │─script→│  kind==api 且有     │
│ · auth        │           │ · api 用例产出        │  JSON  │  合法 script?        │
│ · contract    │           │   结构化 script       │  存    │   ├是→api-executor  │
│               │           │ _validate_api_script │ test_  │   │  (新,确定性fetch) │
│ 契约录入三条路: │           │  (新校验器)           │ case   │   └否→runClaude兜底 │
│ Swagger/手写/  │           │ 分流:gui→_validate_  │ .script│  gui/e2e→step-exec  │
│ 粘贴 curl      │           │ script;api→新校验器   │        │  (现状不变)         │
└──────────────┘           └─────────────────────┘        └────────────────────┘
        │                                                            │
        └──── curl 解析器(纯函数):curl文本→{method,base_url,path,headers,body},剥离鉴权头 ────┘
                     入口①:并入 contract  入口②:转单步 script 种子
```

**关键**:api script 复用 gui/e2e 已有的 `test_case.script`(TEXT,存 JSON)字段——数据库 schema、exec_queue 调度、dispatch/claim/回写**零改动**。api script 只是 `script` 字段里换一种 JSON 形状。

## 5. API script schema(本设计核心)

一条 api 用例的 `script` = **请求步骤有序数组**,每步是一个"请求-断言-提取"原子。

### 5.1 单步结构

```jsonc
{
  "name": "创建项目",                    // 该步人读名
  "request": {
    "method": "POST",                    // GET/POST/PUT/PATCH/DELETE
    "path": "/api/projects/{{pid}}",     // 相对路径;base_url 由执行器注入拼接;可含 {{var}}
    "headers": { "Authorization": "Bearer {{token}}" },  // 可选,可含 {{var}}
    "query": { "page": 1 },              // 可选,可含 {{var}}
    "body": { "name": "自动化项目" }       // 可选,可含 {{var}}
  },
  "asserts": [                           // 至少 1 个,否则无判定依据
    { "type": "status", "op": "eq", "value": 200 },
    { "type": "jsonpath", "path": "code", "op": "eq", "value": 0 },
    { "type": "jsonpath", "path": "data.id", "op": "exists" }
  ],
  "extract": { "pid": "data.id" },       // 可选:从本步响应体按点路径取值,写入用例级变量表
  "cleanup": false                       // 可选:标记该步为清理步骤(见 5.4)
}
```

### 5.2 断言(asserts)

每个断言 `{ type, path?, op, value? }`:

| type | 说明 | 例 |
|---|---|---|
| `status` | HTTP 状态码 | `{type:"status", op:"eq", value:200}` |
| `jsonpath` | 响应体字段(点路径) | `{type:"jsonpath", path:"data.id", op:"exists"}` |

`op` 全集及语义:

| op | 含义 | 备注 |
|---|---|---|
| `eq` / `neq` | 相等 / 不等 | 需 `value` |
| `exists` | 字段存在(非 undefined) | 无需 `value`;宜用于不稳定字段(时间戳/自增 id) |
| `contains` | 字符串包含 / 数组含元素 | 需 `value` |
| `gt` / `lt` | 数值大于 / 小于 | 需 `value` |
| `regex` | 正则匹配 | `value` 为正则串 |
| `type` | 类型判定 | `value ∈ {string,number,boolean,object,array,null}` |

**信封优势**:平台统一 `{code,msg,data}`,`{type:"jsonpath", path:"code", op:"eq", value:0}` 即标准业务成功断言,几乎每条 api 用例都会带。

### 5.3 变量与链式(extract + {{var}})

- `extract: {token: "data.token"}`:从**本步响应体**按点路径取值,写入用例级**变量表** `vars`。
- 后续任何步骤 request 里的 `{{token}}`,在发请求前被替换为 `vars.token`。
- 变量表在**一条用例执行周期内**存活(单用例内多步)。登录取 token、创建取 id 全靠它。
- `fixed` 鉴权模式下,项目配置的固定 header 在执行开始时预置进请求(不占用变量)。

### 5.4 清理(cleanup)

- 含写操作(POST/PUT/PATCH/DELETE)的 script,生成器被要求在末尾补 `cleanup:true` 的删除步骤,用 `{{提取的id}}` 定位删除。
- 执行语义(详见 §6):cleanup 步骤**无论前面成败都执行**、**不计入 verdict**、**多个逆序执行**。

### 5.5 为什么是这个形态(而非变体 B/C)

- **原子完整**:一个 HTTP 请求天然携带它的断言与提取。变体 B 把三者拆成独立 step,要靠"上一步响应"隐式传递,难读易错,且 gui 的 `target.key` 语义对 api 毫无意义。
- **表达力够**:变体 C(扁平 `expect:{status,code}`)无法表达 contains/regex/数组长度/类型断言。变体 A 的结构化断言覆盖全。
- **可校验**:结构固定,`_validate_api_script` 能逐字段校验(见 §7)。

## 6. 确定性执行器 `api-executor.mjs`(新增)

纯 Node 原生 `fetch`,确定、可复现、不起 Claude。与 `step-executor.mjs`(gui)确定性优先的模式同构。

### 6.1 执行流程(单条用例)

```
输入: script(步骤数组) + 项目 api 配置(base_url, auth) + payload
1. 初始化变量表 vars = {};若 auth.type==fixed,预置固定 header
2. 顺序遍历每个非 cleanup step:
   a. 深度替换 step 内所有 {{var}} ← vars(path/headers/query/body)
   b. fetch(base_url + path, {method, headers, query, body}),带单步超时
   c. 读响应: status + body(尽量 JSON.parse,失败留原文)
   d. 逐条判 asserts → 任一失败即该步 fail
   e. 若有 extract: 按点路径从 body 取值写入 vars(取不到 = 提取失败 = 该步 fail)
   f. 记录该步结果(请求摘要/响应码/断言明细/耗时)入 steps[]
   g. 若该步 fail → 短路(停止后续非 cleanup 步),跳至 3
3. 执行所有 cleanup step(逆序,尽力而为,见 6.2)
4. 汇总 verdict 回写
```

### 6.2 关键语义

- **① 普通步骤失败即短路**:某步断言失败/请求异常/提取失败 → 停止后续普通步骤(后续多半依赖其 extract),verdict=fail,随后仍执行 cleanup。
- **② cleanup 尽力而为**:标 `cleanup:true` 的步骤——无论前面成败都执行(保证不留脏数据);其断言**不计入 verdict**(清理失败只告警);多个 cleanup **逆序**执行(后创建的先删)。
- **③ verdict 契约不变**:输出 `{verdict, reason, evidence}`,与现有 runner 回写契约完全一致,`runner.mjs` 回写逻辑零改动。

```jsonc
{
  "verdict": "pass",  // 或 "fail"
  "reason": "step2「创建项目」断言失败: jsonpath data.id 期望 exists,实际 undefined",
  "evidence": ""      // api 无截图;可放请求-响应快照文本供排障
}
```

`reason` 必须**精确到步 / 断言 / 期望 vs 实际**——api 排障靠文字,比 gui 的截图更依赖此。

### 6.3 与 runner.mjs 集成(最小改动)

现分流:`gui/e2e → step-executor;api/cli → runClaude(开 Bash)`。改为:

```js
if (kind === "api" && 有合法结构化 script) {
    result = await apiExecutor.run(script, apiEnv, payload);   // 新确定性路径
} else if ((kind === "gui" || kind === "e2e") && 有 script) {
    result = await stepExecutor...                             // 现状不变
} else {
    result = await runClaude(payload, kind);                   // 旧 LLM 兜底(api 无 script / cli)
}
```

仅在 `runner.mjs` 加一个分支 + `import`;`step-executor.mjs`、claim/回写、exec_queue 全不碰。

### 6.4 项目 api 配置的传递

执行器需 base_url/auth。**决策:下发时把项目 api 配置塞进 payload**(后端入队时附上),而非 runner 执行时另拉接口。理由:配置小、且保证"下发那一刻的配置快照",避免执行时配置漂移。

## 7. 生成侧改造

两个引擎(claude/deepseek)共用 `build_testcase_prompt`/`parse_testcases`,改一处两者都生效。

### 7.1 prompt 改造(`build_testcase_prompt`)

- **让 api 也产出 script**:新增"api script 编写规范"段,与现有 gui/e2e 段**并列扩写**(不替换,gui/e2e 生成质量不受影响)。含变体 A 字段说明 + 一个完整正例(登录→创建→清理)。
- 硬约束:每步至少一个 assert;含写操作必须末尾补 cleanup 步骤;断言优先带 `{path:"code",op:"eq",value:0}`;边界/异常用例给具体示例值(呼应现有第 10 条)。
- **找不到接口契约 → 改判 kind=manual、script=[]**(与 gui"找不到 key 就降级 manual"同构,不许瞎编 path)。
- **注入项目 api 契约**:新增 `_load_api_contract(project_id)`(类比 `_load_selector_keys`),把 base_url + 接口清单拼进 prompt;读不到则注入空块并提示"无契约,api 用例请改判 manual"。

### 7.2 新增校验器 `_validate_api_script`

仿 `_validate_script`(gui),返回 `(规范化 script, 错误)`,非法则调用方降级 manual:

- 是非空数组;每步是对象;
- `request.method` 合法、`request.path` 非空;
- 每步 `asserts` 非空;每断言 `type ∈ {status,jsonpath}`、`op` 合法;jsonpath 必须有 `path`;需值的 op 必须有 `value`;
- **变量引用闭环校验**(关键):任何 `{{var}}` 必须在**之前某步的 extract 里定义过**(或来自 auth 固定注入);引用未定义变量 → 非法。api 版的"未注册 key"校验,把错误挡在生成阶段;
- **写操作清理校验**:含写方法但无任何 `cleanup:true` 步骤 → 非法降级(落实决策 6)。

### 7.3 分流接入 `parse_testcases`

```python
if kind in ("gui", "e2e"):  script, err = _validate_script(...)        # 现状
elif kind == "api":         script, err = _validate_api_script(...)    # 新增
# err → 降级 manual;合法 → 存 script_json
```

`gen-script` 单条重生接口、`build_script_prompt` 同步加 api 分支(让"重新生成 script"按钮对 api 用例也可用)。

## 8. 项目 api 配置 + 需求反推(回答"需求要满足什么")

### 8.1 项目级 api 配置(配一次,全项目复用)

新增轻表 `api_env`(类比 selector 项目级配置),平台/项目管理员维护:

| 字段 | 作用 |
|---|---|
| `project_id` | 归属项目 |
| `base_url` | 被测系统地址,执行器拼 path |
| `auth_type` + `auth_json` | `fixed`(固定 header/token)或 `login`(登录接口信息) |
| `contract` (TEXT) | 接口契约:Swagger 导入结果 / 手写清单 / curl 解析累积 |

契约录入三条路:**导入 Swagger**(贴 `/openapi.json` URL 或上传,后端解析成精简清单)/ **手写** / **粘贴 curl**(见 §9)。

### 8.2 需求要满足什么 —— 分三档

契约配在项目级后,需求本身负担大幅下降:

- **档① 项目已导入契约(最省)**:需求只写**业务意图 + 场景** —— 测哪个接口/功能、测什么场景(正常/必填校验/重复冲突/越权)、业务判定标准(什么算成功、错误码对应)。接口细节 AI 从注入契约查。
- **档② 无契约,需求自带接口信息**:需求须补 method+path、每参数(名称/位置/类型/必填/**示例值**)、鉴权方式、成功响应结构 + 关键字段 + code 约定。
- **档③ 只写一句"测下用户接口"**:无契约可依 → 生成器降级 manual,不产出 script。这是保护,非 bug。

### 8.3 一句话反推结论

> **可执行 api 用例 = 接口契约(打哪/带什么/期望什么)+ 场景意图(测什么情况/怎么算对)。**
> 契约能在项目级用 Swagger/curl 配一次,需求就只管场景意图;配不了契约,需求就得自带契约,否则只能生成人工用例。

据此,需求录入界面宜按"关联接口(从契约选)+ 场景清单 + 业务判定"引导,而非给空文本框任其发挥。

### 8.4 录入界面(前端)

- 项目设置加"api 环境"页:填 base_url/鉴权 + 导入 Swagger / 手写 / 粘贴 curl(类比已有 selector 管理页)。
- (可选增强)AITestGen 生成页:项目配了契约时展示"可选接口清单"辅助 QA 圈定范围。

## 9. curl 解析器(纯函数,独立可测)

吃 curl 文本 → 吐 `{method, base_url, path, headers, body}`,**剥离鉴权头**。两个用途共用同一 parser:

- **入口①(契约来源)**:解析结果并入项目 `api_env.contract`,注入生成 prompt。
- **入口②(用例种子)**:解析结果转成变体 A 的**单步 script**(request + 默认断言),AI 再补断言/边界/清理。

三个必须处理的点:
1. **鉴权不焊死**:curl 里 `Authorization: Bearer xxx` 是会过期的真实 token,明文存库也不安全 → 抽出归入项目 `auth` 或替换成 `{{token}}`,不原样进 script。
2. **url 拆分**:`https://host/api/x` → base_url(项目配置)+ path(`/api/x`),支持一键切环境。
3. **只是起点**:curl 无断言/边界/清理,AI 需在此基础上补。

支持范围:最常见形态(`-X` / `-H` / `-d`/`--data`/`--data-raw` / GET 带 query);multipart/cookie 先告警不强解。

## 10. 数据模型变更

- **api script 复用 `test_case.script`(现有 TEXT 列)** → **无需 migrate**、无枚举变更。
- **新增 `api_env` 表**:需三处同步(既有约定)——SQLAlchemy 模型 `app/models/`、`backend/sql/schema.sql`、`app/db/migrate.py` 加 `ensure_api_env_table`(老库建表);模型在 `app/models/__init__.py` 汇总导入。
- payload 扩展:下发 api 用例时附 `api_env`(base_url/auth)快照(见 §6.4)。

## 11. 错误处理(不静默,延续现有保守风格)

| 环节 | 失败情形 | 处理 |
|---|---|---|
| curl 解析 | 格式不认识/multipart | 前端提示无法解析,**不产出半成品** |
| 生成校验 | script 非法(method/断言/变量闭环/缺清理) | 降级 manual,`kind_reason` 写明原因 |
| 执行·请求 | 网络错误/超时/DNS | 该步 fail,reason 带具体错误;整条 fail |
| 执行·断言 | 期望≠实际 | fail,reason 精确到步/断言/期望vs实际 |
| 执行·提取 | 点路径取不到值 | 提取失败=该步 fail(早失败早报) |
| 执行·清理 | cleanup 步骤失败 | 只告警,不计入 verdict |
| 配置缺失 | 项目未配 base_url | 执行器直接 fail,reason"项目未配置 api 环境" |

## 12. 测试策略(本仓库无测试框架,沿用手动端到端 + 纯函数自测)

- **纯函数自测**(Node 直接跑几组输入输出):curl 解析器、断言判定、点路径提取、变量替换。
- **执行器端到端**:拿**平台自身后端**当被测系统(标准 `{code,msg,data}` + JWT),构造"登录→创建项目→查询→删除清理"的 script,真跑验证变量传递/断言/清理全链路。
- **生成侧闭环**:用一段带契约的需求,验证 AI 产出的 api script 能过 `_validate_api_script` 且能被执行器跑通——"生成即可执行"的验收。

## 13. 交付边界(YAGNI,明确不做)

- ❌ 跨用例依赖(depends_on)
- ❌ 为每接口建契约行(先单表存精简清单)
- ❌ multipart/文件上传、cookie 鉴权、GraphQL
- ❌ 响应体 JSON 之外的断言(XML/二进制)

## 14. 风险与权衡

- **AI 产出 script 的可靠性**:未必总能结构化。缓解:非法降级 manual;人工复核可修;单条 `gen-script` 可重生。
- **契约质量决定生成上限**:契约缺/错则生成不可靠。缓解:Swagger 导入保准确;无契约明确降级 manual 而非瞎编。
- **点路径表达力有限**:深层/数组复杂取值可能不够。缓解:先覆盖 `{code,msg,data}` 常见结构;不够再按需扩(集中在提取函数一处)。
- **api_env 三处 schema 同步**:模型 + `schema.sql` + migrate(既有约定),改表一并改。
- **鉴权 token 存储**:固定 token 存 `auth_json`,注意不随用例明文外泄;仅下发快照时带、日志脱敏。

## 15. 验收标准

- 生成一批含 api 的用例:有契约时 api 用例带合法结构化 script(过 `_validate_api_script`);无契约时降级 manual。
- 一条多步 api 用例(登录→创建→查询→清理):runner **不经 Claude** 确定性执行,变量正确传递,断言 pass/fail 正确,cleanup 步骤执行且不留脏数据。
- 写操作 api 用例若无 cleanup 步骤:生成阶段被校验降级 manual(不下发)。
- 引用未定义 `{{var}}` 的 script:生成阶段被校验拦截降级。
- 粘贴一条 curl:能解析出 method/base_url/path/headers/body,鉴权头被剥离;既能并入契约,也能一键转成单步 script 种子。
- 项目未配 api 环境时下发 api 用例:执行器明确 fail 提示"未配置 api 环境",不空转、不瞎打。

## 16. 分阶段落地(设计一次成型,建分阶段)

| 阶段 | 内容 | 落地即收益 |
|---|---|---|
| **P1** | `api-executor.mjs` + runner 分流;payload 带 api_env;`api_env` 表 + migrate + schema.sql | 手写一条 api script 即可确定性执行,不再靠 LLM 猜 |
| **P2** | 生成侧:prompt 加 api 段 + `_load_api_contract` + `_validate_api_script` + parse 分流 | 需求→可执行 api 用例的自动生成闭环 |
| **P3** | 项目 api 环境录入页 + Swagger 导入 + curl 解析器(两个入口) | 契约零/低负担录入,QA 只写场景意图 |
| **P4** | 需求录入引导(关联接口+场景+判定)、AITestGen 接口清单辅助 | 从源头提高 api 用例生成质量 |
