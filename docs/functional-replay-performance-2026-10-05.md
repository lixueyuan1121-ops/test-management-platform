# 功能回归：前置脚本、耗时与 Codex 引擎

## 执行路径

普通用例保持原有流程。非空文字前置条件仍交给 AI 导航，避免未经验证就跳过业务前提；纯脚本回归在执行前拒绝文字前置条件和 AI judge。

在用例库打开“详情 → script → 编辑”，勾选“将前置操作合入脚本”，填写现场确认过的前置步骤 JSON 和到位断言。确认保存后，前置步骤追加到主脚本开头，原前置文字保留在步骤说明中，precondition 清空。保存只改变草稿，不能视为已验证，随后下发两次严格回归。

本地 Skill 回填草稿也支持 `setup_script`：Runner 将其与主 script 合并后执行两次，输出原有平台 DSL 格式的 verified.json。不能只清空 precondition 而遗漏导航或数据准备；前置到位断言不能代替主脚本的业务断言。已在完整 script 中包含前置动作时，不再重复填写 setup_script。

## 耗时口径

新 Runner 的 duration_ms 是从接手用例到证据准备完成的设备总耗时。报告展示客户端准备、复位、AI 前置导航、脚本步骤、AI 兜底、追踪和证据上传等实际发生的阶段。每个已执行步骤带 duration_ms，失败步骤也保留时间。脚本阶段含断言截图与主观判定时间；阶段之和可能少于总时间，因为还有本地处理和选择器建议回传等收尾。

平台依据数据库 created_at/started_at/finished_at 计算排队和执行墙钟时间，避免用不同机器的时钟相减。外部导入不会伪装成队列排队耗时，旧 Runner 不补造阶段数据。运行中尚无完整报告，手动刷新结果查看完成情况；本次不增加列表自动轮询。

## 可选 Codex

设备 `.env` 可设置：

```dotenv
GUI_AI_ENGINE=codex
CODEX_BIN=codex
CODEX_TIMEOUT_MS=240000
# 可选：CODEX_MODEL 使用该账号支持的模型，不填使用 CLI 默认。
# 可选：CODEX_RUNNER_HOME 指向单独登录目录的绝对路径。
```

默认仍为 claude；纯脚本不受这个选择影响。API/CLI 类型保持原有路径。Codex 仅替换 GUI/E2E 前置导航、judge 与 AI 兜底；未安装、未登录、额度不足或调用失败时记录失败，不偷偷换回 Claude 执行同一业务操作。

需使用支持 `exec --json --ephemeral --ignore-user-config` 的官方 Codex CLI。本次命令/配置兼容性核验使用 0.160.0。若 `codex` 是失效包装器，CODEX_BIN 可直接指定官方原生可执行文件，或 `@openai/codex/bin/codex.js`；Windows npm 的 codex.cmd 会解析为相邻的 Node 入口，不经 shell 拼接用户输入。

执行复用 CLI 登录，但通过 `--ignore-user-config` 忽略个人 config.toml；关闭插件、钩子、Shell、浏览器工具与协作工具，只注入本项目 GUI MCP。judge 不注入 GUI 工具。模型若需要显式选择，用 CODEX_MODEL；个人配置中的自定义模型供应商不在这条适配路径自动继承。无需复制登录凭据，Runner token 不转发给 Codex。组织级管理策略仍生效。

先在 `tools/qalab-runner/gui-mcp` 安装 npm 依赖。Codex 仅为固定 GUI 工具清单配置逐工具无人值守授权；没有放开其他 MCP 或新增工具的默认授权。最终截图由 Runner 采集，不向模型开放任意路径写文件工具。

Codex 的 GUI 通过结论必须伴随实际断言工具返回的 pass=true；工具调用完成本身不算断言通过。超时和取消走自有进程树停止逻辑。

接口依据：[官方非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)，并用实际 CLI 帮助及配置解析核验。这里没有宣称 Codex 比 Claude 更快；应在相同用例、设备和起始状态下比较实际报告。

## 发布与验证

先更新平台后端/前端，再在设备空闲时更新 Runner，最后分发仓库内回填 Skill。不需要数据库新增列；阶段数据存在执行 payload 中。不要在运行中覆盖执行机代码或配置。

自动测试覆盖合并前置步骤、纯脚本零 AI 调用、Codex 协议/错误/超时、进程清理、证据兼容和耗时存取，以及前端编辑确认、耗时展示和原有终止流程。测试使用隔离数据，没有批量改写历史用例，也没有重跑线上业务批次。真实业务速度和 Windows 原生运行仍需要目标设备验证。

本机真实联调已用官方 Codex CLI 0.160.0 完成隔离 Chromium 页面的连接、点击和文本断言，收到实际 `pass=true`，并核对页面内容。成功轮次总耗时约 60 秒；另一次因模型服务反复重连触及 90 秒测试上限，因此不能据此宣称切换引擎必然提速。正式默认上限为 240 秒。分发测试使用临时 Git 索引纳入新增模块，验证独立包启动、升级和配置保留，未修改实际暂存区。
