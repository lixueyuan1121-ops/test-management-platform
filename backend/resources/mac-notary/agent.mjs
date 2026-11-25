import { execFile, spawn } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { constants, createWriteStream } from 'node:fs'
import { access, mkdir, mkdtemp, readFile, readdir, realpath, rm, stat } from 'node:fs/promises'
import { hostname, tmpdir } from 'node:os'
import { basename, dirname, join } from 'node:path'
import { Readable, Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { setTimeout as sleep } from 'node:timers/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'

const HERE = dirname(fileURLToPath(import.meta.url))
const execute = promisify(execFile)
const TARGET_TOOLS = ['codesign', 'hdiutil', 'ditto', 'tar', 'sandbox-exec']
const READONLY_PROFILE = '(version 1)(allow default)(deny file-write*)(allow file-write* (subpath (param "WORK")) (literal "/dev/null"))(deny process-exec (subpath (param "WORK")))'

export async function checkHost() {
  if (process.platform !== 'darwin') throw new Error('请在团队 Mac 上启动，预检需要 macOS。')
  for (const name of TARGET_TOOLS) await access('/usr/bin/' + name, constants.X_OK)
}

function api(config, agent, path, init = {}) {
  const url = new URL('/api/signcheck/agent/' + path, config.url)
  url.searchParams.set('platform', 'macos')
  url.searchParams.set('agent', agent)
  return fetch(url, { ...init, redirect: 'error', headers: {
    Authorization: 'Bearer ' + config.token, ...init.headers,
  } }).then(async res => {
    if (res.ok) return res
    const err = new Error('平台请求失败 HTTP ' + res.status)
    err.status = res.status
    throw err
  })
}

async function download(config, agent, job, file) {
  const res = await api(config, agent, job.id + '/file', { signal: AbortSignal.timeout(30 * 60 * 1000) })
  const hash = createHash('sha256')
  let bytes = 0
  const tap = new Transform({ transform(chunk, _, cb) {
    bytes += chunk.length
    hash.update(chunk)
    cb(bytes > job.file_size ? new Error('下载文件超过声明大小') : null, chunk)
  } })
  await pipeline(Readable.fromWeb(res.body), tap, createWriteStream(file, { flags: 'wx', mode: 0o600 }))
  if (bytes !== job.file_size || hash.digest('hex') !== job.sha256) throw new Error('下载文件的大小或 SHA-256 与上传记录不一致')
}

// Termination can interrupt the scanner before its finally block. Only touch
// mountpoints inside the private TMPDIR created for this particular job.
async function clean(dir, scratch) {
  for (const name of await readdir(scratch)) {
    if (!name.startsWith('notary-precheck-')) continue
    const mount = join(scratch, name, 'mnt')
    const mounted = await stat(mount).catch(() => null)
    if (mounted && mounted.dev !== (await stat(scratch)).dev && await realpath(mount) === mount) {
      await execute('/usr/bin/hdiutil', ['detach', mount, '-force'])
    }
  }
  await rm(dir, { recursive: true, force: true })
}

export async function scan(file, teamId, dir, scratch) {
  const report = join(dir, 'report.json')
  const args = ['-D', 'WORK=' + dir, '-p', READONLY_PROFILE, process.execPath,
    join(HERE, 'mac-notary-precheck.mjs'), file, '--json', report]
  if (teamId) args.push('--team-id', teamId)
  const env = { PATH: '/usr/bin:/bin', TMPDIR: scratch, LANG: 'en_US.UTF-8', HOME: dir }
  const start = Date.now()
  const child = spawn('/usr/bin/sandbox-exec', args, { env, cwd: HERE, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  let timedOut = false
  const stop = setTimeout(() => {
    timedOut = true
    if (child.pid) { try { process.kill(-child.pid, 'SIGKILL') } catch {} }
  }, 60 * 60 * 1000)
  const out = join(dir, 'stdout.log'), err = join(dir, 'stderr.log')
  let code
  try {
    const closed = new Promise((resolve, reject) => {
      child.once('error', reject)
      child.once('close', resolve)
    })
    const [exitCode] = await Promise.all([closed,
      pipeline(child.stdout, createWriteStream(out)), pipeline(child.stderr, createWriteStream(err))])
    code = exitCode
  } finally {
    clearTimeout(stop)
  }
  const result = { exit_code: code, duration_ms: Date.now() - start }
  for (const [key, path] of [['stdout_b64', out], ['stderr_b64', err]]) {
    if ((await stat(path)).size > 8 * 1024 ** 2) {
      result.error = '执行日志超过 8 MiB，无法完整回传。'
    } else result[key] = (await readFile(path)).toString('base64')
  }
  if (timedOut) result.error = '本次预检超过 60 分钟，已中断。'
  try {
    if ((await stat(report)).size > 8 * 1024 ** 2) throw new Error('报告超过 8 MiB')
    result.report = JSON.parse(await readFile(report, 'utf8'))
    if (result.report.target !== file || result.report.teamId !== (teamId ?? null)) {
      throw new Error('报告目标或预期 Team ID 不一致')
    }
  } catch (error) {
    result.error ||= '未得到本次有效报告，请查看日志。' + error.message
    delete result.report
  }
  return result
}

export async function handleJob(config, agent, job) {
  if (job.platform !== 'macos' || basename(job.filename) !== job.filename || !/\.(dmg|zip)$/i.test(job.filename)) {
    throw new Error('平台返回了无效的 Mac 安装包任务')
  }
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'qalab-notary-')))
  const scratch = join(dir, 'work')
  await mkdir(scratch)
  const heartbeat = setInterval(() => api(config, agent, job.id + '/heartbeat', {
    method: 'POST', signal: AbortSignal.timeout(10000),
  }).then(r => r.arrayBuffer()).catch(e => console.error(e.message)), 15000)
  let result = {}
  try {
    const file = join(dir, job.filename)
    await download(config, agent, job, file)
    result = await scan(file, job.team_id, dir, scratch)
  } catch (error) {
    result.error = '预检中断：' + error.message
  } finally {
    try { await clean(dir, scratch) } catch (error) {
      result.error = '清理本次工作目录失败，请检查 ' + dir + ' ' + error.message
    }
    clearInterval(heartbeat)
  }
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await api(config, agent, job.id + '/result', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...result, error: result.error?.slice(0, 2000), agent }), signal: AbortSignal.timeout(60000),
      })
      return (await res.json()).data
    } catch (error) {
      if ([401, 403, 404, 409].includes(error.status) || attempt >= 4) throw error
      await sleep(attempt * 2000)
    }
  }
}

async function main() {
  await checkHost()
  const config = JSON.parse(await readFile(join(HERE, 'connection.json'), 'utf8'))
  const agent = hostname().slice(0, 48) + '-' + randomUUID().slice(0, 8)
  let stopping = false
  const stop = () => { stopping = true; console.log('当前任务完成后退出。') }
  process.on('SIGINT', stop)
  process.on('SIGTERM', stop)
  console.log('已启动 Mac 预检：', config.url, agent, '。返回平台上传安装包。按 Ctrl+C 退出。')
  while (!stopping) {
    try {
      const res = await api(config, agent, 'next', { signal: AbortSignal.timeout(20000) })
      const job = (await res.json()).data
      if (job) {
        console.log('#' + job.id, job.filename, '开始预检')
        const result = await handleJob(config, agent, job)
        console.log('#' + job.id, result?.verdict)
        continue
      }
    } catch (error) {
      if (error.status === 401 || error.status === 403) throw new Error('凭据已过期或无效，请从平台重新下载启动包。')
      console.error(error.message)
    }
    if (!stopping) await sleep(3000)
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch(err => { console.error(err.message); process.exitCode = 1 })
}
