import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { osEscapeCommand, pressOsEscape, defaultRunner } from "./os-key.mjs";

// ---- osEscapeCommand:按平台生成「向前台窗口发 ESC」的命令(纯函数)----

test("osEscapeCommand:win32 → PowerShell SendKeys {ESC}", () => {
  const c = osEscapeCommand("win32");
  assert.equal(c.cmd, "powershell");
  const line = c.args.join(" ");
  assert.ok(line.includes("SendKeys"), "Windows 走 SendKeys");
  assert.ok(line.includes("{ESC}"), "应发送 {ESC}");
});

test("osEscapeCommand:darwin → osascript key code 53(mac 的 Escape 键码)", () => {
  const c = osEscapeCommand("darwin");
  assert.equal(c.cmd, "osascript");
  assert.ok(c.args.join(" ").includes("key code 53"), "mac 的 Escape 虚拟键码是 53");
});

test("osEscapeCommand:其它平台(linux)→ null(OS 级不支持,交由页面层兜底)", () => {
  assert.equal(osEscapeCommand("linux"), null);
});

// ---- pressOsEscape:执行 OS 命令(runner 可注入便于单测,尽力而为绝不抛)----

test("pressOsEscape:不支持的平台 → false 且不调执行器", async () => {
  let called = false;
  const runner = async () => { called = true; return true; };
  const ok = await pressOsEscape("linux", { runner });
  assert.equal(ok, false);
  assert.equal(called, false, "不支持的平台不应调执行器");
});

test("pressOsEscape:支持的平台 → 把命令交给执行器,成功返回 true", async () => {
  let got = null;
  const runner = async (command) => { got = command; return true; };
  const ok = await pressOsEscape("win32", { runner });
  assert.equal(ok, true);
  assert.equal(got.cmd, "powershell", "应把 osEscapeCommand 的命令交给执行器");
});

test("pressOsEscape:执行器抛错 → false(尽力而为,不向上抛)", async () => {
  const runner = async () => { throw new Error("spawn 失败"); };
  const ok = await pressOsEscape("darwin", { runner });
  assert.equal(ok, false);
});

// 真机教训:powershell 冷启动(叠加 Windows Defender 对 spawn 子进程的实时扫描)实测约 4.5s 才发出 ESC。
// 默认超时必须够大,否则 OS 级 ESC 每次都被判超时 kill → 恒返回 false(等于没这层自愈)。
test("pressOsEscape:默认超时足够大(≥8s,容纳 powershell 冷启动实测约 4.5s)", async () => {
  let gotTimeout = null;
  const runner = async (_cmd, timeoutMs) => { gotTimeout = timeoutMs; return true; };
  await pressOsEscape("win32", { runner });
  assert.ok(gotTimeout >= 8000, `默认超时应 ≥8s 以容纳 powershell 冷启动,实际 ${gotTimeout}`);
});

// ---- defaultRunner:真实 spawn 编排(注入 fake spawn 确定性驱动 exit/error/timeout/kill,不起真进程)----
// 假子进程:EventEmitter + 可观测的 kill 标志。测试在 spawnFn 里 queueMicrotask 触发事件,确保
// defaultRunner 已注册 on("exit")/on("error") 监听后再 emit。
function fakeChild() {
  const c = new EventEmitter();
  c.killed = false;
  c.kill = () => { c.killed = true; };
  return c;
}

test("defaultRunner:子进程退出码 0 → true", async () => {
  const child = fakeChild();
  const spawnFn = () => { queueMicrotask(() => child.emit("exit", 0)); return child; };
  assert.equal(await defaultRunner({ cmd: "x", args: [] }, 5000, spawnFn), true);
});

test("defaultRunner:退出码非 0 → false", async () => {
  const child = fakeChild();
  const spawnFn = () => { queueMicrotask(() => child.emit("exit", 1)); return child; };
  assert.equal(await defaultRunner({ cmd: "x", args: [] }, 5000, spawnFn), false);
});

test("defaultRunner:子进程 error 事件 → false", async () => {
  const child = fakeChild();
  const spawnFn = () => { queueMicrotask(() => child.emit("error", new Error("ENOENT"))); return child; };
  assert.equal(await defaultRunner({ cmd: "x", args: [] }, 5000, spawnFn), false);
});

test("defaultRunner:超时 → kill 子进程并 false", async () => {
  const child = fakeChild();
  const spawnFn = () => child;  // 从不 emit exit,逼超时路径
  const r = await defaultRunner({ cmd: "x", args: [] }, 30, spawnFn);
  assert.equal(r, false);
  assert.equal(child.killed, true, "超时应 kill 子进程");
});

test("defaultRunner:spawn 抛错 → false(不崩)", async () => {
  const spawnFn = () => { throw new Error("spawn ENOENT"); };
  assert.equal(await defaultRunner({ cmd: "x", args: [] }, 5000, spawnFn), false);
});
