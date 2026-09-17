# 纳米Work测评组合执行

在「测评任务 → 执行」中关闭 A/B 对比后：

- 对话模式、思考深度可多选；模型可按行填写，也支持中英文逗号、分号。
- 模型名去除首尾空格，忽略英文大小写去重；模型名内部空格、斜杠保留。
- 每道用例按「模式 × 模型 × 深度 × 独立执行次数」展开。空维度按一次默认配置处理。
- 界面显示组合数和总执行记录数。例如 10 道单轮题、2 种模式、3 个模型、2 档深度、1 次独立执行，共 120 条。
- 多轮题在每种组合、每次独立执行中保持完整会话；每轮仍占一条执行记录。认领、重跑、上下文与结果分组均按独立会话隔离。
- 同时选择 WorkBuddy 时，单独填写其模型。其执行次数不乘纳米Work组合数。
- A/B 对比继续使用两套单一配置，与组合执行互斥。
- 最近一次配置可回填，定时任务复用相同组合。结果列表、详情、导出及综合评价保留配置；重复执行统计按配置分别聚合。

单批最多 300 种组合、10,000 条执行记录；模型名最长 64 字符。超限或非法参数在创建记录前整体拒绝。

## 接口与兼容性

`POST /api/eval-tasks/{id}/run` 新增可选 `dialog_options_matrix`，只对纳米Work生效：

```json
{
  "runners": ["runner-01", "runner-02"],
  "target_engines": ["namiwork", "workbuddy"],
  "trial_count": 1,
  "dialog_options_matrix": {
    "chatMode": ["边想边做", "先规划，再执行"],
    "model": ["GLM-5.3", "豆包（seed-2.1）"],
    "thinkingDepth": ["标准", "高"]
  },
  "dialog_options": {"model": "WorkBuddy模型名"}
}
```

此例每道用例下发 8 条纳米Work和 1 条WorkBuddy执行。原有不传矩阵的标量接口、A/B语义保持兼容。矩阵三项都空时，继续沿用题目保存的选项；矩阵中有指定项时使用该组合，未指定项沿用客户端状态，与原标量执行规则一致。

任务的 `dialog_options.matrix` 保存规范化选项。每条纳米Work执行的 `payload.dialog_options` 仍为单套字符串配置，额外记录 `configuration_id/index/count/label`。runner协议无需升级，不涉及数据库列迁移；上线需同时更新前后端。

## 离线验证

- 后端：`cd backend && .venv/bin/python -m scripts.test_eval_dialog_matrix`
- 前端逻辑：`cd frontend && node --test tests/dialog-matrix.test.mjs tests/result-integrity.test.js`
- 页面：启动本机 Vite 后执行 `UI_BASE_URL=http://127.0.0.1:5197 node frontend/tests/eval-dialog-matrix-ui.cjs`；全部业务 API 模拟，不下发真实任务。
