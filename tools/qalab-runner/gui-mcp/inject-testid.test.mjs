import { test } from "node:test";
import assert from "node:assert/strict";
import { injectTestIdMode } from "./gui-core.mjs";

// 造一个最小 mock ctx/page,记录 addInitScript / evaluate / reload 调用。
function makeMock({ alreadyEnabled = false, frames = 1 } = {}) {
  const calls = { addInit: 0, reload: 0, frameEvals: 0 };
  const frameArr = Array.from({ length: frames }, () => ({
    evaluate: async () => { calls.frameEvals += 1; },
  }));
  const page = {
    // page.evaluate 用于读 already 标志
    evaluate: async () => alreadyEnabled,
    frames: () => frameArr,
    reload: async () => { calls.reload += 1; },
  };
  const ctx = { addInitScript: async () => { calls.addInit += 1; } };
  return { ctx, page, calls };
}

test("首连未开过 → addInitScript + 各 frame 置位 + reload 一次", async () => {
  const { ctx, page, calls } = makeMock({ alreadyEnabled: false, frames: 2 });
  const r = await injectTestIdMode(ctx, page);
  assert.equal(calls.addInit, 1, "addInitScript 应调用一次");
  assert.equal(calls.frameEvals, 2, "每个 frame 都即时置位");
  assert.equal(calls.reload, 1, "未开过应 reload 一次让 main.ts 重跑");
  assert.deepEqual(r, { reloaded: true });
});

test("已开过(flag=1) → 不 reload,不折腾当前页", async () => {
  const { ctx, page, calls } = makeMock({ alreadyEnabled: true, frames: 1 });
  const r = await injectTestIdMode(ctx, page);
  assert.equal(calls.addInit, 1, "addInitScript 仍注入(保证后续加载带 flag)");
  assert.equal(calls.reload, 0, "已开过不应 reload");
  assert.deepEqual(r, { reloaded: false });
});

test("addInitScript 接口不可用不阻断:仍即时置位并 reload", async () => {
  const { page, calls } = makeMock({ alreadyEnabled: false, frames: 1 });
  const ctx = { addInitScript: async () => { throw new Error("unsupported"); } };
  const r = await injectTestIdMode(ctx, page);
  assert.equal(calls.frameEvals, 1, "addInitScript 抛错也要即时置位");
  assert.equal(calls.reload, 1);
  assert.equal(r.reloaded, true);
});

test("跨域 frame.evaluate 抛错被吞,不影响 reload", async () => {
  const calls = { reload: 0 };
  const page = {
    evaluate: async () => false,
    frames: () => [{ evaluate: async () => { throw new Error("cross-origin"); } }],
    reload: async () => { calls.reload += 1; },
  };
  const ctx = { addInitScript: async () => {} };
  const r = await injectTestIdMode(ctx, page);
  assert.equal(calls.reload, 1, "个别 frame 注入失败不应阻断 reload");
  assert.equal(r.reloaded, true);
});
