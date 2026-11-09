// Minimal W3C client. All element commands use the session for the configured phone.
const ELEMENT = 'element-6066-11e4-a52e-4f735466cecf';
const PNG_HEADER = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

export function checkedPng(bytes) {
  if (!bytes?.subarray(0, 8).equals(PNG_HEADER)) throw new Error('截图不是有效的 PNG');
  return bytes;
}

export async function appiumSession(config, request = fetch) {
  const android = config.platform === 'android';
  const caps = {
    platformName: android ? 'Android' : 'iOS',
    'appium:automationName': android ? 'UiAutomator2' : 'XCUITest',
    'appium:udid': config.udid,
    'appium:noReset': true,
    'appium:autoLaunch': false,
    'appium:newCommandTimeout': 300,
  };
  if (android) {
    caps['appium:appPackage'] = config.appId;
    caps['appium:settings[waitForIdleTimeout]'] = 500;
    caps['appium:settings[waitForSelectorTimeout]'] = 500;
    if (config.appActivity) caps['appium:appActivity'] = config.appActivity;
    if (config.systemPort) caps['appium:systemPort'] = config.systemPort;
  } else {
    caps['appium:bundleId'] = config.appId;
    caps['appium:wdaLocalPort'] = config.wdaPort;
    if (config.wdaUrl) caps['appium:webDriverAgentUrl'] = config.wdaUrl;
    else if (config.preinstalledWda) caps['appium:usePreinstalledWDA'] = true;
    if (config.xcodeOrgId) {
      caps['appium:xcodeOrgId'] = config.xcodeOrgId;
      caps['appium:xcodeSigningId'] = 'Apple Development';
    }
    if (config.wdaBundleId) caps['appium:updatedWDABundleId'] = config.wdaBundleId;
  }

  let broken, closed = false;
  async function http(method, path, body, timeout = 45000) {
    const redacted = path.replace(/^\/session\/[^/]+/, '/session/:id').replace(/\/element\/[^/]+/, '/element/:id');
    const label = 'Appium ' + method + ' ' + redacted + (body?.script ? ' (' + body.script + ')' : '');
    let response, reply;
    try {
      response = await request(config.appiumUrl + path, {
        method,
        headers: {'Content-Type':'application/json'},
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeout),
      });
      reply = await response.json();
    } catch (cause) {
      const timedOut = ['AbortError', 'TimeoutError'].includes(cause.name);
      broken = label + (timedOut ? ' 超时 ' + timeout + 'ms' : ' 通信失败：' + cause.message);
      throw new Error(broken, {cause});
    }
    if (!response.ok || reply.value?.error) {
      const code = String(reply.value?.error || response.status);
      const reason = String(reply.value?.message || '').slice(0, 600);
      const message = label + ' HTTP ' + response.status + ' ' + code + ' ' + reason;
      if (/invalid session|no such driver|timeout/i.test(code) ||
          /socket hang up|ECONNRESET|connection refused|instrumentation process is not running|not proxied|timed out/i.test(reason))
        broken = message;
      throw new Error(message);
    }
    return reply.value;
  }

  const session = await http('POST', '/session', {capabilities: {alwaysMatch:caps, firstMatch:[{}]}}, 180000);
  if (!session?.sessionId) throw new Error('Appium 没有返回 sessionId');
  const base = '/session/' + encodeURIComponent(session.sessionId);
  async function command(method, path, body) {
    // Timed-out input might still have been delivered. Never replay an input or reuse that session.
    if (broken || closed) throw new Error(broken || 'Appium 会话已关闭');
    const timeout = ['/source', '/screenshot', '/elements'].includes(path) ? 20000 : 45000;
    return http(method, base + path, body, timeout);
  }
  async function close() {
    if (closed) return;
    closed = true;
    await http('DELETE', base, undefined, 8000);
  }
  const received = session.capabilities || {};
  const reportedId = received.deviceUDID || received['appium:deviceUDID'] || received.udid || received['appium:udid'];
  if (reportedId && String(reportedId).toLowerCase() !== config.udid.toLowerCase()) {
    await close();
    throw new Error('Appium 连接到的手机与绑定设备不一致');
  }
  const elem = id => '/element/' + encodeURIComponent(id);
  return {
    platform: config.platform,
    capabilities: received,
    get needsReconnect() { return !!broken || closed; },
    close,
    findAll: async locator => (await command('POST', '/elements', locator)).map(el => el[ELEMENT]),
    click: id => command('POST', elem(id) + '/click', {}),
    clear: id => command('POST', elem(id) + '/clear', {}),
    type: (id, text) => command('POST', elem(id) + '/value', {text}),
    text: id => command('GET', elem(id) + '/text'),
    attribute: (id, name) => command('GET', elem(id) + '/attribute/' + encodeURIComponent(name)),
    displayed: id => command('GET', elem(id) + '/displayed'),
    enabled: id => command('GET', elem(id) + '/enabled'),
    source: () => command('GET', '/source'),
    screenshot: async () => checkedPng(Buffer.from(await command('GET', '/screenshot'), 'base64')),
    mobile: (name, params = {}) => command('POST', '/execute/sync', {script:'mobile: ' + name, args:[params]}),
  };
}
