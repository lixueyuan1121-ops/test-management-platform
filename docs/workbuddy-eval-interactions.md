# WorkBuddy 测评模型选择、反问与分享交互

## 行为

- macOS 启动路径可填 `.app` 或内部可执行文件。启动前先解析并校验文件及权限，再按配置关闭旧进程；异步 `EACCES`/`ENOENT` 和启动后提前退出会正常向上层报错，由平台记录该批连接失败。只有收到进程的 `spawn` 事件才记录“已启动”。调试端口已就绪时直接连接，无需本机启动文件可用。
- 指定模型时展开当前模型列表，按纯名称查找；忽略英文大小写及空白，如 `glm-5.3`、`GlM-5.3` 均匹配 `GLM-5.3`。优惠标签和 `0.79x` 等积分倍率不参与名称比较。兼容旧执行配置中的 `item-info` 名称选择器。
- 列表等待最多 4 秒以容纳异步加载，查找范围包含已挂载的屏外选项；找到后滚动到可见位置再点击，并回读已选模型确认生效。未指定模型时只记录当前值。
- 不同版本和后缀保持严格区分，`GLM-5.3` 不会替换成 `GLM-5.3-Flash`。不存在时列出当前模型名称；模型不可选、同名歧义、列表未加载或选中后读回不符均明确失败，阻止发送该轮问题。平台失败原因只显示一次错误码前缀。
- 等待任务完成期间轮询反问卡片。保留已有预选项；没有预选时选择第一个可用选项。
- 支持分页反问：中间题继续下一题，末题发送/提交。单选点击后会自动翻页的界面也支持；多选不重复切换已有选项。
- 工作流/计划执行确认支持 `exit-plan-mode-floating`、`conversation-exit-plan-panel`、`pending-plan-panel` 三种客户端组件。点击默认“开始执行”项直接提交，不误点用于修改计划的“发送”；提交期间等待，已批准的结果面板不阻塞完成判断。
- 卡片可见时不把中间输出判定为完成。无可用选项的自由输入题保持待回答，超过任务时限记为未完成，并跳过同会话后续轮。
- 进入分享底栏后先检查“全选”：已勾选时保留，未勾选或部分选择时点击，并等待 `aria-checked=true` 确认生效，再点击复制链接。全选控件缺失或选择未生效时不复制，退出后重试。
- 分享使用当前可见底栏里的复制按钮，等待其可点击。写入独立剪贴板标记后，只接收本次更新得到的 `workbuddy.link/p/` 链接。
- 点击失败或复制超时会退出分享，再重试一次；所有结束路径均检查并关闭底栏。新建任务及同会话下一轮发送前再次检查遗留分享态。
- 新建任务点击失败明确报告错误，避免把下一任务误发到上一会话。

反问处理限定在 WorkBuddy 问题卡片中，不通过全页面匹配“继续”按钮。选择器使用语义类名前缀，避免绑定 CSS Module 的版本哈希。

## 验证

2026-09-14 模型选择修复：检查 macOS WorkBuddy 5.5.6 实际下拉菜单，确认 `GLM-5.3` 与 `GLM-5.3-Flash` 是独立选项，整行还包含倍率/优惠文字。对照本机客户端组件确认名称使用 `cr-model-selector__item-name`，列表选项全部挂载在滚动容器中。回归在合成 Chromium 页面中验证名称与附加信息分离、大小写、屏外项、隐藏同名项、异步加载、旧选择器、缺失/禁用/歧义、回读失败和发送拦截；另验证错误码回写去重。

同日启动修复追加真机验证：确认本机 WorkBuddy 未运行后，以 `/Applications/WorkBuddy.app` 和 `killExisting: false` 调用执行器。应用包解析为 `Contents/MacOS/Electron`，进程启动、9335 端口连接和输入框就绪均通过；随后执行器将 `gLm-5.3` 匹配为 `GLM-5.3` 并回读验证，最后恢复原模型。未发送模型对话或写入业务测评结果。14 项启动回归覆盖包路径、空格、重命名程序、无效路径/权限、真实异步 EACCES/ENOENT、启动事件时序、提前退出、复用已就绪端口及关闭旧客户端前校验。

```sh
cd tools/qalab-runner/eval
node --test test/workbuddy-model-selection.test.js test/execution-result.test.js test/dialog-config.test.js test/workbuddy-interactions.test.js
node --test test/workbuddy-launch.test.js
```

2026-09-11，在 macOS WorkBuddy 5.5.6（单窗口、单 frame）通过 CDP 实测：

1. 合成反问询问“简洁/详细”和“中文/英文”，执行器保留默认的简洁、中文并提交，得到最终回答。
2. 同会话追加下一轮，得到“下一轮通过”。
3. 新建下一任务，得到“新任务通过”。
4. 三次均成功抓到分享 URL，分享底栏消失，输入区域可见。未向业务测评队列写入合成结果。

工作流追加验证：当前模型使用 AskUserQuestion 展示“开始执行 / 取消 / 修改工作流”确认卡。执行器自动提交默认执行项，得到“工作流确认通过”，成功复制分享链接，随后同会话得到“工作流后续对话通过”。本轮模型未提供 EnterPlanMode / ExitPlanMode 工具，因此独立计划执行面板的三种组件依据本机客户端代码核对结构，并通过 Chromium DOM 回归验证，未宣称完成其模型驱动的真机端到端验证。

分享全选追加验证：在上述合成对话中，通过真实界面取消全部分享选择，确认全选框及四条消息的复选框均为 `aria-checked=false`。执行器将五个复选框全部选中后成功复制分享 URL，底栏消失、输入框恢复；同会话下一轮得到“分享全选后续对话通过”。28 项交互回归及 7 项相关回归全部通过。

自动化回归使用真实 Chromium DOM，覆盖默认项、多选、分页、暂禁用按钮、反问超时、分享全选为空/部分选择/已勾选、全选延迟生效/未生效/控件缺失、隐藏同名复制按钮、剪贴板未更新、权限失败、重试及下一任务恢复：

```sh
cd tools/qalab-runner/eval
node --test test/workbuddy-interactions.test.js
node --test test/workbuddy-duration.test.js test/workbuddy-capture-integration.test.js test/conversation-share-capture.test.js test/conv-has-attachments.test.js test/clipboard-file.test.js
```

Windows 客户端未做本次真机验证。平台运行配置仍应指向执行机实际使用的 WorkBuddy 程序和 CDP 端口。

## 生效范围

改动属于 `tools/qalab-runner/eval/`，需要执行机加载新版 runner 才生效。只重启平台后端不能替换执行机内存中的旧执行器。

现有平台模式在启动时检查 runner 更新，常驻期间每 30 分钟在任务批次之间检查一次；通过 `run-eval.sh` / `run-eval.cmd` 启动时，下载新版后由外层脚本重启加载。发布前应确保平台提供的 runner bundle 已包含上述改动。
