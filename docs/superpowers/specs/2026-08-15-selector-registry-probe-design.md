# 选择器注册表单源化 + 设备探测 设计文档

- 日期:2026-08-15
- 状态:待评审
- 关联:CLAUDE.md「生成引擎抽象」「数据库与迁移」;`tools/qalab-runner/gui-mcp/`;`backend/app/services/claude_runner.py`

## 1. 背景与问题

平台用**语义选择器注册表**(`selectors.json`)把界面元素抽象成稳定的 `key`,供两处消费:
1. **生成侧**:`build_testcase_prompt` 把可用 key 清单注入 prompt,让 AI 只用库内 key 写 gui/e2e 的 script;新增的 key 校验(`_validate_script(valid_keys)`)把"瞎编 key"的用例降级 manual。
2. **执行侧**:runner 的 `gui-core.mjs` 用注册表把 `target.key` 解析成实际选择器(多候选自愈 + iframe 穿透)。

**根本问题:`selectors.json` 是一份"没有主人"的分布式文件。** 它纳入 git,后端(服务器 checkout)一份、每台执行设备各一份,谁都有、谁都不权威:
- **写回难**:补一个 key 要改仓库文件、提交、各设备 pull 才生效,跨机同步麻烦。
- **校验失真**:后端 key 校验读的是后端那份,未必等于设备实际能用的那份。
- **不支持多应用**:注册表当前写死针对「纳米Work」单一应用;平台实为多项目,且 `sub_product` 枚举已跨两个真实产品(纳米Work / 360安全龙虾),它们 UI 不同、选择器天然不同。

## 2. 目标与非目标

### 目标
- 把注册表从"分散文件"改为**后端集中持有 + API 下发**,成为唯一事实来源。
- 注册表按 **项目 + 子产品** 作用域区分,支持「同项目下子产品共用」。
- 平台网页可**触发在线设备探测**当前页面,回传可交互元素及打分候选选择器。
- 探测结果可**一键写回**注册表(增/改 key),即一次普通 DB 写,无 git 操作。
- 生成侧 key 注入/校验改读 DB,使校验成为**权威**(与设备实际一致)。

### 非目标(本次不做,YAGNI)
- **不改执行层写死的纳米Work 假设**(等 AI 回复的 `stopBtn/answerBubble` 判据等)。仅把 `vmIframe` 纳入注册表配置,算迈向多应用的第一步。
- **不做 git 自动写回**。
- **生成页不加子产品选择器**(见 §9,生成只用项目级共享 key)。
- 不做注册表的历史版本/审计(超出本次)。

## 3. 作用域模型:项目级共享 + 子产品级覆盖(merge)

一条 key 归属 `(project_id, sub_product)`,其中 `sub_product = ''` 表示**项目级共享**。

**解析规则**——给定 项目 P + 子产品 S,有效注册表 =
```
项目级共享 keys(P, '')  ∪  子产品专属 keys(P, S)
若同名 key 冲突 → 子产品专属覆盖共享
```

贴合「默认共享、个别专属」:
- 纳米Work 桌面版/云端版共用的 key → 放**项目级共享**,两个子产品都继承。
- 某子产品界面不同的个别 key → 放该**子产品专属**,自动覆盖同名共享 key。
- 不同项目(如另一项目测 360安全龙虾)→ 由 `project_id` 天然隔离。

`sub_product` 取值沿用 `api/release.py::SUB_PRODUCTS` 白名单(全平台固定枚举),空串=共享。

## 4. 数据模型(MySQL 5.6 无 JSON 类型 → JSON 以 TEXT 存储)

### 4.1 `selector_key`
| 列 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | int, FK→project, index | |
| sub_product | VARCHAR(32) NOT NULL DEFAULT '' | ''=项目级共享;否则为 SUB_PRODUCTS 之一 |
| `key` | VARCHAR(64) | 语义 key 名 |
| frame | VARCHAR(8) | shell / vm / auto |
| `desc` | VARCHAR(255) | 人读说明 |
| candidates | TEXT | JSON 字符串:`[{by,value,name?}, ...]`(按稳定性排序) |
| updated_by | int, FK→user, null | |
| updated_at | datetime | |

- 唯一约束:`UNIQUE(project_id, sub_product, key)`(用 `''` 而非 NULL,保证 MySQL 唯一性正常)。
- 索引:`(project_id, sub_product)` 便于按作用域取。

### 4.2 `selector_scope`(每个 项目+子产品 的注册表级配置)
| 列 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | int, FK, index | |
| sub_product | VARCHAR(32) NOT NULL DEFAULT '' | |
| vm_iframe | VARCHAR(255) | 内嵌 iframe 选择器(纳米Work 缺省 `iframe[src*=".work.n.cn"]`) |
| updated_at | datetime | |

- 唯一约束:`UNIQUE(project_id, sub_product)`。
- 解析 vmIframe 时:子产品专属优先,回落项目级共享。

两处 schema 同步(项目约定):`app/models/selector.py` + `backend/sql/schema.sql`;`migrate.py` 加 `ensure_selector_tables()`。模型在 `app/models/__init__.py` 汇总导入。

## 5. 后端 API(`app/api/selectors.py`,信封 `{code,msg,data}`)

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/api/selectors?project_id=&sub_product=` | 返回**合并后有效注册表**(runner 消费);含 `vm_iframe`、`version` | runner token |
| GET | `/api/selectors/manage?project_id=` | 分层返回(共享 / 各子产品),供管理页编辑 | 项目 admin/member |
| POST | `/api/selectors` | 新增一个 key(project_id, sub_product, key, frame, desc, candidates) | 项目 admin/member |
| PATCH | `/api/selectors/{id}` | 改 desc/frame/candidates | 项目 admin/member |
| DELETE | `/api/selectors/{id}` | 删 key | 项目 admin/member |
| PUT | `/api/selectors/scope` | 设某作用域的 vm_iframe | 项目 admin/member |
| POST | `/api/selectors/import-legacy?project_id=` | 把内置旧 `selectors.json` 的 57 key 导入为该项目**项目级共享**(幂等:已存在的 key 跳过) | 项目 admin |

- `version`:该作用域下 `max(updated_at)` 或 key 集合哈希,供 runner 判断是否需重拉。
- 鉴权沿用 `assert_project_role`(project_id 来自 query/body)。
- 序列化沿用手写 `_to_out`。

## 6. 探测子系统

### 6.1 `probe_request` 表
| 列 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | int, FK, index | |
| sub_product | VARCHAR(32) DEFAULT '' | 探测归属作用域(决定写回落点建议) |
| runner | VARCHAR(64) | 目标设备 runner_id |
| status | VARCHAR(16) | pending/running/done/failed |
| params | TEXT | JSON:`{contains?, limit?}` |
| result | TEXT | JSON:`gui.probe()` 的 `{groups:[...]}` |
| error | VARCHAR(500), null | |
| created_by | int, FK | |
| created_at / updated_at | datetime | |

独立于 `exec_run`:探测结果体积大(几十元素×候选)、语义不同(不进 checklist),分表避免污染。

### 6.2 API
| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| POST | `/api/probe` | 网页发起探测(project_id, sub_product, runner, params)→ 建 pending | 项目 admin/member |
| GET | `/api/probe/{id}` | 网页轮询结果 | 项目 admin/member |
| GET | `/api/probe/pending?runner=` | runner 拉取待探测 | runner token |
| PATCH | `/api/probe/{id}` | runner 回写 result/error | runner token |

runner 归属校验同 exec-queue:设备 token 锁定 `runner_id`,防冒充。

### 6.3 runner 探测循环(两种模式)
主循环在拉 exec-queue 之外,并列拉 `/api/probe/pending`:认领→`guiCore.setRegistry(该作用域注册表)`→按 `params.mode` 分派→PATCH 回写。设备离线则该 probe_request 停在 pending(网页轮询超时提示"设备未响应")。

- **discover(缺省)**:`guiCore.probe(params)` 扫当前页可交互元素,回写 `{groups:[...]}`。用于**新增/定向探测**(配 `params.contains` 关键词过滤锁定某新功能区域)。
- **verify(E2)**:`guiCore.verifyKeys(keys)` 批量校验——对当前作用域**已有的每个 key** 跑 `isKeyVisible`,回写 `{verify:{key: true|false}}`。用于**改完某功能后,一键定位哪些 key 已失效**、需要更新。

## 7. runner 改造(从 API 拉注册表,回落内置文件)

- `gui-core.mjs`:`createGuiCore` 增加接受 `registry`/`vmIframe` **对象**入参;新增 `setRegistry(registry, vmIframe)` 就地换注册表(把内部 `REGISTRY/VM_IFRAME` 由 `const` 改 `let`,不动 browser 连接)。默认仍可 `readFileSync` 内置文件(back-compat / API 不可达兜底)。
- `runner.mjs`:
  - 新增 `fetchRegistry(project_id, sub_product)` → `GET /api/selectors`,按 `(project_id, sub_product)` + `version` 缓存;失败回落内置 `selectors.json`(记 warning)。
  - 执行 gui/e2e 前:据工作项 `payload.project_id`(+ sub_product)取注册表 → `setRegistry` → 再 `runScript`。
  - exec-queue 的 `payload` 增补 `project_id`(§9)。执行侧 v1 用 `sub_product=''`(共享),与生成侧一致。

## 8. 生成侧改造(读 DB,校验权威化)

- `_load_selector_keys()` / `_registered_keys()` 改为**按 project 读 DB**(项目级共享 keys)。当前签名无 project 参数——调用链 `build_testcase_prompt(requirement)` / `parse_testcases(raw)` 需透传 `project_id`。
  - `api/ai.py::gen_testcases` 已有 `body.project_id`,传入。
  - `gen_script` 用 `tc.project_id`。
- v1 生成/校验只用**项目级共享**(sub_product='');不引入子产品维度(见 §9 决策 a)。
- DB 读不到(项目无注册表)→ 返回空集:prompt 不注入 key 清单、校验放行(与"文件读不到"的既有兜底一致,不误伤)。

## 9. 生成页子产品维度(决策:a)

AITestGen 生成页维持"项目 + 任务",**不加子产品下拉**。生成/校验用项目级共享 key。子产品精度以后需要再加。

## 10. 前端「选择器管理」页

- 路由:`/selectors`,菜单在合适位置;`meta` 需项目 admin/member(参 `router` 守卫)。
- 布局:
  - 顶部:项目选择 + 子产品选择(含「项目级共享」项)。
  - 左:注册表浏览——分层展示「共享」与「当前子产品专属」,列 key/desc/frame/候选;可编辑/删除。
  - 右:探测面板——选在线设备(复用 `listMyDevices` / 设备在线状态)。两种动作:
    - **探测(discover)**:可选关键词过滤 → 点「探测」→ 轮询 `GET /api/probe/{id}` → 展示 `groups`(shell/vm 分组的元素及 best 候选)→ 每个元素「加为 key」弹表单:
      - **新建**(预填 best 候选、frame)→ `POST /api/selectors`(落当前作用域);
      - **更新已有(E1)**:选当前作用域一个已有 key → 把 best 候选**追加到头部或替换** → `PATCH /api/selectors/{id}`。用于功能改动后更新失效 key。
    - **校验失效 key(verify,E2)**:点「校验」→ probe `mode=verify` → 展示当前作用域每个 key 的命中/失效;失效项就地提供「重新探测更新」入口(切到 discover + 该 key 的更新模式)。
  - 顶部动作:「导入内置纳米Work注册表到当前项目」(`POST /api/selectors/import-legacy`,项目 admin)。
- `api/index.js` 增薄封装函数;沿用响应拦截解包。

## 11. 迁移与 seed

- `migrate.py::ensure_selector_tables()`:建 `selector_key` / `selector_scope`(幂等)。
- `schema.sql` 补两张表(MySQL,`candidates`/`vm_iframe` 用 TEXT/VARCHAR)。
- **seed 不在迁移时自动做**(dev/prod 项目 id 不同、项目可能未建)。改为**管理页显式「导入」**:读打包在仓库的旧 `selectors.json`,插入到所选项目的共享层 + 写 `selector_scope.vm_iframe`。生产由操作者对纳米Work 项目点一次。
- 旧 `selectors.json` 文件**保留在 git 作备份 / runner 兜底**,不再是事实来源。

## 12. 数据流

**探测写回**:
```
网页(选设备/作用域)→ POST /api/probe(pending)
   → 设备 runner 轮询 GET /api/probe/pending → setRegistry → gui.probe()
   → PATCH /api/probe/{id}(result) → 网页轮询 GET 展示
   → 用户「加为 key」→ POST /api/selectors(写 DB)
下次生成/执行读 DB → 立即可用(设备下次拉注册表即最新)
```

**生成校验**:
```
gen_testcases(project_id) → _load_selector_keys(project_id) 读 DB 共享层
   → 注入 prompt / _validate_script(valid_keys) 权威校验
```

## 13. 错误处理

- 探测:设备离线/未响应 → probe_request 留 pending,网页轮询设超时(如 60s)提示;runner 探测抛错 → PATCH error,网页显示。
- 注册表拉取:runner `GET /api/selectors` 失败 → 回落内置文件 + warning,不中断执行。
- 权限:非项目 admin/member 调管理/探测接口 → 403(统一信封)。
- key 校验:DB 空集时放行(不因环境问题把 gui/e2e 全降 manual)。

## 14. 测试(本仓库无自动化测试框架,手动端到端)

- 后端:TestClient 冒烟——建 key、GET 合并、import-legacy 幂等、probe 建/查、rbac 403。
- runner:`--dry` 模式验证 probe 拉取/回写连通;setRegistry 换注册表后 resolveKey 生效。
- 前端:探测面板走通"选设备→探测→加为 key→列表出现"。
- 迁移:老库(无表)启动后建表;import 到演示项目验证。

## 15. 假设与开放点

- **seed 目标项目**:生产上纳米Work 对应的项目由操作者在管理页选择后点「导入」;dev 无纳米Work 项目(仅演示项目)。
- **子产品专属 key 的即时价值**:v1 执行/生成走共享层,故探测写到某子产品专属的 key 要到"子产品作用域被消费"(后续子产品级执行 / 手写 script)才生效;merge 模型对此前向兼容。
- **version 语义**:先用 `max(updated_at)` 时间戳;若并发编辑频繁再换哈希。

## 16. 涉及文件清单(实现时参照)

- 新增:`backend/app/models/selector.py`、`backend/app/schemas/selector.py`、`backend/app/api/selectors.py`、`backend/app/api/probe.py`、`frontend/src/views/SelectorAdmin.vue`
- 改:`backend/app/db/migrate.py`、`backend/sql/schema.sql`、`backend/app/models/__init__.py`、`backend/app/main.py`(注册路由/迁移)、`backend/app/services/claude_runner.py`(读 DB)、`backend/app/services/generators/deepseek_runner.py`(透传)、`backend/app/api/ai.py`(透传 project_id)、`backend/app/api/exec_queue.py`(payload 补 project_id)、`tools/qalab-runner/runner.mjs`、`tools/qalab-runner/gui-mcp/gui-core.mjs`、`frontend/src/api/index.js`、`frontend/src/router/index.js`、前端菜单
