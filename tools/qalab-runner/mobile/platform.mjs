import { publishResult } from '../result-reporting.mjs';
import { join } from 'node:path';

export function platformClient(config, request = fetch) {
  if (!config.runnerId || !config.runnerToken || !config.baseUrl) throw new Error('平台模式需要 BASE_URL、RUNNER_ID 和设备专属 RUNNER_TOKEN');
  const query = '?consumer=mobile&runner=' + encodeURIComponent(config.runnerId);
  const auth = {Authorization:'Bearer ' + config.runnerToken};
  async function call(method,path,body) {
    const multipart = body instanceof FormData;
    const response = await request(config.baseUrl + path, {method, headers:multipart ? auth : {...auth,'Content-Type':'application/json'},
      body:body === undefined || multipart ? body : JSON.stringify(body), redirect:'error', signal:AbortSignal.timeout(30000)});
    const env = await response.json();
    if (!response.ok || ![0,200,201].includes(env.code)) {
      const error = new Error(method + ' ' + path.split('?')[0] + ' HTTP ' + response.status + ' ' + String(env.detail || env.msg || env.message || '').slice(0,512));
      error.status = response.status;
      throw error;
    }
    return env.data;
  }
  const runPath = id => {
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('无效的 run_id');
    return '/api/exec-queue/' + id;
  };
  return {
    presence: observation => {
      const {version, ...body} = observation;
      return call('POST','/api/devices/mobile-presence',body);
    },
    pending: () => call('GET','/api/exec-queue' + query + '&limit=1'),
    claim: id => call('POST',runPath(id) + '/claim' + query),
    heartbeat: id => call('POST',runPath(id) + '/heartbeat' + query),
    screenshot: async (id,idx,png) => {
      const data = new FormData();
      data.append('file',new Blob([png],{type:'image/png'}),'step-' + idx + '.png');
      const result = await call('POST',runPath(id) + '/screenshot' + query + '&idx=' + idx,data);
      if (!result?.screenshot_url) throw new Error('平台未返回截图地址');
      return result.screenshot_url;
    },
    report: (id,result) => publishResult({runId:id,result,directory:join(config.evidenceDir,config.deviceId,'pending-reports'),
      send: body => call('PATCH',runPath(id) + query,body)}),
  };
}
