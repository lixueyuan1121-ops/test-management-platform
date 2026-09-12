"""Export GUI scripts with the exact runtime distributed to execution devices.

Unsupported steps fail export explicitly; no executable test silently drops a check.
The shared JS asset is packaged with backend/, so backend-only deployments work too.
"""
from __future__ import annotations

import json
from pathlib import Path

_RUNTIME = Path(__file__).with_name("playwright_runtime.mjs")
_ACTIONS = {"connect", "goto", "click", "hover", "fill", "type", "set_checked", "select_option", "press", "wait_for",
            "wait_response", "get_text", "screenshot", "assert_visible", "assert_absent",
            "assert_text", "mock_route", "unmock_route"}
_TARGET_ACTIONS = {"click", "hover", "fill", "type", "set_checked", "select_option", "wait_for", "get_text",
                   "assert_visible", "assert_absent", "assert_text"}


def _validate_target(target, registry, depth=0):
    if not isinstance(target, dict) or not (target.get("key") or target.get("selector")):
        raise ValueError("定位步骤缺少 target.key/selector")
    if depth > 3:
        raise ValueError("within 最多嵌套三层")
    if target.get("key") and target["key"] not in registry:
        raise ValueError(f"未登记语义 key：{target['key']}，请先补齐选择器")
    if "nth" in target and (type(target["nth"]) is not int or target["nth"] < 0):
        raise ValueError("nth 必须是非负整数")
    if "within" in target:
        _validate_target(target["within"], registry, depth + 1)


def export_case_to_playwright(case: dict, registry: dict, vm_iframe: str) -> str:
    if (case.get("exec_kind") or "gui") not in ("gui", "e2e"):
        raise ValueError("仅 gui/e2e 用例支持导出 Playwright 脚本")
    script = case.get("script")
    if not isinstance(script, list) or not script:
        raise ValueError("用例没有结构化 script")
    budget = 30000
    for i, step in enumerate(script):
        if not isinstance(step, dict) or step.get("action") not in _ACTIONS:
            raise ValueError(f"第 {i + 1} 步无法等价导出（动作未支持），请先补齐执行实现")
        action = step["action"]
        args, target = step.get("args") or {}, step.get("target") or {}
        if not isinstance(args, dict) or not isinstance(target, dict):
            raise ValueError(f"第 {i + 1} 步 args/target 必须是对象")
        if action in _TARGET_ACTIONS or (action == "press" and target):
            _validate_target(target, registry)
        if action == "assert_text" and not isinstance(args.get("expected"), str):
            raise ValueError("assert_text 缺少字符串 expected")
        if action == "press" and not args.get("key_name"):
            raise ValueError("press 缺少 key_name")
        if action in ("mock_route", "unmock_route") and not args.get("url"):
            raise ValueError(f"{action} 缺少 url")
        step_timeout = args.get("timeout_ms", 90000 if action == "wait_response" else 10000)
        if type(step_timeout) not in (int, float) or not 0 <= step_timeout < float("inf"):
            raise ValueError("timeout_ms 必须是有限非负数")
        budget += max(1000, step_timeout) * 2
    if not any(s["action"].startswith("assert_") for s in script):
        raise ValueError("用例缺少断言，不能导出为可通过的测试")

    # JSON encoding preserves multiline inputs and treats all user data as data.
    config = json.dumps({"title": case.get("title") or "未命名用例", "registry": registry,
                         "vmIframe": vm_iframe, "script": script, "budget": budget}, ensure_ascii=True, allow_nan=False)
    runtime = _RUNTIME.read_text(encoding="utf-8").replace("export function ", "function ")
    return '''// 由测试管理平台导出。与平台共用定位、断言、回复等待及 Mock 实现。
// npm i -D @playwright/test
// 启动被测客户端并启用 --remote-debugging-port=9222，然后运行 npx playwright test。
// CDP_URL 可覆盖调试地址；同一客户端上的测试必须串行运行（--workers=1）。
// 执行前需恢复与平台一致的登录态、首页和业务测试数据；此文件导出用例步骤。
import { test, chromium } from '@playwright/test';

''' + runtime + "\nconst config = " + config + ";\n" + r'''
test(config.title, async ({}, testInfo) => {
  test.setTimeout(config.budget);
  const browser = await chromium.connectOverCDP(process.env.CDP_URL || 'http://127.0.0.1:9222');
  let runtime, context, tracing = false, passed = false;
  const mocksSeen = new Map();
  try {
    context = browser.contexts()[0];
    if (!context) throw new Error('CDP 未提供可用 context');
    const deadline = Date.now() + 15000;
    let page;
    while (!page && Date.now() < deadline) {
      const pages = context.pages().filter(p => !p.isClosed());
      page = pages.find(p => p.url().includes('work.n.cn')) || pages[0];
      if (!page) await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!page) throw new Error('CDP 没有就绪的业务页面');
    runtime = createAutomationRuntime({ page, registry: config.registry, vmIframe: config.vmIframe });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
    tracing = true;
    for (let i = 0; i < config.script.length; i++) {
      const { action, target = {}, args = {}, desc = '' } = config.script[i];
      await test.step(desc || `${i + 1}. ${action}`, async () => {
        const responseArgs = responseArgsBeforeAction(config.script, i);
        if (responseArgs !== null) await runtime.captureResponse(responseArgs);
        const a = { ...target, ...args };
        let result;
        switch (action) {
          case 'connect': break;
          case 'goto': await page.goto(args.url || target.url, { waitUntil: 'domcontentloaded', timeout: args.timeout_ms ?? 10000 }); break;
          case 'click': await runtime.click(a); break;
          case 'hover': await runtime.hover(a); break;
          case 'fill': await runtime.fill(a); break;
          case 'set_checked': await runtime.setChecked(a); break;
          case 'select_option': await runtime.selectOption(a); break;
          case 'type': await runtime.type(a); break;
          case 'press': await runtime.pressKey(a); break;
          case 'wait_for': await runtime.waitFor(a); break;
          case 'get_text': await runtime.getText(a); break;
          case 'wait_response':
            result = await runtime.waitResponse(args);
            if (!result.done) throw new Error(result.reason);
            break;
          case 'assert_text': result = await runtime.assertText(a); break;
          case 'assert_visible': result = await runtime.assertVisible(a); break;
          case 'assert_absent': result = await runtime.assertAbsent(a); break;
          case 'screenshot': await page.screenshot({ path: args.path || testInfo.outputPath(`step-${i + 1}.png`) }); break;
          case 'mock_route':
            await runtime.mockRoute(args);
            mocksSeen.set(args.url, { pattern: args.url, hits: 0 });
            break;
          case 'unmock_route': {
            const r = await runtime.unmockRoute(args);
            if (mocksSeen.has(args.url)) mocksSeen.set(args.url, { pattern: args.url, hits: r.hits });
            break;
          }
          default: throw new Error(`未支持动作：${action}`);
        }
        if (result && result.pass === false) throw new Error(`断言失败：${JSON.stringify(result)}`);
      });
    }
    for (const stat of runtime.mockStats()) mocksSeen.set(stat.pattern, stat);
    const missed = [...mocksSeen.values()].filter(s => !s.hits);
    if (missed.length) throw new Error(`Mock 未命中：${missed.map(s => s.pattern).join(', ')}`);
    passed = true;
  } finally {
    try {
      try { await runtime?.unmockAll(); }
      catch (e) { passed = false; throw e; }
      finally {
        if (tracing) {
          const path = passed ? undefined : testInfo.outputPath('trace.zip');
          await context.tracing.stop(path ? { path } : {});
          if (path) await testInfo.attach('Playwright Trace', { path, contentType: 'application/zip' });
        }
      }
    } finally { await browser.close(); }
  }
});
'''
