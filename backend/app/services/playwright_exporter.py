"""把一条结构化用例 script 翻译成自包含的 Playwright .spec.mjs 文本（供开发本地自测）。

纯函数、无 DB、无网络：registry / vmIframe 由调用方（api 层）从 selectors 服务取好后传入，
便于单测（见 scripts/test_playwright_export.py）。

翻译契约与执行侧保持一致：
- key→locator 映射镜像 tools/qalab-runner/gui-mcp/gui-core.mjs::byToLocator
  （testid/role/label/text/placeholder/css）；一个 key 的多个 candidates 用 .or() 串成自愈链。
- frame 作用域：'shell'→顶层 page；其余（'vm'/'auto'/'url:...'）→ 业务 iframe（frameLocator(vmIframe)），
  执行侧 vm 会回退 shell，导出脚本从简只落 iframe 作用域（业务页现状在 iframe 内）。
- step→语句：connect（连接头已在模板，转注释）/goto/click/fill/wait_for/get_text/
  assert_visible/assert_text；wait_response/judge 无通用 Playwright 对应 → 生成 TODO 占位注释。
- 未登记 key（选择器待补）→ 生成抛错占位 + TODO 注释（点名缺失 key），其余步照常翻译。

只支持 gui/e2e（其余 kind / 空 script 抛 ValueError，由 api 层转 400 或在 zip 中跳过）。
"""
from __future__ import annotations


def _js_str(s: str) -> str:
    """把 Python 字符串安全嵌进 JS 单引号字符串（转义 \\ 和 '，去掉换行）。"""
    s = str(s or "")
    s = s.replace("\\", "\\\\").replace("'", "\\'")
    s = s.replace("\r", " ").replace("\n", " ")
    return s


def _js_comment(s) -> str:
    """把值安全嵌入 // 单行注释：消除换行（\\r/\\n → 空格）。

    注释里的值（desc/title/action/key 等来自用例文本）若含换行，会逃出 // 行注释，
    使后续内容变成 live JS——导出脚本在开发本机 `npx playwright test` 运行时即被执行
    （存储型注入 → 开发机代码执行）。字符串字面量走 _js_str 已消除换行，注释侧须对称处理。
    """
    return str(s or "").replace("\r", " ").replace("\n", " ")


def _cand_expr(scope: str, cand: dict) -> str:
    """单个 candidate → Playwright locator 表达式（不含 scope 前缀的调用），镜像 byToLocator。"""
    by = cand.get("by", "css")
    val = _js_str(cand.get("value", ""))
    if by == "testid":
        return f"{scope}.getByTestId('{val}')"
    if by == "role":
        name = cand.get("name")
        if name:
            return f"{scope}.getByRole('{val}', {{ name: '{_js_str(name)}' }})"
        return f"{scope}.getByRole('{val}')"
    if by == "label":
        return f"{scope}.getByLabel('{val}')"
    if by == "text":
        return f"{scope}.getByText('{val}')"
    if by == "placeholder":
        return f"{scope}.getByPlaceholder('{val}')"
    # css 及未知 by 一律 locator（与 byToLocator 的 default 一致）
    return f"{scope}.locator('{val}')"


def _scope_var(frame) -> str:
    """frame 归属 → 作用域变量名。shell=顶层 page；其余走业务 iframe 变量 vm。"""
    return "page" if frame == "shell" else "vm"


def _locator_expr(entry: dict, key: str, registry: dict, vm_iframe: str) -> str:
    """一个已登记 key 的 entry → 多候选 .or() 链表达式。"""
    scope = _scope_var(entry.get("frame"))
    cands = entry.get("candidates") or []
    if not cands:
        # 登记了 key 但无候选：退化成一个显然定位不到的占位，交给下方 TODO 逻辑处理更合适，
        # 但此函数只管表达式；给个 css 空串（调用侧不会走到这，candidates 一般非空）。
        return f"{scope}.locator('')"
    exprs = [_cand_expr(scope, c) for c in cands]
    head = exprs[0]
    for e in exprs[1:]:
        head = f"{head}.or({e})"
    return head


def _resolve_target(target: dict, registry: dict, vm_iframe: str):
    """把 step 的 target 解析成 (locator_expr, todo)。

    - target.selector（原始 CSS）→ 顶层 page.locator，无 todo（执行侧 selector 走 contentFrame，
      导出从简用 page 作用域；开发可按需改 vm）。
    - target.key 已登记 → (.or() 链, None)。
    - target.key 未登记 → (None, 缺失说明)：调用侧生成抛错占位。
    - 无 key 也无 selector → (None, 说明)。
    """
    if not isinstance(target, dict):
        target = {}
    if target.get("selector"):
        return f"page.locator('{_js_str(target['selector'])}')", None
    key = target.get("key")
    if not key:
        return None, "该步无 key/selector，无法定位"
    entry = registry.get(key)
    if not entry:
        return None, f'未登记语义 key "{key}"（选择器待补）'
    return _locator_expr(entry, key, registry, vm_iframe), None


# 需要定位目标的动作（其余动作如 connect/goto/wait_response/judge 不走 _resolve_target）
def _step_lines(idx: int, step: dict, registry: dict, vm_iframe: str) -> list[str]:
    """单个 step → 若干行 JS（含前置 desc 注释）。返回的行不含缩进（调用方统一缩进）。"""
    action = str(step.get("action") or "")
    desc = step.get("desc") or action
    args = step.get("args") or {}
    target = step.get("target") or {}
    # 两行注释：序号定位（step N/动作）+ 用例原始 desc 独立成行（便于搜索/对照）
    lines = [f"// step{idx + 1} [{action}]", f"// {desc}"]

    if action == "connect":
        lines.append("// 连接已在文件头完成（connectOverCDP），此步无需额外操作")
        return lines

    if action == "goto":
        url = args.get("url") or target.get("url") or ""
        lines.append(f"await page.goto('{_js_str(url)}');")
        return lines

    if action in ("wait_response", "judge"):
        q = args.get("question") or desc
        lines.append(f"// TODO: 「{action}」无通用 Playwright 对应，请手写。判定点：{_js_str(q)}")
        return lines

    # 以下动作都需要定位
    loc, todo = _resolve_target(target, registry, vm_iframe)
    if todo:
        key = target.get("key") or ""
        lines.append(f"// TODO: {todo}；请在平台补 selector 后重新导出，或手写下面这步的定位")
        lines.append(f"throw new Error('用例导出：{_js_str(todo)}（key={_js_str(key)}）');")
        return lines

    if action == "click":
        lines.append(f"await {loc}.click();")
    elif action == "fill":
        text = _js_str(args.get("text", ""))
        lines.append(f"await {loc}.fill('{text}');")
    elif action == "wait_for":
        timeout = args.get("timeout_ms")
        if timeout:
            lines.append(f"await {loc}.waitFor({{ state: 'visible', timeout: {int(timeout)} }});")
        else:
            lines.append(f"await {loc}.waitFor({{ state: 'visible' }});")
    elif action == "get_text":
        lines.append(f"const _t{idx + 1} = await {loc}.textContent();")
    elif action == "screenshot":
        path = args.get("path") or f"evidence/step{idx + 1}.png"
        lines.append(f"await page.screenshot({{ path: '{_js_str(path)}' }});")
    elif action == "assert_visible":
        lines.append(f"await expect({loc}).toBeVisible();")
    elif action == "assert_text":
        expected = _js_str(args.get("expected", ""))
        if args.get("contains"):
            lines.append(f"await expect({loc}).toContainText('{expected}');")
        else:
            lines.append(f"await expect({loc}).toHaveText('{expected}');")
    else:
        lines.append(f"// TODO: 未支持的动作「{action}」，请手写")
    return lines


def _safe_test_name(title: str) -> str:
    """用例标题 → 可放进 test('...') 的字符串。"""
    return _js_str(title or "未命名用例")


def export_case_to_playwright(case: dict, registry: dict, vm_iframe: str) -> str:
    """一条 gui/e2e 用例 → 自包含 Playwright .spec.mjs 文本。

    case 需含：exec_kind、script（已解析为 list）、title/steps/expected（可选，写进注释）。
    非 gui/e2e 或空 script → ValueError。
    """
    kind = (case.get("exec_kind") or "gui")
    if kind not in ("gui", "e2e"):
        raise ValueError(f"仅 gui/e2e 用例支持导出 Playwright 脚本（当前 {kind}）")
    script = case.get("script")
    if not isinstance(script, list) or not script:
        raise ValueError("该用例无结构化 script，无法导出（可在平台先「生成 script」）")

    title = case.get("title") or "未命名用例"
    steps_txt = _js_str(case.get("steps") or "")
    expected_txt = _js_str(case.get("expected") or "")
    vm = _js_str(vm_iframe)

    body_lines: list[str] = []
    for i, st in enumerate(script):
        body_lines.extend(_step_lines(i, st or {}, registry, vm_iframe))
        body_lines.append("")  # 步与步之间空行

    indented = "\n".join(("  " + ln if ln else "") for ln in body_lines).rstrip()

    header = f"""// 由测试管理平台导出：{title}
// 用例意图（steps）：{steps_txt}
// 预期（expected）：{expected_txt}
//
// 【本地运行前置】
// 1) 安装依赖：  npm i -D @playwright/test
// 2) 带调试端口启动被测客户端（Electron）：  <客户端可执行文件> --remote-debugging-port=9222
//    （客户端须先跑起来并开着调试端口，脚本通过 CDP 连它、驱动其内嵌业务页）
// 3) 运行：  npx playwright test 本文件
//
// 说明：业务页在客户端内嵌 iframe（{vm}）里，下方用 vm 作为其作用域。
// 一个语义定位用 .or() 串了多个候选（自愈）；标了 TODO 的步骤需你手动补全。
import {{ test, expect, chromium }} from '@playwright/test';

test('{_safe_test_name(title)}', async () => {{
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = context.pages()[0] || (await context.newPage());
  // 业务页所在 iframe 的作用域（frame:shell 的定位用顶层 page，其余用 vm）
  const vm = page.frameLocator('{vm}');

{indented}
}});
"""
    return header
