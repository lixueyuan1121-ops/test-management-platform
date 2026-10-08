import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const GUI_SERVER = fileURLToPath(new URL('./gui-mcp/server.mjs', import.meta.url));
const TOOLS = ['gui_connect', 'gui_list_keys', 'gui_probe', 'gui_inspect', 'gui_goto', 'gui_click', 'gui_hover', 'gui_fill', 'gui_get_text', 'gui_wait_for', 'gui_assert_text', 'gui_type', 'gui_press', 'gui_assert_visible', 'gui_assert_absent', 'gui_capture_response', 'gui_wait_response', 'gui_mock_route', 'gui_unmock_route'];
const quote = value => JSON.stringify(value); // TOML strings/arrays, never shell text.

export function codexCommand(bin = 'codex', platform = process.platform) {
  if (/\.[mc]?js$/i.test(bin)) return { command: process.execPath, prefix: [resolve(bin)] };
  if (platform !== 'win32') return { command: bin, prefix: [] };
  let executable = bin;
  if (!isAbsolute(bin)) executable = execFileSync('where.exe', [bin], { encoding: 'utf8', windowsHide: true }).trim().split(/\r?\n/).find(p => /\.(exe|cmd)$/i.test(p)) || bin;
  if (/\.cmd$/i.test(executable)) {
    const cli = join(dirname(executable), 'node_modules', '@openai', 'codex', 'bin', 'codex.js');
    if (!existsSync(cli)) throw new Error('请将 CODEX_BIN 指向 Codex 原生可执行文件或 @openai/codex/bin/codex.js');
    return { command: process.execPath, prefix: [cli] };
  }
  return { command: executable, prefix: [] };
}

export function codexConfigArgs({ directory, registryFile, cdpUrl, gui = true }) {
  const config = [
    'approval_policy="never"', 'sandbox_mode="read-only"', 'web_search="disabled"',
    'features.shell_tool=false', 'features.unified_exec=false', 'features.plugins=false',
    'features.apps=false', 'features.hooks=false', 'features.multi_agent=false',
    'features.memories=false', 'features.browser_use=false', 'features.browser_use_external=false',
    'project_doc_max_bytes=0', `model_instructions_file=${quote(join(directory, 'instructions.txt'))}`,
  ];
  if (gui) config.push(
    `mcp_servers.qalab_gui.command=${quote(process.execPath)}`,
    `mcp_servers.qalab_gui.args=${quote([GUI_SERVER])}`,
    'mcp_servers.qalab_gui.enabled=true', 'mcp_servers.qalab_gui.required=true',
    `mcp_servers.qalab_gui.enabled_tools=${quote(TOOLS)}`,
    // Dispatch already authorizes these GUI actions. Scope unattended approval
    // to this fixed list, not to other servers or future GUI tools.
    ...TOOLS.map(tool => `mcp_servers.qalab_gui.tools.${tool}.approval_mode="approve"`),
    `mcp_servers.qalab_gui.env.GUI_REGISTRY_FILE=${quote(registryFile)}`,
    `mcp_servers.qalab_gui.env.QALAB_CDP_URL=${quote(cdpUrl)}`,
  );
  return config.flatMap(value => ['-c', value]);
}

function toolData(result) {
  if (result?.structured_content || result?.structuredContent) return result.structured_content || result.structuredContent;
  for (const block of result?.content || []) {
    if (block.type === 'text') try { return JSON.parse(block.text); } catch { /* Non-JSON diagnostics are not assertions. */ }
  }
  return null;
}

export function codexEvent(event) {
  if (event.type !== 'item.completed' || event.item?.type !== 'mcp_tool_call' || event.item.server !== 'qalab_gui') return null;
  const item = event.item, data = toolData(item.result);
  const ok = item.status === 'completed' && !item.error && !item.result?.isError && !item.result?.is_error && data?.pass !== false;
  return { action: `mcp__qalab_gui__${item.tool}`, desc: JSON.stringify(item.arguments || {}), ok,
    ...(item.tool.startsWith('gui_assert_') ? { check: { pass: ok && data?.pass === true, actual: data?.actual ?? data?.pass, expected: item.arguments?.expected ?? true } } : {}),
    ...(!ok ? { error: JSON.stringify(item.error || data || item.result?.content || '工具执行失败').slice(0, 1000) } : {}) };
}

// Ignore personal config/plugins/hooks while preserving the existing CLI login.
// An optional dedicated auth directory is never populated by copying secrets.
export async function runCodex({ prompt, systemPrompt, registry, cdpUrl, gui = true, timeoutMs = 240000,
  bin = process.env.CODEX_BIN || 'codex', home = process.env.CODEX_RUNNER_HOME, model = process.env.CODEX_MODEL,
  log = () => {} }) {
  const started = Date.now(), observed = [];
  if (home && (!isAbsolute(home) || !existsSync(home))) return { spawnError: 'CODEX_RUNNER_HOME 须指向已存在的绝对路径', observed, duration_ms: 0 };
  const directory = mkdtempSync(join(tmpdir(), 'qalab-codex-'));
  const registryFile = join(directory, 'selectors.json');
  writeFileSync(registryFile, JSON.stringify(registry || {}), { mode: 0o600 });
  writeFileSync(join(directory, 'instructions.txt'), systemPrompt.replaceAll('mcp__gui__', 'mcp__qalab_gui__').replaceAll('gui_screenshot 存证', '最终截图由 Runner 自动存证'), { mode: 0o600 });
  try {
    const { command, prefix } = codexCommand(bin);
    // Only the child receives its dedicated Codex config root. Runner config and
    // tokens are not forwarded to the AI process or GUI MCP.
    const env = { ...process.env, ...(home ? { CODEX_HOME: home } : {}) };
    for (const key of ['RUNNER_TOKEN', 'FEISHU_APP_SECRET']) delete env[key];
    const overrides = codexConfigArgs({ directory, registryFile, cdpUrl, gui });
    const args = [...prefix, ...overrides, 'exec', '--ignore-user-config', '--json', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', ...(model ? ['--model', model] : []), '-'];
    return await new Promise(resolveResult => {
      let buffer = '', text = '', err = '', timedOut = false, protocolError = null, spawnError = null, completed = false;
      const child = spawn(command, args, { cwd: directory, env, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
      const kill = () => {
        try {
          if (process.platform === 'win32') {
            try { execFileSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 10000 }); } catch { child.kill('SIGKILL'); }
          } else {
            // Keep the worker's process group intact for cancellation, but stop
            // this AI subtree (including MCP children) on its own timeout.
            const pairs = execFileSync('ps', ['-axo', 'pid=,ppid='], { encoding: 'utf8' }).trim().split('\n').map(line => line.trim().split(/\s+/).map(Number));
            const owned = [child.pid];
            for (let i = 0; i < owned.length; i++) for (const [pid, parent] of pairs) if (parent === owned[i]) owned.push(pid);
            for (const pid of owned.reverse()) try { process.kill(pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
          }
        } catch {
          protocolError = 'Codex 子进程清理失败，将由执行 Worker 清理整个进程组';
          child.kill('SIGKILL');
        }
      };
      const timer = setTimeout(() => { timedOut = true; kill(); }, Math.max(1, timeoutMs - (Date.now() - started)));
      const consume = line => {
        let event; try { event = JSON.parse(line); } catch { return; }
        if (event.type === 'turn.failed') protocolError = event.error?.message || 'Codex 执行失败';
        if (event.type === 'error') err = event.message || event.error?.message || 'Codex 暂时连接失败';
        if (event.type === 'turn.completed') completed = true;
        if (event.type === 'item.completed' && event.item?.type === 'agent_message') text = event.item.text || '';
        const step = codexEvent(event);
        if (step) { observed.push({ no: observed.length + 1, ...step }); log(`Codex → ${step.action} ${step.ok ? '完成' : '失败：' + step.error}`); }
      };
      child.stdout.on('data', chunk => {
        buffer += chunk;
        let end; while ((end = buffer.indexOf('\n')) >= 0) { consume(buffer.slice(0, end)); buffer = buffer.slice(end + 1); }
        if (buffer.length > 2 * 1024 * 1024) { protocolError = 'Codex 单条事件过大'; kill(); }
      });
      child.stderr.on('data', chunk => { err = (err + chunk).slice(-4000); });
      child.stdin.on('error', () => {});
      child.on('error', error => { spawnError = `Codex 启动失败：${error.message}`; protocolError = spawnError; });
      child.on('close', code => {
        clearTimeout(timer); if (buffer.trim()) consume(buffer);
        resolveResult({ text, err: protocolError || err, code, timedOut, spawnError, failed: !!protocolError || !completed, observed, duration_ms: Date.now() - started });
      });
      child.stdin.end(prompt);
    });
  } catch (error) {
    // Do not expose effective config/MCP output (which may contain auth data).
    return { spawnError: error.code === 'ENOENT' ? 'Codex CLI 未安装或 CODEX_BIN 无效' : 'Codex 配置检查失败，请检查 CLI 版本和登录状态', observed, duration_ms: Date.now() - started };
  } finally { rmSync(directory, { recursive: true, force: true }); }
}
