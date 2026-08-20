import { test } from "node:test";
import assert from "node:assert/strict";
import { decimate, pollPerfOnce } from "./perf-collect.mjs";

test("decimate 不足 keep 全保留、按 t 升序", () => {
  const s = [
    { metric: "cpu", t: 3, v: 1 },
    { metric: "cpu", t: 1, v: 2 },
    { metric: "mem", t: 2, v: 3 },
  ];
  const out = decimate(s, 2000);
  assert.equal(out.length, 3);
  assert.deepEqual(out.map((x) => x.t), [1, 2, 3]);
});

test("decimate 超过 keep 每组抽到 keep 点", () => {
  const s = [];
  for (let i = 0; i < 5000; i++) s.push({ metric: "cpu", t: i, v: i });
  for (let i = 0; i < 100; i++) s.push({ metric: "fps", t: i, v: i });
  const out = decimate(s, 2000);
  assert.equal(out.filter((x) => x.metric === "cpu").length, 2000);
  assert.equal(out.filter((x) => x.metric === "fps").length, 100);
});

// ---- pollPerfOnce 编排(桩 api + 桩 runPerfdog,不真跑 perfdog) ----
function makeCtx(routes, calls) {
  const api = async (method, path, body) => {
    calls.push(`${method} ${path}`);
    const key = `${method} ${path.split("?")[0]}`;
    const r = routes[key];
    return typeof r === "function" ? r(body) : r;
  };
  return { api, log: () => {}, RUNNER_ID: "win-01", PERFDOG_DIR: ".", SESSIONS_DIR: "./sessions", REPORT_SET_ID: null };
}

test("pollPerfOnce 空队列直接返回、不认领", async () => {
  const calls = [];
  const ctx = makeCtx({ "GET /api/perf/queue": [] }, calls);
  await pollPerfOnce(ctx, { runPerfdog: async () => ({ code: 0, dir: null }) });
  assert.deepEqual(calls, ["GET /api/perf/queue?runner=win-01&limit=5"]);
});

test("pollPerfOnce 认领长监控→采集→成功回传", async () => {
  const calls = [];
  let patchBody = null;
  const routes = {
    "GET /api/perf/queue": [{ run_id: 7, scenario: "长监控", variant: "v1", duration: "40s" }],
    "POST /api/perf/queue/7/claim": {},
    "PATCH /api/perf/queue/7/prompt": { status: "running", signal_seq: 0 },
    "PATCH /api/perf/queue/7": (body) => { patchBody = body; return {}; },
  };
  const ctx = makeCtx(routes, calls);
  // dir 非 null → _readSession 桩生效,走成功回传路径(而非 dir=null 的 failed 兜底)
  const stubRun = async () => ({ code: 0, dir: "./sessions/s7" });
  await pollPerfOnce(
    { ...ctx, _readSession: () => ({ meta: { outcome: "ok" }, samples: [], events: [] }) },
    { runPerfdog: stubRun },
  );
  assert.ok(calls.includes("POST /api/perf/queue/7/claim?runner=win-01"));
  assert.ok(calls.some((c) => c.startsWith("PATCH /api/perf/queue/7?runner=win-01")));
  assert.equal(patchBody.outcome, "ok");
});
