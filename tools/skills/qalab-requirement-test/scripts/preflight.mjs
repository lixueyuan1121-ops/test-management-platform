// Read-only execution profile. Does not launch/restart apps, read .env or import data.
import { readFileSync, existsSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
const options={};for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
try {
  if (!options.runner || !options.cdp || !options.application) throw new Error('需要 --runner 目录 --cdp http://127.0.0.1:端口 --application 待测地址/路径，可选 --out 文件');
  const runner=resolve(options.runner), u=new URL(options.cdp);
  if(u.protocol!=='http:' || !['localhost','127.0.0.1','[::1]'].includes(u.hostname) || u.pathname!=='/' || u.username || u.password || u.search || u.hash) throw new Error('CDP仅接受本机HTTP源地址，不携带凭据或查询参数');
  const files=['runner.mjs','verify-import.mjs','step-executor.mjs','gui-mcp/gui-core.mjs'];
  for(const file of files)if(!existsSync(join(runner,file)))throw new Error('Runner缺文件：'+file);
  if(!readFileSync(join(runner,'runner.mjs'),'utf8').includes('--verify-import'))throw new Error('Runner不支持最终双次验证，需要升级');
  const get=async path=>{const r=await fetch(u.origin+path,{redirect:'error',signal:AbortSignal.timeout(5000)});if(!r.ok)throw new Error(`CDP ${path} HTTP ${r.status}`);return r.json();};
  const version=await get('/json/version'),targets=await get('/json/list');
  if(!version.Browser || !Array.isArray(targets))throw new Error('CDP响应不是可用浏览器');
  const pages=targets.filter(t=>t.type==='page').map(t=>{let url;try{const p=new URL(t.url);url=p.origin+p.pathname;}catch{url='非HTTP页面';}return {title:t.title,url};});
  if(!pages.length)throw new Error('浏览器没有页面，需要打开并登录待测应用');
  const profile={checked_at:new Date().toISOString(),runner_directory:runner,node_version:process.version,host_platform:process.platform,cdp_origin:u.origin,browser:version.Browser,application:options.application,pages,
    pending:['确认目标tab及业务frame唯一','确认应用登录和账号权限','读取同一回填平台的设备、项目、动作能力','确认无其他用例占用设备'],note:'这是连接预检，不证明业务成功、远端Linux桌面可控或平台队列可重跑。'};
  if(options.out)writeFileSync(options.out,JSON.stringify(profile,null,2));console.log(JSON.stringify(profile,null,2));
}catch(e){console.error('执行预检未通过：'+e.message);process.exitCode=1;}
