'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

// 沿用 Electron 测试探针的 .app → Contents/MacOS 解析；优先应用声明的主程序，
// 避免包名与二进制不同（WorkBuddy 的主程序名为 Electron）或误选 helper。
function resolveExecutable(inputPath) {
  if (typeof inputPath !== 'string' || !inputPath.trim()) throw new Error('未配置客户端启动路径');
  let executable = path.resolve(inputPath.trim());
  if (!fs.existsSync(executable)) throw new Error(`客户端路径不存在：${executable}`);
  if (fs.statSync(executable).isDirectory() && executable.toLowerCase().endsWith('.app')) {
    const macos = path.join(executable, 'Contents', 'MacOS');
    const plist = path.join(executable, 'Contents', 'Info.plist');
    let name;
    if (process.platform === 'darwin' && fs.existsSync(plist)) {
      try {
        name = execFileSync('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleExecutable', plist],
          { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 3000 }).trim();
      } catch {
        throw new Error(`无法读取应用主程序信息：${plist}；请将 WORKBUDDY_EXE 设置为实际可执行文件路径`);
      }
      if (!name || name === '.' || name === '..' || /[/\\]/.test(name)) {
        throw new Error(`应用主程序名称无效：${plist}`);
      }
    } else {
      const candidates = fs.existsSync(macos) ? fs.readdirSync(macos).filter(entry => {
        const file = path.join(macos, entry);
        if (!fs.statSync(file).isFile() || /helper|crashpad|uninstall|setup|update/i.test(entry)) return false;
        try { fs.accessSync(file, fs.constants.X_OK); return true; } catch { return false; }
      }) : [];
      const bundleName = path.basename(executable).slice(0, -4);
      name = candidates.includes(bundleName) ? bundleName
        : candidates.includes('Electron') ? 'Electron' : candidates.length === 1 ? candidates[0] : null;
      if (!name) throw new Error(`无法确定应用主程序：${macos}；请将 WORKBUDDY_EXE 设置为实际可执行文件路径`);
    }
    executable = path.join(macos, name);
  }
  if (!fs.existsSync(executable)) throw new Error(`客户端可执行文件不存在：${executable}`);
  if (!fs.statSync(executable).isFile()) throw new Error(`客户端启动路径必须是 .app 应用或可执行文件，不能直接启动目录：${executable}`);
  try { fs.accessSync(executable, fs.constants.X_OK); }
  catch { throw new Error(`客户端文件没有执行权限（EACCES）：${executable}；请检查应用安装或配置实际可执行文件`); }
  return executable;
}

module.exports = { resolveExecutable };
