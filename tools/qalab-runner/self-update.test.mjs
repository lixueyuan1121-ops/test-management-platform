// tools/qalab-runner/self-update.test.mjs —— node --test
import { test } from "node:test";
import assert from "node:assert/strict";
import { shouldUpdate, readLocalVersion, writeLocalVersion } from "./self-update.mjs";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("shouldUpdate: 本地无版本(首次) → 需要更新", () => {
  assert.equal(shouldUpdate("", "abc12345"), true);
  assert.equal(shouldUpdate(null, "abc12345"), true);
});

test("shouldUpdate: 版本一致 → 不更新", () => {
  assert.equal(shouldUpdate("abc12345", "abc12345"), false);
});

test("shouldUpdate: 版本不同 → 更新", () => {
  assert.equal(shouldUpdate("abc12345", "def67890"), true);
});

test("shouldUpdate: 远端版本空/异常 → 不更新(保守)", () => {
  assert.equal(shouldUpdate("abc12345", ""), false);
  assert.equal(shouldUpdate("abc12345", null), false);
});

test("readLocalVersion/writeLocalVersion round-trip", () => {
  const dir = mkdtempSync(join(tmpdir(), "selfup-"));
  try {
    assert.equal(readLocalVersion(dir), "");          // 无文件 → 空串
    writeLocalVersion(dir, "abc12345");
    assert.equal(readLocalVersion(dir), "abc12345");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
