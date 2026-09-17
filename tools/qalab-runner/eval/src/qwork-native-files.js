'use strict';
// Electron 原生选择文件对话框无法被 CDP 的 setInputFiles 驱动。
// 仅替换本次带随机标题的选择框返回值，其他对话框仍使用原实现，并在 finally 恢复。
const http = require('node:http');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

async function prepareQworkFiles(page, paths, { inspectPort = 9337, inspectHost = '127.0.0.1' } = {}) {
  const WebSocket = require(path.join(path.dirname(require.resolve('playwright-core')), 'lib/utilsBundle.js')).ws;
  const targets = await new Promise((resolve, reject) => {
    const req = http.get(`http://${inspectHost}:${inspectPort}/json/list`, res => {
      let text = ''; res.on('data', d => text += d); res.on('end', () => { try { resolve(JSON.parse(text)); } catch (e) { reject(e); } });
    });
    req.on('error', reject); req.setTimeout(4000, () => req.destroy(new Error('QWork 主进程调试端口未开启，请退出 QWork 后由 runner 启动')));
  });
  const target = targets.find(t => t.type === 'node' && t.webSocketDebuggerUrl);
  if (!target) throw new Error('未取得 QWork 原生附件选择接口');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map(); let seq = 0;
  ws.on('message', raw => {
    let value; try { value = JSON.parse(raw); } catch { return; }
    pending.get(value.id)?.(value);
  });
  // 主进程退出/断连时立即结束等待，避免未监听的 error 使整个执行器退出。
  ws.on('error', error => { for (const receive of [...pending.values()]) receive({ error: error.message }); });
  ws.on('close', () => { for (const receive of [...pending.values()]) receive({ error: '主进程连接已关闭' }); });
  const evaluate = expression => new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('QWork 原生附件选择接口超时')); }, 10000);
    pending.set(id, value => { clearTimeout(timer); pending.delete(id);
      if (value.error || value.result?.exceptionDetails) reject(new Error(value.result?.exceptionDetails?.exception?.description || 'QWork 原生附件选择失败'));
      else resolve(value.result?.result?.value);
    });
    ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
  });
  const marker = `__qalab_files_${randomUUID().replaceAll('-', '')}`;
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { ws.terminate(); reject(new Error('QWork 主进程连接超时')); }, 5000);
      ws.once('open', () => { clearTimeout(timer); resolve(); });
      ws.once('error', e => { clearTimeout(timer); reject(e); });
    });
    await page.evaluate(marker => { window[marker] = true; }, marker);
    await evaluate(`(async()=>{
      const {app,dialog,webContents}=process.getBuiltinModule('module').createRequire(process.execPath)('electron');
      if(app.getName()!=='QWork') throw new Error('附件端口不是 QWork');
      const key=${JSON.stringify(marker)}, files=${JSON.stringify(paths)};
      const matches=await Promise.all(webContents.getAllWebContents().map(async w=>{
        try{return await w.executeJavaScript('window['+JSON.stringify(key)+'] === true')}catch{return false}
      }));
      if(matches.filter(Boolean).length!==1) throw new Error('QWork 主进程与当前页面不匹配');
      const original=dialog.showOpenDialog;
      const restore=()=>{if(dialog.showOpenDialog===replacement)dialog.showOpenDialog=original;clearTimeout(timer);delete globalThis[key]};
      const replacement=async(...args)=>{
        const options=args.at(-1);
        if(options?.title!==key)return original.apply(dialog,args);
        restore();return {canceled:false,filePaths:files};
      };
      const timer=setTimeout(restore,10000);
      globalThis[key]=restore;dialog.showOpenDialog=replacement;
    })()`);
    return await page.evaluate(marker => window.workGui.files.selectTaskAttachments(marker, true), marker);
  } finally {
    if (ws.readyState === WebSocket.OPEN) await evaluate(`globalThis[${JSON.stringify(marker)}]?.()`).catch(() => {});
    await page.evaluate(marker => { delete window[marker]; }, marker).catch(() => {});
    ws.close();
  }
}
module.exports = { prepareQworkFiles };
