import { test } from "node:test";
import assert from "node:assert/strict";
import { decimate } from "./perf-collect.mjs";

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
