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

test("pickCandidates: DB 坏/空 → 回落内置同名 key；DB 有效 → 用 DB", () => {
  const builtin = [{ by: "css", value: "h1.home" }];
  assert.deepEqual(pickCandidates([{}], builtin), builtin, "DB 坏 → 回落内置");
  assert.deepEqual(pickCandidates([], builtin), builtin, "DB 空 → 回落内置");
  assert.deepEqual(
    pickCandidates([{ by: "css", value: "db" }], builtin),
    [{ by: "css", value: "db" }],
    "DB 有效 → 用 DB,不回落",
  );
  assert.deepEqual(pickCandidates([{}], []), [], "两边都坏 → 空");
});
