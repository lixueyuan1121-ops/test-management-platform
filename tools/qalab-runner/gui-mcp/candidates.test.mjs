import { test } from "node:test";
import assert from "node:assert/strict";
import { validCands, pickCandidates } from "./candidates.mjs";

test("validCands: 只留 by+value 齐全且 by 合法的候选", () => {
  assert.deepEqual(
    validCands([{ by: "css", value: "h1" }, {}, { by: "css" }, { value: "x" }, { by: "bogus", value: "y" }]),
    [{ by: "css", value: "h1" }],
  );
  assert.deepEqual(validCands(null), []);
});

test("validCands: xpath 是合法 by", () => {
  const xp = { by: "xpath", value: "//button[normalize-space(.)='打开文件夹']" };
  assert.deepEqual(validCands([xp, { by: "xpath" }]), [xp], "xpath 有 value 保留、缺 value 剔除");
});

test("pickCandidates: DB 坏/空 → 保持不可用；DB 有效 → 用 DB", () => {
  const builtin = [{ by: "css", value: "h1.home" }];
  assert.deepEqual(pickCandidates([{}], builtin), [], "DB 坏 → 不回落内置");
  assert.deepEqual(pickCandidates([], builtin), [], "DB 空 → 不回落内置");
  assert.deepEqual(
    pickCandidates([{ by: "css", value: "db" }], builtin),
    [{ by: "css", value: "db" }],
    "DB 有效 → 用 DB,不回落",
  );
  assert.deepEqual(pickCandidates([{}], []), [], "两边都坏 → 空");
});
