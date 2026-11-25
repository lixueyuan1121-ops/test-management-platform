// 运行: node --test tools/signcheck-agent/agent.test.mjs
// 用一个假校验程序（输出 GBK 字节的 node 脚本）+ 假平台 HTTP 服务，验证 下载→校验→回传 的整条处理。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";

import { createClient, handleJob, missingVerifierFiles, pollOnce, readConfig, runVerifier, safeFileName } from "./agent.mjs";

const work = mkdtempSync(join(tmpdir(), "signcheck-agent-test-"));
// "签名状态：签名有效" 的 GBK 字节，模拟原程序按 ANSI 代码页输出
const GBK_VALID = "c7a9c3fbd7b4ccaca3bac7a9c3fbd3d0d0a7";

// 假校验程序 = 一段 node 脚本，由 node 本身当"exe"、脚本路径走 prefixArgs（Windows/Linux 通用）
function fakeVerifier(name, body) {
  const js = join(work, `${name}.cjs`);
  writeFileSync(js, body);
  return js;
}

const okJs = fakeVerifier("ok", `
const fs = require("fs");
const p = process.argv[2];
if (!fs.existsSync(p)) { process.stdout.write("missing"); process.exit(3); }
process.stdout.write(Buffer.concat([Buffer.from("EmbeddedSign(x)->\\t"), Buffer.from("${GBK_VALID}", "hex"), Buffer.from("\\r\\nsize=" + fs.statSync(p).size)]));
process.stderr.write("warn");
process.exit(7);
`);
const hangJs = fakeVerifier("hang", `setTimeout(() => {}, 60000);`);
const NODE = process.execPath;

const content = Buffer.from("MZ" + "x".repeat(70000));
const sha = createHash("sha256").update(content).digest("hex");
let server, base, calls;
const state = { job: null, serveContent: content, resultStatus: [] };

before(async () => {
  server = createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    calls.push(`${req.method} ${url.pathname}${url.search}`);
    const json = (status, body) => { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(body)); };
    if (req.headers.authorization !== "Bearer tok") return json(401, { code: 401, msg: "无效的签名校验机令牌", data: null });
    if (url.pathname === "/api/signcheck/agent/next") { const j = state.job; state.job = null; return json(200, { code: 0, data: j }); }
    if (url.pathname.endsWith("/file")) { res.writeHead(200, { "Content-Type": "application/octet-stream" }); return res.end(state.serveContent); }
    if (url.pathname.endsWith("/result")) {
      let raw = "";
      req.on("data", (c) => { raw += c; });
      req.on("end", () => {
        const status = state.resultStatus.shift() || 200;
        if (status !== 200) return json(status, { code: status, msg: "boom", data: null });
        state.posted = JSON.parse(raw);
        json(200, { code: 0, data: { id: 1, status: "completed", verdict: "passed" } });
      });
      return;
    }
    json(404, { code: 404, msg: "not found", data: null });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => { server.close(); rmSync(work, { recursive: true, force: true }); });

const cfgOf = (js, extra = {}) => ({ baseUrl: base, token: "tok", agentId: "win-test", pollMs: 10, timeoutMs: 20000, verifierExe: NODE, verifierArgs: [js], ...extra });
const leftovers = () => readdirSync(tmpdir()).filter((n) => n.startsWith("signcheck-") && !n.startsWith("signcheck-agent-test-"));

test("safeFileName 去掉 Windows 不接受的字符和保留名", () => {
  assert.equal(safeFileName("安装包 v1.2.exe"), "安装包 v1.2.exe");
  assert.equal(safeFileName('a<b>:"c|d?*.exe'), "a_b___c_d__.exe");
  assert.equal(safeFileName("..\\..\\x.exe"), ".._.._x.exe");
  assert.equal(safeFileName("name. . "), "name");
  assert.equal(safeFileName("CON"), "_CON");
  assert.equal(safeFileName("com1.txt"), "_com1.txt");
  assert.equal(safeFileName(""), "_file");
  const long = safeFileName("a".repeat(300) + ".exe");
  assert.equal(long.length, 120);
  assert.ok(long.endsWith(".exe"));
});

test("readConfig 默认值与路径解析", () => {
  const c = readConfig({ BASE_URL: "http://p:4173///", SIGNCHECK_AGENT_TOKEN: "t" });
  assert.equal(c.baseUrl, "http://p:4173");
  assert.equal(c.pollMs, 3000);
  assert.equal(c.timeoutMs, 120000);
  assert.ok(c.verifierExe.endsWith(join("verifier", "360Base_test.exe")));
  assert.ok(c.agentId.length > 0);
});

test("missingVerifierFiles 列出缺的 DLL", () => {
  const miss = missingVerifierFiles(join(work, "nope", "360Base_test.exe"));
  assert.equal(miss.length, 5);
});

test("runVerifier 原样拿回字节、退出码；超时与启动失败给出原因", async () => {
  const f = join(work, "in.bin");
  writeFileSync(f, content);
  const r = await runVerifier(NODE, f, 20000, [okJs]);
  assert.equal(r.error, null);
  assert.equal(r.exitCode, 7);
  assert.ok(r.stdout.includes(Buffer.from(GBK_VALID, "hex")), "GBK 字节不能被转码");
  assert.ok(r.stdout.toString("latin1").endsWith(`size=${content.length}`));
  assert.equal(r.stderr.toString(), "warn");

  const t = await runVerifier(NODE, f, 300, [hangJs]);
  assert.match(t.error, /未完成/);
  const n = await runVerifier(join(work, "does-not-exist"), f, 2000);
  assert.match(n.error, /无法启动原校验工具/);
});

test("pollOnce：认领→下载→校验→回传，临时目录清理", async () => {
  calls = [];
  const beforeDirs = leftovers().length;
  state.job = { id: 1, filename: "setup 安装包.exe", file_size: content.length, sha256: sha };
  state.serveContent = content;
  assert.equal(await pollOnce(cfgOf(okJs), createClient(cfgOf(okJs))), true);
  assert.deepEqual(calls, ["GET /api/signcheck/agent/next?agent=win-test", "GET /api/signcheck/agent/1/file", "POST /api/signcheck/agent/1/result"]);
  const p = state.posted;
  assert.equal(p.agent, "win-test");
  assert.equal(p.exit_code, 7);
  assert.equal(p.error, null);
  const out = Buffer.from(p.stdout_b64, "base64");
  assert.ok(out.includes(Buffer.from(GBK_VALID, "hex")));
  assert.ok(out.toString("latin1").endsWith(`size=${content.length}`), "校验程序应拿到完整文件");
  assert.equal(Buffer.from(p.stderr_b64, "base64").toString(), "warn");
  assert.ok(p.duration_ms >= 0);
  assert.equal(leftovers().length, beforeDirs, "signcheck-* 临时目录应已删除");

  assert.equal(await pollOnce(cfgOf(okJs), createClient(cfgOf(okJs))), false, "无待办返回 false");
});

test("下载内容与 sha256 不符：不跑校验程序，回传错误", async () => {
  state.serveContent = Buffer.from("tampered");
  await handleJob({ id: 1, filename: "a.exe", sha256: sha }, cfgOf(okJs), createClient(cfgOf(okJs)));
  assert.match(state.posted.error, /sha256 不匹配/);
  assert.equal(state.posted.stdout_b64, undefined);
  state.serveContent = content;
});

test("回传失败会重试；409 不重试", async () => {
  state.resultStatus = [500];
  calls = [];
  const r = await handleJob({ id: 1, filename: "a.exe", sha256: sha }, cfgOf(okJs), createClient(cfgOf(okJs)));
  assert.equal(r.verdict, "passed");
  assert.equal(calls.filter((c) => c.startsWith("POST")).length, 2);

  state.resultStatus = [409];
  calls = [];
  await assert.rejects(handleJob({ id: 1, filename: "a.exe", sha256: sha }, cfgOf(okJs), createClient(cfgOf(okJs))), (e) => e.status === 409);
  assert.equal(calls.filter((c) => c.startsWith("POST")).length, 1);
});

test("令牌错误：抛出带 401 的错误", async () => {
  const bad = cfgOf(okJs, { token: "wrong" });
  await assert.rejects(pollOnce(bad, createClient(bad)), (e) => e.status === 401);
});

test("真实 verifier 目录（如已放置）文件齐全", { skip: !existsSync(readConfig({}).verifierExe) }, () => {
  assert.deepEqual(missingVerifierFiles(readConfig({}).verifierExe), []);
});
