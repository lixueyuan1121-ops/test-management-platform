// OS 级按键:向操作系统「当前前台窗口」发一次 ESC —— 用于关掉挡住被测客户端的系统窗
// (如误触发的文件资源管理器/原生文件选择框)。CDP/Playwright 的 page.keyboard 只作用于被测页面内部,
// 关不掉 OS 级窗口,故这里走操作系统自带的按键模拟。纯函数(osEscapeCommand)与执行(pressOsEscape)
// 分离,便于单测;执行尽力而为(超时/失败/平台不支持都返回 false,绝不抛),仅作复位自愈的兜底一招。
import { spawn } from "node:child_process";

// 按平台生成「向前台窗口发 ESC」的命令。不支持的平台返回 null(交由页面层 ESC 兜底)。
// - win32:PowerShell + System.Windows.Forms.SendKeys(Windows 自带 .NET,无需安装)。
// - darwin:osascript 发 key code 53(mac 的 Escape 虚拟键码;需系统「辅助功能」权限,否则静默失败)。
export function osEscapeCommand(platform) {
  if (platform === "win32") {
    return { cmd: "powershell", args: ["-NoProfile", "-NonInteractive", "-Command", "Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('{ESC}')"] };
  }
  if (platform === "darwin") {
    return { cmd: "osascript", args: ["-e", 'tell application "System Events" to key code 53'] };
  }
  return null;
}

// 默认执行器:spawn 命令,尽力而为(spawn 抛错/进程 error/超时/退出码非 0 → false)。windowsHide 不弹黑框。
// spawnFn 可注入便于单测(默认真 node:child_process spawn);timer.unref 让超时器不阻止事件循环退出。
export function defaultRunner({ cmd, args }, timeoutMs, spawnFn = spawn) {
  return new Promise((resolve) => {
    let child;
    try { child = spawnFn(cmd, args, { windowsHide: true }); }
    catch { return resolve(false); }
    const timer = setTimeout(() => { try { child.kill(); } catch { /* 已退出 */ } resolve(false); }, timeoutMs);
    timer.unref?.();
    child.on("error", () => { clearTimeout(timer); resolve(false); });
    child.on("exit", (code) => { clearTimeout(timer); resolve(code === 0); });
  });
}

// 向前台窗口发一次 OS 级 ESC。平台不支持 → false;否则用 runner 执行命令。
// runner 可注入便于单测(默认真 spawn)。绝不抛:任何失败都 resolve false。
// timeoutMs 默认 8s:powershell 冷启动 + Defender 扫描实测约 4.5s,给近 2x 余量,别把慢当失败。
export async function pressOsEscape(platform = process.platform, { runner = defaultRunner, timeoutMs = 8000 } = {}) {
  const command = osEscapeCommand(platform);
  if (!command) return false;
  try { return await runner(command, timeoutMs); }
  catch { return false; }
}
