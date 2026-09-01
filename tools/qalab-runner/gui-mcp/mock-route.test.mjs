import { test } from "node:test";
import assert from "node:assert/strict";
import { toUrlMatcher, buildMockResponse } from "./mock-route.mjs";

const hit = (pattern, url) => toUrlMatcher(pattern).test(url);

// ---- toUrlMatcher:根因① —— Playwright 的 glob「必须匹配整个 URL」,带 query 的真实请求匹配不上 ----

test("glob:**/api/tasks 命中无参 URL", () => {
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/tasks"), true);
  assert.equal(hit("**/api/tasks", "http://127.0.0.1:8000/api/tasks"), true);
});

test("glob:**/api/tasks 命中**带 query**的 URL(根因①:真实请求几乎都带参)", () => {
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/tasks?project_id=1"), true);
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/tasks?project_id=1&date=2026-09-01"), true);
});

test("glob:**/api/tasks 命中带 hash 的 URL", () => {
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/tasks#top"), true);
});

test("glob:容忍 query 不等于放宽路径 —— 子路径/别的接口仍不命中", () => {
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/tasks/12"), false);
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/taskstat"), false);
  assert.equal(hit("**/api/tasks", "https://h.example.com/api/issues"), false);
});

test("glob:单 * 不跨 /,也不吃进 query", () => {
  assert.equal(hit("**/api/*", "https://h.example.com/api/tasks"), true);
  assert.equal(hit("**/api/*", "https://h.example.com/api/tasks?x=1"), true);
  assert.equal(hit("**/api/*", "https://h.example.com/api/tasks/12"), false);
});

test("glob:** 跨 / 匹配任意深度", () => {
  assert.equal(hit("**/api/**", "https://h.example.com/api/tasks/12?x=1"), true);
  assert.equal(hit("**/api/**", "https://h.example.com/other/x"), false);
});

test("glob:裸路径 /api/tasks 当作 **/api/tasks(模型常这么写,不该静默失配)", () => {
  assert.equal(hit("/api/tasks", "https://h.example.com/api/tasks?x=1"), true);
  assert.equal(hit("/api/tasks", "https://h.example.com/api/issues"), false);
});

test("glob:模式里显式写了 query → 严格匹配,不再追加容忍后缀", () => {
  assert.equal(hit("**/api/tasks?page=1", "https://h.example.com/api/tasks?page=1"), true);
  assert.equal(hit("**/api/tasks?page=1", "https://h.example.com/api/tasks"), false);
});

test("glob:{a,b} 择一 + 正则元字符按字面量转义", () => {
  assert.equal(hit("**/api/{tasks,issues}", "https://h.example.com/api/issues?x=1"), true);
  assert.equal(hit("**/api/{tasks,issues}", "https://h.example.com/api/tools"), false);
  assert.equal(hit("**/a.b", "https://h.example.com/a.b"), true);
  assert.equal(hit("**/a.b", "https://h.example.com/axb"), false);
});

test("glob:完整 URL 模式照常可用", () => {
  assert.equal(hit("http://127.0.0.1:8000/api/tasks", "http://127.0.0.1:8000/api/tasks?x=1"), true);
  assert.equal(hit("http://127.0.0.1:8000/api/tasks", "http://other:8000/api/tasks"), false);
});

// ---- buildMockResponse:根因② —— fulfill 的响应缺 CORS 头,跨域请求被浏览器拦掉 ----

test("响应:跨域请求回显 Origin + 允许携带凭证(根因②)", () => {
  const r = buildMockResponse({ body: { code: 0, data: [] } }, { origin: "http://10.0.0.5" });
  assert.equal(r.headers["access-control-allow-origin"], "http://10.0.0.5");
  assert.equal(r.headers["access-control-allow-credentials"], "true");
});

test("响应:无 Origin(同源/非 CORS)→ ACAO 用 *,且不发 credentials(* 与凭证互斥)", () => {
  const r = buildMockResponse({ body: {} }, {});
  assert.equal(r.headers["access-control-allow-origin"], "*");
  assert.equal(r.headers["access-control-allow-credentials"], undefined);
});

test("响应:对象 body 序列化为 JSON,状态码默认 200", () => {
  const r = buildMockResponse({ body: { code: 0, data: [] } }, {});
  assert.equal(r.status, 200);
  assert.equal(r.body, '{"code":0,"data":[]}');
  assert.equal(r.headers["content-type"], "application/json");
});

test("响应:字符串 body 原样透传,不二次 stringify(否则前端拿到的是 JSON 字符串而非对象)", () => {
  const r = buildMockResponse({ body: '{"code":0,"data":[]}' }, {});
  assert.equal(r.body, '{"code":0,"data":[]}');
});

test("响应:缺 body → 空对象;显式 status 生效", () => {
  const r = buildMockResponse({ status: 500 }, {});
  assert.equal(r.status, 500);
  assert.equal(r.body, "{}");
});

test("响应:调用方可覆盖 content-type 与自定义头(大小写归一)", () => {
  const r = buildMockResponse({ body: "plain", content_type: "text/plain", headers: { "X-Trace": "abc" } }, {});
  assert.equal(r.headers["content-type"], "text/plain");
  assert.equal(r.headers["x-trace"], "abc");
});

test("响应:Origin 头大小写不敏感(Playwright 给小写,防御性兼容 Origin)", () => {
  const r = buildMockResponse({ body: {} }, { Origin: "https://app.example.com" });
  assert.equal(r.headers["access-control-allow-origin"], "https://app.example.com");
});
