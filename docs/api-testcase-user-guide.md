# 可执行 API 测试用例 · 使用说明

> 面向 QA / 项目管理员:如何在本平台配置、生成并执行 **api 类型**的自动化测试用例。
> 设计背景见 [`docs/superpowers/specs/2026-08-18-api-executable-testcases-design.md`](./superpowers/specs/2026-08-18-api-executable-testcases-design.md)。

## 它解决什么

api 用例过去只有自然语言 steps/expected,执行时靠 AI 临场拼 curl——不可复现、断言松、易假阳性。现在 api 用例带一份**结构化可执行脚本**(`script`),由执行机上的确定性执行器(Node 原生 fetch,**不起 AI**)逐步跑:发请求 → 判断言 → 提取变量 → 传递给下一步 → 收尾清理。

一句话:**可执行 api 用例 = 接口契约(打哪 / 带什么 / 期望什么)+ 场景意图(测什么情况 / 怎么算对)。**

---

## ⚠️ 适用边界 —— 先读这条,别白折腾

api 用例能在**执行机上、脱离被测客户端**直接发请求。这要求被测系统的鉴权**能在客户端外复现**:

- ✅ **适用**:固定 token/header 就能调、或"登录接口换 token 再调"的标准后端(REST + 稳定鉴权)。
- ❌ **不适用**:被测系统是**客户端内部动态签名鉴权**的——典型如 **namiwork / claw**(纳米Work、安全龙虾)这类:
  - base_url 是**一次性会话哈希子域名**(`https://<vm_id>.work.n.cn`),每人 / 每会话不同、会过期;
  - 每个请求的 token/签名(`access-token`、`zm-token`、`timestamp` 等)由**客户端 JS 拦截器每次现算**,在客户端外**无法复现**(实测:抓客户端真实请求头到外部原样重放,几秒内即 `4001 用户登录失败`;不带 → `110001 Unauthorized`)。
  - 对这类系统,**无论怎么在「api 环境」里填 token 都会很快失效**。正确做法:**改用 gui / e2e 用例**——在客户端界面上操作、断言界面结果,鉴权/签名/vm_id 全由客户端自理,平台一个都不用配。

> 判断口诀:**接口能不能在 Postman/curl 里稳定调通?** 能 → api 用例;不能(只有客户端内才调得动)→ gui/e2e 用例。
>
> 好消息:AI 生成时已内置这个倾向——**项目没配 api 契约时,会把本可判 api 的验证点自动改判 gui/e2e**(见下方「AI 生成」),你多数时候不用手动纠。

---

## 全流程一览

```
① 配 api 环境          ② 生成 / 编写用例        ③ 下发执行             ④ 看结果
项目→「api 环境」   →   AI 测试助手 生成      →  勾选用例→发送到    →  「执行结果」看
base_url/鉴权/契约      (或手写 script)          本地执行机             verdict/reason
```

---

## ① 配置项目 api 环境(一次,全项目复用)

菜单:**设计 → api 环境**(需**项目管理员**;含被测系统凭据,成员/嘉宾不可见)。

| 字段 | 说明 |
|---|---|
| **base_url** | 被测系统地址,如 `https://biz.example.com`。执行时以此为前缀拼接每步的相对 path。 |
| **鉴权方式** | `固定 token/header`:所有请求预置同一鉴权头;`用例内登录取 token`:token 由用例的登录步骤动态取得。 |
| **固定鉴权 / 登录信息** | JSON 对象。固定模式填 `{"headers":{"Authorization":"Bearer xxx"}}`,执行器把 `headers` 预置进每个请求;登录模式填 `{}` 即可。 |
| **接口契约** | 每行一个接口的清单,注入 AI 生成 prompt,让它"打对接口"。越全,生成质量越高。 |

### 契约怎么来 —— 三条路,可混用

1. **粘贴 curl**(推荐,最快):点「粘贴 curl 解析」,贴一条 curl(支持浏览器「复制为 cURL」)。
   - **鉴权头会被自动剥离**(Authorization/Cookie 等不入库、不进脚本)——真实 token 会过期且不安全,鉴权统一由上面的「鉴权方式」注入。
   - 解析后可「并入契约」(加一行到契约),或「复制 script 种子」(粘到某条用例里再补断言/清理)。
2. **导入 OpenAPI/Swagger**:点「导入 OpenAPI/Swagger」,粘贴 `openapi.json` / `swagger.json` 的**内容**。
   - 出于安全**不在服务端拉取 URL**(避免被诱导访问内网),请自行获取内容后粘贴。
   - 解析出接口清单后可「追加到契约」或「替换契约」。
3. **手写**:直接在契约文本框按格式写,如:
   ```
   GET /api/users 用户列表(page,size)
   POST /api/users 创建用户{name,email}
   DELETE /api/users/{id} 删除用户
   ```

> 改完记得点**保存**落库。

---

## ② 生成或编写 api 用例

### A. 用 AI 生成(推荐)

菜单:**设计 → AI 测试助手**。选项目后,若项目已配契约,页面会展示**「本项目 api 接口清单」**:

- 逐条点「引用」把接口加进需求;
- 点「插入 api 用例需求模板」,按**关联接口 / 测试场景 / 业务判定**三段填写(见下)。

需求写好 → 生成。AI 会为 api 用例产出结构化 `script`;**过不了校验的会自动降级为「人工(manual)」**(不会派坏脚本到执行机)。

**需求要写什么(按契约完备度分档):**

| 情况 | 需求要写 |
|---|---|
| 项目已配契约(最省) | 只写**业务意图 + 场景**:测哪个接口、什么场景(正常/必填/重复/越权)、什么算成功(错误码含义)。接口细节 AI 从契约查。 |
| 无契约,需求自带 | 需补 method+path、每个参数(名/位置/类型/必填/**示例值**)、鉴权方式、成功响应结构 + 关键字段 + code 约定。 |
| 只写一句"测下用户接口" | 无契约可依 → 降级 manual,不产脚本。**这是保护,不是 bug。** |

### B. 手写 / 修改 script

用例的 `script` 是一个**请求-断言-提取原子**的有序数组。单步结构:

```jsonc
{
  "name": "创建项目",                                   // 人读名
  "request": {
    "method": "POST",                                  // GET/POST/PUT/PATCH/DELETE
    "path": "/api/projects/{{pid}}",                   // 相对路径,可含 {{变量}}
    "headers": { "Authorization": "Bearer {{token}}" },// 可选
    "query": { "page": 1 },                            // 可选
    "body": { "name": "自动化项目" }                    // 可选
  },
  "asserts": [                                         // 至少 1 个
    { "type": "status",   "op": "eq", "value": 200 },
    { "type": "jsonpath", "path": "code",    "op": "eq",     "value": 0 },
    { "type": "jsonpath", "path": "data.id", "op": "exists" }
  ],
  "extract": { "pid": "data.id" },                     // 可选:存变量供后续步骤 {{pid}} 引用
  "cleanup": false                                     // 可选:标记为清理步骤
}
```

**断言** `type`:`status`(状态码)/ `jsonpath`(响应体字段,用点路径如 `data.list.0.id`)。
**op 全集**:`eq / neq / exists / contains / gt / lt / regex / type`(除 `exists` 外都需 `value`)。

**变量与链式**:`extract` 从本步响应取值存入变量表,后续步骤请求里的 `{{变量}}` 在发请求前被替换。变量表在**一条用例执行周期内**存活——登录取 token、创建取 id 全靠它。

**清理(cleanup)**:标 `cleanup:true` 的步骤,**无论前面成败都执行、多个逆序执行、其断言失败不算用例失败**——用于删掉测试造的数据。

完整示例(登录 → 创建 → 查询 → 清理):

```jsonc
[
  { "name": "登录", "request": { "method": "POST", "path": "/api/auth/login",
      "body": { "username": "qa", "password": "..." } },
    "asserts": [{ "type": "jsonpath", "path": "code", "op": "eq", "value": 0 }],
    "extract": { "token": "data.access_token" } },
  { "name": "创建", "request": { "method": "POST", "path": "/api/items",
      "headers": { "Authorization": "Bearer {{token}}" }, "body": { "name": "x" } },
    "asserts": [{ "type": "jsonpath", "path": "data.id", "op": "exists" }],
    "extract": { "id": "data.id" } },
  { "name": "查询", "request": { "method": "GET", "path": "/api/items/{{id}}",
      "headers": { "Authorization": "Bearer {{token}}" } },
    "asserts": [{ "type": "jsonpath", "path": "code", "op": "eq", "value": 0 }] },
  { "name": "清理", "cleanup": true, "request": { "method": "DELETE", "path": "/api/items/{{id}}",
      "headers": { "Authorization": "Bearer {{token}}" } },
    "asserts": [{ "type": "status", "op": "eq", "value": 200 }] }
]
```

**生成/保存时的硬规则**(违反即降级 manual):
- 每步 `request.method` 合法、`path` 非空;
- 每步 `asserts` 至少 1 个;`jsonpath` 断言必须有 `path`;需值的 op 必须有 `value`;
- **变量闭环**:任何 `{{变量}}` 必须在**之前某步的 `extract`** 里定义过(或来自固定鉴权注入);
- **写操作必带清理**:含 POST/PUT/PATCH/DELETE 的脚本,必须有至少一个 `cleanup:true` 步骤(避免残留脏数据)。

> 单条用例可在用例详情点「重新生成 script」让 AI 按当前 steps/expected 重出(gui/e2e/api 均支持)。

---

## ③ 下发到执行机

1. 用例进入某任务的**验收清单**后,在清单里**勾选** api 用例;
2. 点**「发送到本地执行」**,选择目标执行机(runner)下发;
3. 执行机(运行 `tools/qalab-runner/runner.mjs` 的机器)轮询拉取 → 认领 → **确定性执行** → 回写结果。

下发时平台会把**该项目 api 环境的快照**(base_url + 鉴权)一并塞进任务,保证"下发那一刻的配置",避免执行时配置漂移。

---

## ④ 查看结果

菜单:**执行 → 执行结果**。每次执行一行(不覆盖,可复测追溯):

- **verdict**:`pass` / `fail`;
- **reason**:精确到**步 / 断言 / 期望 vs 实际**,如
  `step2「创建」断言失败: jsonpath data.id 期望 exists,实际 undefined`;
- 结果同步回验收清单项状态(pass→passed / fail→failed),下游统计、失败转遗留问题照常联动。

---

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 执行直接 fail:`项目未配置 api 环境(base_url 为空)` | 去「api 环境」配 base_url 并保存。 |
| api 用例被生成成了「人工(manual)」 | 无契约可依,或脚本违反硬规则(缺断言/变量不闭环/写操作缺清理)。补契约或按规则修脚本后「重新生成 script」。 |
| 某步 401/403 | 鉴权没配对。固定模式检查「api 环境」的 headers;登录模式检查登录步是否正确 `extract` 了 token、后续步是否带 `{{token}}`。 |
| 所有 api 用例统一 fail:`code:110001 Unauthorized` / `4001 用户登录失败` | 被测系统是**客户端内动态签名鉴权**(如 namiwork/claw),api 在客户端外无法鉴权。**别再填 token**——把这些用例改判 **gui/e2e**(见上方「适用边界」)。 |
| 提取失败:`响应无路径 data.xxx` | 点路径写错,或该字段在响应里不存在。对照实际响应结构改 `extract` 的点路径。 |
| 想验证越权/异常码 | 断言直接写期望的失败码,如 `{"type":"status","op":"eq","value":403}`;边界用例在 body 里给具体示例值(超长名、缺必填等)。 |

## 能力边界(暂不支持)

- 跨用例依赖(用例 A 的变量传给用例 B)——只做单用例内多步;
- multipart / 文件上传、cookie 鉴权、GraphQL;
- 响应体 JSON 之外的断言(XML / 二进制)。

遇到上述场景,先把用例判为 manual 人工执行。
