const fetch = require('node-fetch');
const FormData = require('form-data');

// 平台对话测评执行队列客户端:拉 pending / claim / report / 上传 trace。
// 仿 qalab-runner/runner.mjs 的 api():Bearer token + {code,msg,data} 解封。
class PlatformClient {
  constructor(config = {}) {
    this.baseUrl = (config.baseUrl || process.env.BASE_URL || '').replace(/\/$/, '');
    this.token = config.token || process.env.RUNNER_TOKEN || '';
    this.runnerId = config.runnerId || process.env.RUNNER_ID || 'mac-01';
    // 本机在跑哪个被测产品(namiwork/workbuddy),随 fetchPending 上报,供平台多产品分机挑机。默认 namiwork。
    this.engine = config.engine || process.env.EVAL_ENGINE || 'namiwork';
    this.claims = new Map();
    this.heartbeatTimer = null;
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
      timeout: 30000,
      body: body ? JSON.stringify(body) : undefined,
    });
    let env;
    try { env = await res.json(); } catch { throw new Error(`平台返回非 JSON(HTTP ${res.status})`); }
    const code = env.code;
    if (!res.ok || (code !== 0 && code !== 200 && code !== 201 && code !== undefined)) {
      throw new Error(`平台接口失败: ${env.msg || env.detail || res.status}`);
    }
    return env.data;
  }

  fetchPending(limit = 5) {
    return this._api('GET', `/api/eval-queue?runner=${encodeURIComponent(this.runnerId)}&limit=${limit}&engine=${encodeURIComponent(this.engine)}&dynamic=true`);
  }
  async claimGroup(conv) {
    const data = await this._api('POST', `/api/eval-queue/${conv[0].run_id}/claim?runner=${encodeURIComponent(this.runnerId)}&whole_group=true&engine=${encodeURIComponent(this.engine)}`);
    const ids = new Set(data && data.run_ids);
    if (!data || !data.claim_token || ids.size !== conv.length || conv.some(it => !ids.has(it.run_id))) {
      throw new Error('整组认领结果不匹配,请确认服务端已升级');
    }
    for (const id of ids) this.claims.set(id, data.claim_token);
    if (!this.heartbeatTimer) {
      this.heartbeatTimer = setInterval(() => {
        for (const [id, token] of this.claims) {
          this._api('POST', `/api/eval-queue/${id}/heartbeat?runner=${encodeURIComponent(this.runnerId)}&claim_token=${token}`)
            .catch(error => console.warn(`[eval-queue] run ${id} heartbeat failed: ${error.message}`));
        }
      }, 60000);
      this.heartbeatTimer.unref();
    }
  }
  stopHeartbeat() {
    clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    this.claims.clear();
  }
  _executionQuery(runId) {
    const token = this.claims.get(runId);
    if (!token) throw new Error(`run ${runId} 未认领,禁止回填`);
    return `runner=${encodeURIComponent(this.runnerId)}&claim_token=${encodeURIComponent(token)}`;
  }
  claim(runId) {
    if (this.claims.has(runId)) return Promise.resolve();
    return this._api('POST', `/api/eval-queue/${runId}/claim?runner=${encodeURIComponent(this.runnerId)}`);
  }
  async report(runId, body) {
    const data = await this._api('PATCH', `/api/eval-queue/${runId}?${this._executionQuery(runId)}`, body);
    this.claims.delete(runId);
    return data;
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
      `${this.baseUrl}/api/eval-queue/${runId}/trace?${this._executionQuery(runId)}`,
      { method: 'POST', headers: { 'Authorization': `Bearer ${this.token}` }, body: form, timeout: 30000 });
    let env;
    try { env = await res.json(); } catch { throw new Error(`trace 上传返回非 JSON(HTTP ${res.status})`); }
    if (!res.ok || (env.code !== 0 && env.code !== undefined)) throw new Error(`trace 上传失败: ${env.msg || env.detail || res.status}`);
    return env.data;
  }
}

module.exports = PlatformClient;
