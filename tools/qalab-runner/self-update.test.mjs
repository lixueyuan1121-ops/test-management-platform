// tools/qalab-runner/self-update.test.mjs —— node --test
import { test } from "node:test";
import assert from "node:assert/strict";
import { shouldUpdate, readLocalVersion, writeLocalVersion, repairWindowsLauncher } from "./self-update.mjs";
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from "node:fs";
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


test('repair Windows comments and CRLF without overwriting private settings', () => {
  const dir = mkdtempSync(join(tmpdir(), 'launcher-repair-'));
  const original = '@echo off\nREM 首次使用 npm install\nset "RUNNER_TOKEN=test-only-token"\nnode runner.mjs %*\n';
  try {
    writeFileSync(join(dir, 'run.cmd'), original);
    assert.equal(repairWindowsLauncher(dir), true);
    const repaired = readFileSync(join(dir, 'run.cmd'), 'utf8');
    assert.match(repaired, /set "RUNNER_TOKEN=test-only-token"\r\nnode runner.mjs %\*/);
    assert.doesNotMatch(repaired, /[^\x00-\x7f]/);
    assert.equal(readFileSync(join(dir, 'run.cmd.encoding-backup'), 'utf8'), original);
    assert.equal(repairWindowsLauncher(dir), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
