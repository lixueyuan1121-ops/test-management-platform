# gui-mcp —— 纳米Work GUI 自动化 MCP server + 语义选择器库

把纳米Work(Electron,UI=work.n.cn)的 CDP 操作封装成 Claude Code 可调用的固定工具(`mcp__gui__*`),
用例只说"定位哪个元素、断言什么",不现写 Playwright。核心是一套**语义选择器库**,让用例引用
稳定的语义 key 而非猜 CSS。

## 组成

- `server.mjs` —— MCP server + 定位引擎(移植自 `nami-work-test/lib/dom.js`)。
- `selectors.json` —— **语义选择器注册表**(数据与代码分离,更新只改这个文件)。

## selectors.json 格式

```jsonc
{
  "vmIframe": "iframe[src*=\".work.n.cn\"]",   // 业务 iframe 定位(frameLocator 穿透用)
  "registry": {
    "navTasks": {
      "frame": "vm",                            // shell=顶层文档 | vm=业务 iframe | auto=先顶层后 iframe
      "desc": "主导航『任务』",                  // 给人/给 claude 看的说明
      "candidates": [                           // 按稳定性排序,引擎逐个试、命中即用
        { "by": "text", "value": "任务" }        // by: testid|role|label|text|placeholder|css
      ]
    }
  }
}
```

- **frame**:`vm` 的元素在业务 iframe(`<vm_id>.work.n.cn`)里,引擎自动 `frameLocator(vmIframe)` 穿透;
  `shell` 在顶层 `work.n.cn/claw` 文档;`auto` 两处都试(先顶层后 iframe)。
- **candidates**:稳定性 `testid > role > label > text > placeholder > css`。多候选=**失效自愈**:
  首选变了,引擎自动退到下一个候选;命中的候选会在工具返回的 `via` 里体现。

## 用例怎么用(Claude 侧)

元素类工具都接 `{key}`(优先)或 `{selector}`(原始 CSS 兜底):

```
gui_connect                                  # 第一步,连 CDP 并下钻业务 iframe
gui_list_keys                                # 列出所有语义 key(定位前先看有哪些)
gui_wait_for   {key:"navTasks"}              # 等可见
gui_assert_text{key:"navTasks", expected:"任务", contains:true}
gui_click      {key:"navExperts"}
gui_get_text   {key:"navItemActive"}
gui_fill       {key:"chatInput", text:"你好"}
gui_screenshot {path:"evidence/xxx.png"}     # 证据建议放 evidence/(已 gitignore)
# 注册表没覆盖的元素才用原始 selector:gui_click {selector:".xxx"}
```

## 怎么更新选择器(团队协作)

1. 连上 CDP(namiclaw 带 `--remote-debugging-port=9222` 起),在 DevTools 里 Pick 到真实元素;
2. 往 `selectors.json` 对应 key 的 `candidates` **头部**(更稳)或**尾部**(兜底)加一条,
   或新增一个 key;不要动用例。
3. git 提交/推送,各执行机 `git pull` 即拿到最新(v1 存储=随 runner 仓库走)。

> 引擎全不命中会抛**带诊断**的错(列出试过哪些候选 + 提示更新哪个 key),照提示补候选即可。

## 基座来源

注册表 v1 导出自 `nami-work-test/lib/selectors.js`(成熟语义注册表),并入了
`daily_test`(首页标题、专家任务「首响引导语」整套实测选择器)与实测的主导航 `nav*`。
覆盖:登录 / 侧栏导航 / 对话输入发送 / 消息气泡 / 上一条下一条 / 分享 / 专家反问 / 算力豆 / 任务引导 等。
