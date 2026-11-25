import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { cp, mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { checkHost, scan } from '../../backend/resources/mac-notary/agent.mjs'

test('Mac 系统工具读取 ZIP、DMG 并保留未覆盖内容', { skip: process.platform !== 'darwin' }, async () => {
  await checkHost()
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'mac-notary-test-')))
  try {
    const src = join(dir, '应用')
    await mkdir(src)
    await cp('/bin/echo', join(src, 'helper'))
    await writeFile(join(src, 'broken.jar'), 'not an archive')
    const packages = ['样本 软件.zip', '样本 软件.dmg']
    execFileSync('/usr/bin/ditto', ['-c', '-k', src, join(dir, packages[0])])
    execFileSync('/usr/bin/hdiutil', ['create', '-quiet', '-fs', 'HFS+', '-format', 'UDZO', '-srcfolder', src, join(dir, packages[1])])
    for (const name of packages) {
      const job = join(dir, name + '-run')
      const scratch = join(job, 'work')
      await mkdir(scratch, { recursive: true })
      const file = join(dir, name)
      const result = await scan(file, 'AAAAAAAAAA', job, scratch)
      assert.equal(result.error, undefined, JSON.stringify([result.error, Buffer.from(result.stderr_b64, 'base64').toString()]))
      assert.equal(result.exit_code, 1)
      assert.equal(result.report.target, file)
      assert.equal(result.report.teamId, 'AAAAAAAAAA')
      assert.equal(result.report.checked, 1)
      assert.equal(result.report.failed, 1)
      assert.match(result.report.warnings.join(), /broken.jar/)
    }
  } finally { await rm(dir, { recursive: true, force: true }) }
})
