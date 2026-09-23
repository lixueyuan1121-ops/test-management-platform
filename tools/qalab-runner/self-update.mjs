// self-update —— runner 自升级：对比平台版本，下载 zip 解压覆盖本目录，exit 75 由外层脚本重启。
//
// 设计：
// - 版本对比：平台 GET /api/runner/version（文件清单指纹）vs 本地 .runner-version 文件。
//   不在代码里硬编码版本常量——覆盖解压后新代码即新版本，指纹落盘即可。
// - 解压：零依赖方针下不手写 zip 解析——用系统命令(Mac/Linux unzip -o / Windows Expand-Archive)。
// - 安全：只覆盖包内文件；本机 .env / node_modules / evidence 不在包内，天然不被触碰。
// - 失败安全：网络更新失败 → 返回 "failed"，入口以 0 退出，继续使用当前版本。
import desktopLease from "./desktop-lease.cjs";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, createWriteStream, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const VERSION_FILE = ".runner-version";

// 纯函数：要不要更新。远端拿不到版本(空/null)时保守不动。
export function shouldUpdate(local, remote) {
  if (!remote) return false;
  return (local || "") !== remote;
}

export function readLocalVersion(dir) {
  try { return readFileSync(join(dir, VERSION_FILE), "utf-8").trim(); }
  catch { return ""; }
}

export function writeLocalVersion(dir, ver) {
  writeFileSync(join(dir, VERSION_FILE), ver + "\n");
}

function execP(cmd, args) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { windowsHide: true }, (err, stdout, stderr) =>
      err ? reject(new Error(stderr || err.message)) : resolve(stdout));
  });
}

// 解压 zip 到 dir(覆盖同名文件)。Mac/Linux 用 unzip -o;Windows 用 PowerShell Expand-Archive -Force。
async function extract(zipPath, dir) {
  if (process.platform === "win32") {
    const psLiteral = (value) => "'" + value.replace(/'/g, "''") + "'";
    await execP("powershell.exe", ["-NoProfile", "-Command",
      `$ErrorActionPreference = 'Stop'; Expand-Archive -LiteralPath ${psLiteral(zipPath)} -DestinationPath ${psLiteral(dir)} -Force`]);
  } else {
    await execP("unzip", ["-o", zipPath, "-d", dir]);
  }
}

// Old Windows launchers contain private settings, so repair encoding in place
// instead of replacing them with repository placeholders. Preserve executable lines.
export function repairWindowsLauncher(dir) {
  let changed = false;
  for (const name of ['run.cmd', 'run-eval.cmd']) {
    const file = join(dir, name);
    if (!existsSync(file)) continue;
    const original = readFileSync(file, 'utf8');
    if (original.includes('\uFFFD')) continue; // Unknown legacy encoding: preserve it.
    const repaired = original.replace(/^\uFEFF/, '').split(/\r?\n/).map(line => {
      if (/^\s*(?:REM(?:\s|$)|::)/i.test(line) && /[^\x00-\x7f]/.test(line))
        return 'REM Runner configuration is preserved. See README for instructions.';
      if (name === 'run-eval.cmd' && /^\s*echo \[run-eval\]/i.test(line) && /[^\x00-\x7f]/.test(line))
        return 'echo [run-eval] Evaluation runner uses settings from the runner .env file.';
      // Known repository launchers must stop on a bad working directory.
      if (name === 'run-eval.cmd' && /^cd \/d "%(?:EVAL_DIR%|~dp0eval)"$/i.test(line.trim()))
        return line + ' || exit /b 1';
      return line;
    }).join('\r\n');
    if (repaired === original) continue;
    const backup = join(dir, name + '.encoding-backup');
    if (!existsSync(backup)) writeFileSync(backup, original);
    writeFileSync(file, repaired);
    changed = true;
  }
  return changed;
}

// 主流程。返回 "updated" | "current" | "failed"(失败不抛,由调用方决定是否继续启动)。
export async function selfUpdate(options) {
  const release = await desktopLease.acquireDesktopLease();
  if (!release) {
    (options.log || console.log)("[update] 另一 runner 正在使用桌面，延后更新");
    return "current";
  }
  try { return await updateUnlocked(options); }
  finally { await release(); }
}

async function updateUnlocked({ baseUrl, token, dir, log = console.log }) {
  if (process.platform === 'win32' && repairWindowsLauncher(dir)) {
    log('[update] 已修复 Windows 启动脚本编码和换行，原配置保留；下次启动生效');
  }
  const H = { Authorization: `Bearer ${token}` };
  let remote;
  try {
    const res = await fetch(`${baseUrl}/api/runner/version`, { headers: H, signal: AbortSignal.timeout(10000) });
    const env = await res.json();
    if (!res.ok || env.code !== 0) throw new Error(`HTTP ${res.status} code=${env.code}`);
    remote = env.data?.version || "";
  } catch (e) {
    log(`[update] 版本检查失败(跳过更新): ${e.message}`);
    return "failed";
  }
  const local = readLocalVersion(dir);
  if (!shouldUpdate(local, remote)) {
    log(`[update] 已是最新版本 ${remote}`);
    return "current";
  }
  log(`[update] 发现新版本 ${remote}(本地 ${local || "无"}),下载中…`);
  const tmp = mkdtempSync(join(tmpdir(), "qalab-update-"));
  const zipPath = join(tmp, "bundle.zip");
  try {
    const res = await fetch(`${baseUrl}/api/runner/bundle`, { headers: H, signal: AbortSignal.timeout(60000) });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    await pipeline(Readable.fromWeb(res.body), createWriteStream(zipPath));
    await extract(zipPath, dir);
    writeLocalVersion(dir, remote);
    log(`[update] 更新完成 → ${remote},即将重启`);
    return "updated";
  } catch (e) {
    log(`[update] 更新失败(继续用当前版本): ${e.message}`);
    return "failed";
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}
