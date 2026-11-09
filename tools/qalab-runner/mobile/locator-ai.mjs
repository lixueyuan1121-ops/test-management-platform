import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { appendFile, mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const roles = new Map(Object.entries({
  home: 'chat home screen', tasks: 'open the task list', newChat: 'start a new chat',
  input: 'editable chat message input', send: 'send the composed chat message',
  stop: 'stop generating the current reply', finished: 'reply generation complete',
  back: 'navigate back to the previous screen',
}));

// Only these fixed structural terms reach the model. Unknown substrings, package
// names, labels, message bodies and user/account identifiers stay on the host.
const vocabulary = new Set((
  'action add agent arrow assistant back bar bottom box btn button cancel cell center chat check close ' +
  'complete completed compose composer confirm container content conversation create dashboard dialog done ' +
  'edit editor empty end enter entry field finish finished footer form forward generate generating go ' +
  'header history home icon image input interrupt item keyboard landing launch list main menu message ' +
  'msg nav navigate navigation new next open panel paper plane pause previous prompt question queue ' +
  'record response result return row run screen send session start state status stop stream streaming ' +
  'submit success tab task tasks text textarea thread toolbar top type user view work workspace write writing'
).split(/\s+/));
const systemPrompt = 'Match the requested mobile UI role to one uniquely suitable control from the supplied list of structural terms. Reply only with its integer index. Reply 0 for insufficient or ambiguous evidence.';
const safeIdentifier = /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/;

function canonicalLocator(item, platform, appId) {
  if (!item || !['id', 'accessibility id'].includes(item.using) || typeof item.value !== 'string') return null;
  let name = item.value;
  const resource = name.indexOf(':id/');
  if (resource >= 0) {
    if (platform !== 'android' || item.using !== 'id' || name.slice(0, resource) !== appId) return null;
    name = name.slice(resource + 4);
  }
  if (!safeIdentifier.test(name)) return null;
  const words = name.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/([A-Z])([A-Z][a-z])/g, '$1 $2')
    .toLowerCase().split(/[^a-z]+/).filter(word => vocabulary.has(word));
  if (!words.length) return null;
  return {locator: {using: item.using, value: item.value}, terms: [...new Set(words)]};
}

const fingerprint = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

async function runClaude(request, bin, timeoutMs, model) {
  const cwd = await mkdtemp(join(tmpdir(), 'qalab-locator-'));
  try {
    const args = ['-p', '--safe-mode', '--setting-sources', 'user', '--settings', '{"disableAllHooks":true}',
      '--disable-slash-commands', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '',
      '--permission-mode', 'dontAsk', '--no-session-persistence', '--output-format', 'text',
      '--system-prompt', systemPrompt, ...(model ? ['--model',model] : []), JSON.stringify(request)];
    const processResult = execute(bin, args, {cwd, timeout: timeoutMs, killSignal: 'SIGKILL',
      windowsHide: true, shell: false, maxBuffer: 16384});
    processResult.child.stdin?.end();
    const {stdout} = await processResult;
    const index = stdout.trim();
    return /^([1-9][0-9]{0,2}|0)$/.test(index) ? Number(index) : null;
  } finally {
    await rm(cwd, {recursive: true, force: true});
  }
}

export function createLocatorAI({platform, appId, build,
  cacheDir = join(homedir(), '.qalab-runner', 'mobile-locators'),
  claudeBin = process.env.CLAUDE_BIN || 'claude', model = process.env.MOBILE_LOCATOR_AI_MODEL || 'sonnet', timeoutMs = 25000,
  select = request => runClaude(request, claudeBin, timeoutMs, model), onDiagnostic} = {}) {
  if (!['android', 'ios'].includes(platform) || !appId || !build) {
    throw new Error('控件识别需要平台、应用标识和构建号');
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120000) throw new Error('控件识别超时配置无效');
  const proposed = new Map();
  const scope = join(cacheDir, fingerprint([platform, appId, build]));
  const latest = key => join(scope, key + '.json');

  async function audit(key, event) {
    await mkdir(scope, {recursive: true, mode: 0o700});
    await appendFile(join(scope, key + '.jsonl'), JSON.stringify({at: new Date().toISOString(), ...event}) + '\n',
      {mode: 0o600});
  }

  return {
    async read(key) {
      if (!roles.has(key)) return null;
      try {
        const locator = JSON.parse(await readFile(latest(key), 'utf8'));
        return canonicalLocator(locator, platform, appId)?.locator ?? null;
      } catch (error) {
        if (error.code === 'ENOENT') return null;
        onDiagnostic?.({key, status: 'unreadable-cache'});
        return null;
      }
    },
    async choose(key, candidates) {
      if (!roles.has(key) || !Array.isArray(candidates)) return null;
      proposed.delete(key);
      const unique = new Map();
      for (const candidate of candidates) {
        const safe = canonicalLocator(candidate, platform, appId);
        if (safe) unique.set(fingerprint(safe.locator), safe);
      }
      const options = [...unique.values()];
      if (!options.length) return null;
      // Truncating the candidate set could hide an equivalent control later on the page.
      if (options.length > 128) {
        await audit(key, {status: 'too-many-candidates', count: options.length});
        return null;
      }
      const request = {role: roles.get(key), candidates: options.map(({locator, terms}, i) =>
        ({index: i + 1, using: locator.using, terms}))};
      const evidence = {candidates: options.map(item => item.locator)};
      let index;
      try {
        index = await select(request);
      } catch (error) {
        const status = error.killed ? 'model-timeout' : 'model-unavailable';
        await audit(key, {...evidence, status});
        onDiagnostic?.({key, status});
        return null;
      }
      const candidate = Number.isSafeInteger(index) && index > 0 ? options[index - 1] : null;
      // If the model saw two indistinguishable controls its numeric choice supplies no evidence.
      const ambiguous = candidate && options.some((other, i) => i !== index - 1 &&
        other.locator.using === candidate.locator.using && other.terms.join(' ') === candidate.terms.join(' '));
      if (!candidate || ambiguous) {
        await audit(key, {...evidence, status: 'unresolved'});
        return null;
      }
      proposed.set(key, candidate.locator);
      await audit(key, {...evidence, status: 'proposed', choice: candidate.locator});
      return {...candidate.locator};
    },
    // Call only when the complete scenario, including behavioral assertions, passed.
    async commit(key, locator) {
      const candidate = proposed.get(key);
      if (!candidate || candidate.using !== locator?.using || candidate.value !== locator?.value) return false;
      await audit(key, {status: 'verified', choice: candidate});
      const temp = join(scope, key + '-' + randomUUID() + '.tmp');
      try {
        await writeFile(temp, JSON.stringify(candidate), {flag: 'wx', mode: 0o600});
        await rename(temp, latest(key));
      } finally {
        await rm(temp, {force: true});
      }
      proposed.delete(key);
      return true;
    },
  };
}
