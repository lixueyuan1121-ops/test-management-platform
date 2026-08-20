import { test } from "node:test";
import assert from "node:assert/strict";
import { resetHomeWithRetry, resetOrBlock } from "./reset-home.mjs";

test("首次成功 → true,只调 1 次", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), true);
  assert.equal(n, 1);
});

test("首次失败、二次成功 → true", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; if (n === 1) throw new Error("reload 超时"); } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), true);
  assert.equal(n, 2);
});

test("两次都失败 → false", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; throw new Error("客户端未响应"); } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), false);
  assert.equal(n, 2);
});

// ---- resetOrBlock:复位成功放行;复位失败或掉登录 → 阻塞结果(fail_kind=selector,接后端 blocked) ----

test("resetOrBlock:复位成功且未掉登录 → ok:true", async () => {
  const gui = {
    async resetHome() {},
    async verifyKeys() { return { verify: { loginModal: false } }; },  // 未掉登录
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, true);
});

test("resetOrBlock:复位失败(重试仍败) → ok:false + fail_kind=selector", async () => {
  const gui = {
    async resetHome() { throw new Error("reload 超时"); },
    async verifyKeys() { return { verify: {} }; },
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, false);
  assert.equal(r.result.verdict, "fail");
  assert.equal(r.result.fail_kind, "selector", "复位失败属环境阻塞");
  assert.ok(/复位/.test(r.result.reason), r.result.reason);
});

test("resetOrBlock:复位成功但掉登录(loginModal 可见) → ok:false + 提示重新登录", async () => {
  const gui = {
    async resetHome() {},
    async verifyKeys(keys) { return { verify: { loginModal: keys.includes("loginModal") } }; },  // 掉登录
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, false);
  assert.equal(r.result.fail_kind, "selector");
  assert.ok(/登录/.test(r.result.reason), r.result.reason);
});

test("resetOrBlock:verifyKeys 抛错不影响放行(掉登录检测尽力而为)", async () => {
  const gui = {
    async resetHome() {},
    async verifyKeys() { throw new Error("verify 不可用"); },
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, true, "掉登录检测失败不应阻断已成功的复位");
});

// ---- 首页就绪门禁:reload 复位后必须确认首页锚点(homepageTitle)可见才放行 ----
// 根因:connect()/resetHome 只等到了某个 iframe,首页(改版 home-revamp)还没渲染出问候标题就跑
// wait_for,产生「运行时首页没停稳」的瞬态 fail。resetHome 的锚点等待是 catch 吞掉的尽力而为,
// 兜不住。这里在 gate 层硬门禁:探到 homepageTitle 不可见 → 记 blocked,不空跑未停稳的用例。

test("resetOrBlock:复位成功、未掉登录、但首页锚点未就绪 → ok:false + fail_kind=selector", async () => {
  const gui = {
    async resetHome() {},
    // 探测可用,但 homepageTitle 未可见(首页没停稳);loginModal 也不可见(没掉登录)
    async verifyKeys(keys) {
      const verify = {};
      for (const k of keys) verify[k] = false;
      return { verify };
    },
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, false, "首页锚点未就绪应记 blocked,不放行");
  assert.equal(r.result.verdict, "fail");
  assert.equal(r.result.fail_kind, "selector", "首页没停稳属环境阻塞,不计功能失败率");
  assert.ok(/首页/.test(r.result.reason), r.result.reason);
});

test("resetOrBlock:复位成功且首页锚点已就绪 → ok:true", async () => {
  const gui = {
    async resetHome() {},
    // homepageTitle 可见=首页停稳,loginModal 不可见=没掉登录 → 放行
    async verifyKeys(keys) {
      const verify = {};
      for (const k of keys) verify[k] = (k === "homepageTitle");
      return { verify };
    },
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, true);
});

test("resetOrBlock:掉登录优先于首页锚点报错(loginModal 可见时提示重新登录,而非首页未就绪)", async () => {
  const gui = {
    async resetHome() {},
    // 掉登录场景:loginModal 可见,homepageTitle 自然也不可见 → 应报「重新登录」而非「首页未就绪」
    async verifyKeys(keys) {
      const verify = {};
      for (const k of keys) verify[k] = (k === "loginModal");
      return { verify };
    },
  };
  const r = await resetOrBlock(gui, () => {});
  assert.equal(r.ok, false);
  assert.ok(/登录/.test(r.result.reason), `应优先提示重新登录,实际:${r.result.reason}`);
});
