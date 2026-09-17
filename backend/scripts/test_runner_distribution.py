"""Offline release-bundle checks, using tracked files and installed npm dependencies.

Run from backend: PYTHONPATH=. .venv/bin/python scripts/test_runner_distribution.py
No platform/model requests, task claims or desktop launches are made.
"""
import asyncio
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.api import runner_update


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'tools/qalab-runner'
NODE = shutil.which('node')
GUARD = '''
import { syncBuiltinESMExports } from 'node:module';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
const blocked = () => { throw new Error('UNEXPECTED_NETWORK'); };
globalThis.fetch = blocked;
http.request = http.get = https.request = https.get = blocked;
net.Socket.prototype.connect = blocked;
syncBuiltinESMExports();
'''


async def response_bytes(response):
    return b''.join([part async for part in response.body_iterator])


class RunnerDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise RuntimeError('Node is required for the distribution smoke test')
        cls.tmp = tempfile.TemporaryDirectory(prefix='qalab-bundle-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        source = cls.root / 'source'
        # Untracked experiments must not accidentally make an incomplete release pass.
        tracked = subprocess.check_output(
            ['git', 'ls-files', '-z', '--', 'tools/qalab-runner'], cwd=ROOT,
        ).decode().split('\0')
        for rel in filter(None, tracked):
            target = source / Path(rel).relative_to('tools/qalab-runner')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, target)
        # Verify private local data is excluded even when present on the server.
        for rel in ('.env', '.runner-version', 'eval/.env', 'eval/accounts/user.json',
                    'evidence/result.json', 'gui-mcp/node_modules/private.txt'):
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('PRIVATE_FIXTURE', encoding='utf-8')
        (source / 'gui-mcp/playwright-runtime.mjs').write_text(
            "throw new Error('STALE_RUNTIME');", encoding='utf-8',
        )
        with patch.object(runner_update, '_RUNNER_DIR', str(source)):
            cls.bundle = asyncio.run(response_bytes(runner_update.runner_bundle(None)))
        cls.zip_path = cls.root / 'release.zip'
        cls.zip_path.write_bytes(cls.bundle)
        cls.guard = cls.root / 'no-network.mjs'
        cls.guard.write_text(GUARD, encoding='utf-8')

    def install(self, name):
        target = self.root / name
        target.mkdir()
        with zipfile.ZipFile(io.BytesIO(self.bundle)) as archive:
            archive.extractall(target)
        # Reuse only npm packages, never another checkout's runner source modules.
        for subdir in ('gui-mcp', 'eval'):
            dependencies = RUNNER / subdir / 'node_modules'
            self.assertTrue(dependencies.is_dir(), f'Run npm install in {dependencies.parent}')
            destination = target / subdir / 'node_modules'
            if os.name == 'nt':
                shutil.copytree(dependencies, destination)
            else:
                destination.symlink_to(dependencies, target_is_directory=True)
        return target

    def node(self, target, *args):
        result = subprocess.run(
            [NODE, '--import', str(self.guard), *map(str, args)], cwd=target,
            env={**os.environ, 'BASE_URL': 'http://127.0.0.1:1',
                 'RUNNER_TOKEN': 'offline-test', 'RUNNER_ID': 'offline-test'},
            capture_output=True, text=True, encoding='utf-8', timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('UNEXPECTED_NETWORK', result.stdout + result.stderr)
        return result.stdout

    def test_bundle_includes_shared_runtime_but_excludes_device_data_and_launchers(self):
        with zipfile.ZipFile(io.BytesIO(self.bundle)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            for rel in ('runner.mjs', 'step-executor.mjs', 'self-update.mjs',
                        'gui-mcp/runtime-loader.mjs', 'gui-mcp/selectors.json',
                        'eval/src/workbuddy-runner.js', 'eval/src/desktop-runner.js',
                        'eval/src/qwork-runner.js', 'eval/src/qwork-pool.js', 'eval/src/qwork-batch.js',
                        'eval/src/qwork-native-files.js', 'eval/src/qwork-trace.js', 'eval/src/product-routing.js'):
                self.assertIn(rel, names)
            for rel in names:
                parts = Path(rel).parts
                self.assertFalse(set(parts) & runner_update._EXCLUDE_DIRS, rel)
                self.assertNotIn(parts[-1], runner_update._EXCLUDE_FILES)
                self.assertNotIn('PRIVATE_FIXTURE', archive.read(rel).decode('utf-8', errors='ignore'))
            self.assertEqual(archive.read('gui-mcp/playwright-runtime.mjs'),
                             (ROOT / 'backend/app/services/playwright_runtime.mjs').read_bytes())

    def test_standalone_package_loads_functional_and_dialog_runners(self):
        target = self.install('新设备 独立目录')
        self.assertFalse((target / '../../../backend/app/services/playwright_runtime.mjs').exists())
        self.assertIn('启动检查通过', self.node(target, 'runner.mjs', '--check'))
        self.assertIn('platform', self.node(target, 'eval/bin/ai-eval.js', '--help'))

    def test_update_replaces_code_and_preserves_device_settings(self):
        target = self.install("O'Brien 的执行机")
        preserved = {
            '.env': 'RUNNER_TOKEN=local-token\nRUNNER_ID=device-a\n',
            'run.cmd': '@echo off\r\nset RUNNER_TOKEN=local-token\r\n',
            'run.sh': '#!/bin/bash\nexport RUNNER_ID=device-a\n',
            'eval/accounts/user.json': '{"local": "login-state"}',
            'evidence/result.json': '{"local": "evidence"}',
        }
        for rel, value in preserved.items():
            file = target / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(value.encode())
        (target / 'runner.mjs').write_text('// previous version\n', encoding='utf-8')
        # Mock just the version/bundle responses; use the real updater + OS unzip.
        driver = self.root / 'update-test.mjs'
        driver.write_text('''
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const [target, archive] = process.argv.slice(2);
const { selfUpdate } = await import(pathToFileURL(target + '/self-update.mjs'));
let calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  if (url === 'https://offline.invalid/api/runner/version')
    return Response.json({code: 0, data: {version: 'fixture-version'}});
  if (url === 'https://offline.invalid/api/runner/bundle')
    return new Response(readFileSync(archive));
  throw new Error('UNEXPECTED_NETWORK');
};
const options = {baseUrl: 'https://offline.invalid', token: 'fixture', dir: target};
assert.equal(await selfUpdate(options), 'updated');
assert.equal(await selfUpdate(options), 'current');
assert.equal(calls.length, 3);
''', encoding='utf-8')
        self.node(target, driver, target, self.zip_path)
        self.assertEqual((target / '.runner-version').read_text().strip(), 'fixture-version')
        for rel, value in preserved.items():
            self.assertEqual((target / rel).read_bytes(), value.encode(), rel)
        self.assertIn('启动检查通过', self.node(target, 'runner.mjs', '--check'))


if __name__ == '__main__':
    unittest.main()
