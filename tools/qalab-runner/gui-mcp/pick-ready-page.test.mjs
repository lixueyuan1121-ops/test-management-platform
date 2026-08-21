import { test } from "node:test";
import assert from "node:assert/strict";
import { pickReadyPage } from "./gui-core.mjs";

// pickReadyPage:从 CDP context 的 pages() 结果里挑"就绪可用"的页面。
// 冷启动竞态根因:CDP 端口先活、页面 target 后注册,ensureConnected 若在 pages() 仍空时
// 立刻 ctx.newPage() 会撞 Electron 的 Target.createTarget: Not supported。故先靠本函数判断
// 有没有可用页面,没有则由调用方轮询等待,newPage 只作最后兜底。

function fakePage(url, closed = false) {
  return { url: () => url, isClosed: () => closed };
}

test("优先返回 url 含 work.n.cn 的页面", () => {
  const pages = [fakePage("about:blank"), fakePage("https://x.work.n.cn/claw")];
  const p = pickReadyPage(pages);
  assert.equal(p.url(), "https://x.work.n.cn/claw");
});

test("无 work.n.cn 时返回首个未关闭页面", () => {
  const pages = [fakePage("about:blank")];
  assert.equal(pickReadyPage(pages).url(), "about:blank");
});

test("跳过已关闭的页面", () => {
  const closed = fakePage("https://x.work.n.cn/claw", true);
  const open = fakePage("about:blank");
  assert.equal(pickReadyPage([closed, open]).url(), "about:blank");
});

test("空列表(页面 target 尚未注册)→ null,调用方应继续等待", () => {
  assert.equal(pickReadyPage([]), null);
});

test("列表里全是已关闭页 → null", () => {
  assert.equal(pickReadyPage([fakePage("about:blank", true)]), null);
});
