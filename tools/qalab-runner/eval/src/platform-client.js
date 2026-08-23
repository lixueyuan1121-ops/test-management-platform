const fetch = require('node-fetch');
const FormData = require('form-data');

// 平台对话测评执行队列客户端:拉 pending / claim / report / 上传 trace。
// 仿 qalab-runner/runner.mjs 的 api():Bearer token + {code,msg,data} 解封。
class PlatformClient {
  constructor(config = {}) {
    this.baseUrl = (config.baseUrl || process.env.BASE_URL || '').replace(/\/$/, '');
    this.token = config.token || process.env.RUNNER_TOKEN || '';
    this.runnerId = config.runnerId || process.env.RUNNER_ID || 'mac-01';
    if (!this.baseUrl) throw new Error('平台模式需配置 BASE_URL(平台地址)');
    if (!this.token) throw new Error('平台模式需配置 RUNNER_TOKEN(在平台「我的设备」注册获取)');
  }

  get _headers() {
    return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` };
  }

  // 解 {code,msg,data} 信封;code 0/200/201/缺省视为成功,返回 data。
  async _api(method, path, body) {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method, headers: this._headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    let env;
    try { env = await res.json(); } catch { throw new Error(`平台返回非 JSON(HTTP ${res.status})`); }
    const code = env.code;
    if (code !== 0 && code !== 200 && code !== 201 && code !== undefined) {
      throw new Error(`平台接口失败(${path}): ${env.msg || code}`);
    }
    return env.data;
  }

  fetchPending(limit = 5) {
    return this._api('GET', `/api/eval-queue?runner=${encodeURIComponent(this.runnerId)}&limit=${limit}`);
  }
  claim(runId) {
    return this._api('POST', `/api/eval-queue/${runId}/claim?runner=${encodeURIComponent(this.runnerId)}`);
  }
  report(runId, body) {
    return this._api('PATCH', `/api/eval-queue/${runId}?runner=${encodeURIComponent(this.runnerId)}`, body);
  }
  // 上报本执行机连上的客户端设备(vm)列表,供平台前端下发时下拉选。
  reportDevices(devices) {
    return this._api('POST', '/api/eval-devices/report', { runner: this.runnerId, devices: devices || [] });
  }
  // trace 走 multipart(与截图同理);multipart 不手设 Content-Type,让 form-data 自动补 boundary。
  async uploadTrace(runId, traceObj) {
    const form = new FormData();
    form.append('file', Buffer.from(JSON.stringify(traceObj), 'utf-8'), {
      filename: `${runId}.json`, contentType: 'application/json',
    });
    const res = await fetch(
      `${this.baseUrl}/api/eval-queue/${runId}/trace?runner=${encodeURIComponent(this.runnerId)}`,
      { method: 'POST', headers: { 'Authorization': `Bearer ${this.token}` }, body: form });
    let env;
    try { env = await res.json(); } catch { throw new Error(`trace 上传返回非 JSON(HTTP ${res.status})`); }
    if (env.code !== 0 && env.code !== undefined) throw new Error(`trace 上传失败: ${env.msg}`);
    return env.data;
  }
}

module.exports = PlatformClient;
