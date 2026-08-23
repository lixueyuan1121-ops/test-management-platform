# 设计:对话测评用例库(eval_query 历史 + 再次触发)

- 日期:2026-08-23
- 状态:已评审(用户 /goal 追加需求 + 确认"独立新页")
- 所属:对话测评链路 · 子项B(平台侧独立功能)
- 范围:eval_query 生成后已落库,但无列表端点、无历史页,生成结果只在 AIEvalGen 页内存里、刷新即丢。补:①GET 列表端点 ②独立新页"对话测评用例库"看历史 + 勾选**再次下发**验证。
- 关联代码:`backend/app/api/ai_eval.py`(加 GET 列表端点,复用现有 `_to_query_out`)、`frontend/src/views/EvalLibrary.vue`(新页)、`frontend/src/router/index.js`、`frontend/src/layouts/MainLayout.vue`、`frontend/src/api/index.js`。

## 1. 背景与问题
子项1 的 eval_query 生成端点(POST /api/ai/eval-queries,SSE)生成后**落库 EvalQuery**,但:
- 无任何列表/历史查询端点。
- 前端 AIEvalGen 只在内存展示本次生成结果,离开/刷新即丢。
- 想"再拿之前生成的 query 去验证某设备"没有入口。

功能测试点那侧有对称的"用例库"(/case-library);对话测评这侧缺。

## 2. 目标与非目标
**目标**
- 后端 `GET /api/ai/eval-queries?project_id=X`:列出该项目历史生成的 eval_query(复用 `_to_query_out`)。
- 前端独立新页"对话测评用例库"(/eval-library):选项目 → 列表(维度/标题/prompt/expected/对话组/轮次/时间/评审态)→ 勾选 → **再次下发**(复用 AIEvalGen 的"选执行机 + 目标设备下拉 + enqueue")。
- 导航挂"测试设计"菜单,紧邻"对话测评生成"。

**非目标(YAGNI)**
- 不做 query 编辑/删除/评审态变更(本子项只读 + 再下发)。
- 不改生成链路、判定、回填。
- 不做分页(先 limit 上限,数据量大再说)。

## 3. 关键决策
| # | 决策 | 选择 |
|---|---|---|
| 1 | 历史页形态 | **独立新页 /eval-library**(与 /case-library 对称,用户已确认)。 |
| 2 | 列表端点位置 | `GET /api/ai/eval-queries`(与 POST 同路径不同方法,放 ai_eval.py,复用 _to_query_out)。 |
| 3 | 鉴权 | assert_project_role(admin/member/**guest** 可读,查看类放宽,同 case 列表)。 |
| 4 | 再次下发 | 复用现有 `enqueueEvalQueries` + 设备下拉(`listEvalDevices`/loadClientDevices)。目标设备可空=用执行机当前设备。 |
| 5 | 排序/限量 | created_at desc,limit 默认 200(le=500)。 |
| 6 | 设备下拉复用 | EvalLibrary 内自建一份 loadClientDevices/chosenRunner/chosenDevice(与 AIEvalGen 同款小逻辑;不强抽公共组件,YAGNI)。 |

## 4. 后端(ai_eval.py 加 GET 列表端点)

```python
@router.get("/eval-queries")
def list_eval_queries(project_id: int = Query(...), limit: int = Query(200, le=500),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalQuery).filter(EvalQuery.project_id == project_id)
            .order_by(EvalQuery.id.desc()).limit(limit).all())
    return ok([_to_query_out(q) for q in rows])
```
- 需 import `Query`(fastapi)、`ok`(schemas.common)。现文件已 import assert_project_role/ProjectRole/EvalQuery/get_current_user。
- 复用现有 `_to_query_out`(已含 id/title/dimension/prompt/expected/attachments/conversation_group/turn_index/review_status/created_at)。

## 5. 前端

### 5.1 api/index.js
```javascript
// 对话测评用例库:某项目历史生成的 eval_query 列表(再次触发验证用)。
export const listEvalQueries = (projectId) => http.get('/ai/eval-queries', { params: { project_id: projectId } })
```
(listEvalDevices/enqueueEvalQueries 子项已有,复用。)

### 5.2 EvalLibrary.vue(新页)
- 顶部:项目选择(el-select,复用 pickDefaultProjectId/setLastProjectId 模式)。
- 主体:el-table(勾选列 + 维度 tag + 标题 + prompt + expected + 对话组 + 轮次 + 生成时间 + 评审态),多轮按 conversation_group 聚拢。
- 工具栏(选中≥1 时):选执行机(chosenRunner,listMyDevices)→ 选目标设备(chosenDevice,listEvalDevices,可空)→"下发选中到执行机"(enqueueEvalQueries)。
- 复用 AIEvalGen 的维度标签映射(DIMENSIONS/DIM_TYPE/dimLabel)、设备下拉逻辑(loadClientDevices)。
- 空态:提示"该项目暂无生成的对话测评 query,去『对话测评生成』生成"。

### 5.3 路由 + 导航
- router/index.js:加 `{ path: 'eval-library', name: 'eval-library', component: () => import('@/views/EvalLibrary.vue'), meta: { title: '对话测评用例库' } }`(在 ai-eval-gen 附近)。
- MainLayout.vue:测试设计子菜单,"对话测评生成"(/ai-eval-gen)下面加 `<el-menu-item index="/eval-library"><el-icon><Collection /></el-icon><span>对话测评用例库</span></el-menu-item>`。

## 6. 迁移与 schema
- 无 schema/DB 变更(EvalQuery 表子项1 已建、生成时已落库)。纯读端点 + 前端页。

## 7. 影响面与风险
- **隔离**:新增 GET 端点(与 POST 生成共存)、新前端页,不改生成/下发/判定/回填。
- **风险1(数据量)**:项目 query 多时列表大。缓解:limit 500 上限;分页留后续。
- **风险2(再下发重复)**:同一 query 可反复下发生成多条 eval_run——这是**预期**(再次验证)。
- **风险3(真验证)**:前端 build + 端到端看历史+再下发。

## 8. 验证方式
1. 后端:插几条 EvalQuery → GET /api/ai/eval-queries 返回列表(排序/字段);guest 可读、非成员 403。脚本验证。
2. 前端:npm build 过;页面列表渲染、勾选、再下发调 enqueue。
3. 端到端:生成 query → 进用例库看到 → 勾选 → 选设备 → 下发 → eval_run 出现。

## 9. 交付清单
- [ ] ai_eval.py:GET /eval-queries 列表端点(复用 _to_query_out)
- [ ] api/index.js:listEvalQueries
- [ ] EvalLibrary.vue:项目选 + 列表 + 勾选 + 再下发(设备下拉复用)
- [ ] router + MainLayout 导航
- [ ] 后端脚本验证 + 前端 build + 端到端
