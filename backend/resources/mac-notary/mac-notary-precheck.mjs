#!/usr/bin/env node
// macOS 公证预检：在提交 Apple 公证前，检查包内每一个原生文件（Mach-O）的签名是否满足公证要求。
//
// 用法（在 Mac 上执行）：
//   node mac-notary-precheck.mjs <路径> [--team-id TEAM_ID] [--json 报告.json] [--verbose]
//   <路径> 可以是 .dmg、.app、.zip，或任意目录。
//
// 做什么：
//   1. DMG 只读挂载；目录逐层遍历；遇到 zip / whl / jar / tar 包就解开继续查（公证服务也会拆这些包，
//      而 `codesign --verify --deep` 不会，这正是 openviking.zip 漏签没被构建发现的原因）。
//   2. 按文件头识别 Mach-O（不看扩展名），对每个文件检查：
//        - 签名有效（codesign --verify --strict）
//        - 用 Developer ID Application 证书签名（不是临时签名 adhoc，也不是未签名）
//        - 带安全时间戳
//        - 可执行文件开启 hardened runtime
//        - 指定 --team-id 时，Team ID 一致
//      可执行文件带 get-task-allow 权限时只给提示，不算不合格。
//   3. 列出不合格文件及原因；有不合格项时退出码为 1。
//
// 不做什么：不替代真正的公证。Apple 还会做恶意软件扫描等本地查不了的检查，预检通过不保证公证通过。

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const MAX_NESTING = 6;
const ARCHIVE_EXT = /\.(zip|whl|jar|egg|tar|tgz|tar\.gz|tar\.bz2|tar\.xz)$/i;
// 平台预检固定使用系统签名工具。
const CODESIGN = '/usr/bin/codesign';

function parseArgs(argv) {
  const args = { target: null, teamId: null, json: null, verbose: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--team-id') args.teamId = argv[++i];
    else if (a === '--json') args.json = argv[++i];
    else if (a === '--verbose') args.verbose = true;
    else if (a === '-h' || a === '--help') args.help = true;
    else if (!args.target) args.target = a;
    else throw new Error(`多余的参数: ${a}`);
  }
  return args;
}

function run(cmd, cmdArgs) {
  const r = spawnSync(cmd, cmdArgs, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (r.error) throw new Error(`无法执行 ${cmd}: ${r.error.message}`);
  return { code: r.status, out: `${r.stdout || ''}${r.stderr || ''}` };
}

/** 读文件头判断是否 Mach-O，并返回是否为可执行文件（MH_EXECUTE）。不是 Mach-O 返回 null。 */
function machoInfo(file) {
  let fd;
  try {
    fd = fs.openSync(file, 'r');
    const head = Buffer.alloc(64);
    const n = fs.readSync(fd, head, 0, 64, 0);
    if (n < 16) return null;
    const thin = (buf) => {
      const be = buf.readUInt32BE(0);
      if (be === 0xfeedface || be === 0xfeedfacf) return { filetype: buf.readUInt32BE(12) };
      if (be === 0xcefaedfe || be === 0xcffaedfe) return { filetype: buf.readUInt32LE(12) };
      return null;
    };
    const direct = thin(head);
    if (direct) return { executable: direct.filetype === 2 };
    const magic = head.readUInt32BE(0);
    if (magic === 0xcafebabe || magic === 0xcafebabf) {
      // 0xcafebabe 也是 Java class 的文件头：class 的第 5–8 字节是版本号（≥45），通用二进制是架构数（很小）。
      const count = head.readUInt32BE(4);
      if (count === 0 || count > 30) return null;
      const offset = magic === 0xcafebabe ? head.readUInt32BE(16) : Number(head.readBigUInt64BE(16));
      const inner = Buffer.alloc(16);
      if (fs.readSync(fd, inner, 0, 16, offset) < 16) return null;
      const slice = thin(inner);
      return slice ? { executable: slice.filetype === 2 } : null;
    }
    return null;
  } catch {
    return null;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function extractArchive(file, dest) {
  if (/\.(tar|tgz|tar\.gz|tar\.bz2|tar\.xz)$/i.test(file)) return run('/usr/bin/tar', ['-xf', file, '-C', dest]);
  if (process.platform === 'darwin') return run('/usr/bin/ditto', ['-x', '-k', file, dest]);
  return run('unzip', ['-q', '-o', file, '-d', dest]);
}

function checkMachO(file, { executable, teamId }) {
  const problems = [];
  const notes = [];
  const verify = run(CODESIGN, ['--verify', '--strict', file]);
  const info = run(CODESIGN, ['-dvv', file]).out;
  if (/not signed at all/i.test(verify.out + info)) return { problems: ['未签名'], notes, authority: null, team: null };
  if (verify.code !== 0) problems.push(`签名无效：${verify.out.trim().split('\n').pop()}`);

  const authority = (info.match(/^Authority=(.+)$/m) || [])[1] || null;
  const team = (info.match(/^TeamIdentifier=(.+)$/m) || [])[1] || null;
  const flags = (info.match(/flags=0x[0-9a-f]+\(([^)]*)\)/i) || [])[1] || '';

  if (/^Signature=adhoc$/m.test(info) || /adhoc/.test(flags)) {
    problems.push('临时签名（adhoc），不是 Developer ID 证书');
  } else if (!authority || !authority.startsWith('Developer ID Application:')) {
    problems.push(`不是 Developer ID Application 证书（当前：${authority || '无'}）`);
  }
  if (!/^Timestamp=/m.test(info)) problems.push('没有安全时间戳（签名时需加 --timestamp）');
  if (teamId && team !== teamId) problems.push(`Team ID 不一致（当前：${team || '无'}，要求：${teamId}）`);
  if (executable) {
    if (!/\bruntime\b/.test(flags)) problems.push('可执行文件未开启 hardened runtime（签名时需加 --options runtime）');
    const ent = run(CODESIGN, ['-d', '--entitlements', '-', '--xml', file]).out;
    if (/com\.apple\.security\.get-task-allow<\/key>\s*<true\s*\/>/.test(ent)) {
      notes.push('get-task-allow 公证风险：请从发布签名配置中移除调试权限并重新验证');
    }
  }
  return { problems, notes, authority, team };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.target) {
    console.log('用法: node mac-notary-precheck.mjs <.dmg|.app|.zip|目录> [--team-id <TeamID>] [--json <报告路径>] [--verbose]');
    process.exit(args.help ? 0 : 2);
  }
  if (process.platform !== 'darwin') {
    console.error('这个脚本需要在 macOS 上运行（依赖系统的 codesign、hdiutil、ditto）。');
    process.exit(2);
  }
  const target = path.resolve(args.target);
  if (!fs.existsSync(target)) {
    console.error(`找不到: ${target}`);
    process.exit(2);
  }

  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'notary-precheck-'));
  const results = [];
  const warnings = [];
  let mount = null;
  let seq = 0;

  const walk = (dir, label, depth) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      const shown = `${label}/${entry.name}`;
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) { walk(full, shown, depth); continue; }
      if (!entry.isFile()) continue;
      scanFile(full, shown, depth);
    }
  };

  const scanFile = (full, shown, depth) => {
    const macho = machoInfo(full);
    if (macho) {
      const r = checkMachO(full, { executable: macho.executable, teamId: args.teamId });
      results.push({ path: shown, executable: macho.executable, ok: r.problems.length === 0, ...r });
      return;
    }
    if (ARCHIVE_EXT.test(full)) {
      if (depth >= MAX_NESTING) { warnings.push(`嵌套层数过深，未展开: ${shown}`); return; }
      const dest = path.join(work, `x${++seq}`);
      fs.mkdirSync(dest);
      const r = extractArchive(full, dest);
      if (r.code !== 0) { warnings.push(`解包失败，未检查: ${shown}（${r.out.trim().split('\n').pop()}）`); return; }
      walk(dest, shown, depth + 1);
      fs.rmSync(dest, { recursive: true, force: true });
    }
  };

  try {
    const stat = fs.statSync(target);
    const name = path.basename(target);
    if (stat.isDirectory()) {
      walk(target, name, 0);
    } else if (/\.dmg$/i.test(target)) {
      mount = path.join(work, 'mnt');
      fs.mkdirSync(mount);
      const r = run('/usr/bin/hdiutil', ['attach', '-nobrowse', '-readonly', '-noautoopen', '-mountpoint', mount, target]);
      if (r.code !== 0) throw new Error(`挂载 DMG 失败: ${r.out.trim()}`);
      walk(mount, name, 0);
    } else {
      scanFile(target, name, 0);
      if (results.length === 0 && warnings.length === 0 && !ARCHIVE_EXT.test(target)) {
        throw new Error('不支持的输入：需要 .dmg、.app、压缩包或目录');
      }
    }
  } catch (error) {
    console.error(`预检中断: ${error.message}`);
    process.exitCode = 2;
  } finally {
    if (mount && fs.realpathSync(mount) === mount && fs.statSync(mount).dev !== fs.statSync(work).dev) {
      const detach = run('/usr/bin/hdiutil', ['detach', mount, '-force']);
      if (detach.code !== 0) throw new Error(`卸载本次 DMG 失败: ${mount} ${detach.out}`);
    }
    fs.rmSync(work, { recursive: true, force: true });
  }
  if (process.exitCode === 2) return;

  const failed = results.filter(r => !r.ok);
  if (args.verbose) for (const r of results.filter(x => x.ok)) console.log(`通过  ${r.path}`);
  if (failed.length) {
    console.log('不合格文件：');
    for (const r of failed) {
      console.log(`\n  ${r.path}`);
      for (const p of r.problems) console.log(`    - ${p}`);
    }
    const byReason = new Map();
    for (const r of failed) for (const p of r.problems) {
      const key = p.replace(/（.*$/, '');
      byReason.set(key, (byReason.get(key) || 0) + 1);
    }
    console.log('\n按原因统计：');
    for (const [reason, count] of byReason) console.log(`  ${count} 个  ${reason}`);
  }
  const noted = results.filter(r => r.notes.length);
  if (noted.length) {
    console.log(`${failed.length ? '\n' : ''}提示（不计入不合格，不影响退出码）：`);
    for (const r of noted) {
      console.log(`\n  ${r.path}`);
      for (const n of r.notes) console.log(`    - ${n}`);
    }
    console.log('');
  }
  for (const w of warnings) console.log(`警告: ${w}`);
  console.log(`\n共检查 ${results.length} 个原生文件：${results.length - failed.length} 个通过，${failed.length} 个不合格` +
    (warnings.length ? `，${warnings.length} 条警告（有内容未检查）` : ''));
  if (results.length === 0) console.log('没有找到任何原生文件，请确认输入路径是否正确。');
  if (!failed.length && !warnings.length && results.length) {
    console.log('预检通过。注意：这不等于公证一定通过，最终结论以 Apple 公证结果为准。');
  }
  if (args.json) {
    fs.writeFileSync(args.json, JSON.stringify({ target, teamId: args.teamId, checked: results.length, failed: failed.length, warnings, results }, null, 2));
    console.log(`报告已写入 ${args.json}`);
  }
  process.exitCode = failed.length || warnings.length || results.length === 0 ? 1 : 0;
}

main();
