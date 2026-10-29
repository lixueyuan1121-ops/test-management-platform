"""Automatic recovery contracts. No real accounts, browser or network are used."""
from contextlib import ExitStack
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import subprocess
import sys

from app.services import nami_auth_recovery as recovery
from app.services import nami_deploy as deploy
from app.services import nami_login_worker as worker

PROBE = 'https://ns.chat.360.cn/zhaomi-so/client-up/test-report'
SHORT = 'https://test-report.zhaomi.cn/'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.config_path = self.folder / '.nami-login.json'
        self.cookie_path = self.folder / '.cookie.json'
        self.state_path = self.folder / '.nami-login-state.json'
        self.config = {'enabled': True, 'username': 'fake-account', 'password': 'fake-password',
                       'terms_acknowledged': True, 'probe_base_url': PROBE}
        recovery._atomic_json(self.config_path, self.config)
        recovery._atomic_json(self.cookie_path, {'cookie': 'Q=old; T=old'})
        self.original = self.cookie_path.read_bytes()
        self.stack.enter_context(patch.object(recovery, 'config_path', return_value=self.config_path))
        self.stack.enter_context(patch.object(deploy, '_cookie_path', return_value=self.cookie_path))
        self.stack.enter_context(patch.object(deploy, '_read_vm_id', return_value='test-vm'))
        self.login = self.stack.enter_context(patch.object(recovery, '_run_worker', return_value='Q=new; T=new'))
        self.shorten = self.stack.enter_context(patch.object(deploy, '_get_short', return_value=SHORT))

    def renew(self, **kwargs):
        return recovery.renew_cookie('Q=old; T=old', **kwargs)

    def test_verified_cookie_replaces_atomically_with_private_backup(self):
        self.assertEqual(self.renew(), ('Q=new; T=new', SHORT))
        self.shorten.assert_called_once_with('test-vm', PROBE, 'Q=new; T=new')
        self.assertEqual(deploy._read_cookie(), 'Q=new; T=new')
        backup, = self.folder.glob('.cookie.json.bak-auto-*')
        self.assertEqual(backup.read_bytes(), self.original)
        for path in (backup, self.cookie_path, self.state_path):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        state = recovery._private_json(self.state_path)
        self.assertEqual(state['status'], 'success')
        self.assertNotIn('fake-password', self.state_path.read_text())

    def test_validation_failure_preserves_cookie_and_does_not_log_secret(self):
        self.shorten.side_effect = deploy.NamiAuthError('Q=new; T=new private-response')
        with self.assertLogs(recovery.log, level='WARNING') as logs:
            with self.assertRaises(deploy.NamiDeployError) as error:
                self.renew()
        self.assertEqual(self.cookie_path.read_bytes(), self.original)
        self.assertEqual(list(self.folder.glob('.cookie.json.bak-auto-*')), [])
        self.assertNotIn('private-response', str(error.exception) + ''.join(logs.output))
        self.assertTrue(recovery._private_json(self.state_path)['manual_required'])

    def test_disabled_and_insecure_configuration_never_start_browser(self):
        self.config['enabled'] = False
        recovery._atomic_json(self.config_path, self.config)
        with self.assertRaises(deploy.NamiDeployError):
            self.renew()
        self.config['enabled'] = True
        recovery._atomic_json(self.config_path, self.config)
        self.config_path.chmod(0o644)
        with self.assertRaises(deploy.NamiDeployError):
            self.renew()
        self.login.assert_not_called()

    def test_updated_cookie_is_reused_without_login(self):
        recovery._atomic_json(self.cookie_path, {'cookie': 'Q=another; T=another'})
        self.assertEqual(self.renew(), ('Q=another; T=another', SHORT))
        self.login.assert_not_called()

    def test_manual_refresh_during_login_is_not_overwritten(self):
        def login(*args):
            recovery._atomic_json(self.cookie_path, {'cookie': 'Q=manual; T=manual'})
            return 'Q=browser; T=browser'
        self.login.side_effect = login
        self.assertEqual(self.renew(), ('Q=manual; T=manual', SHORT))
        self.assertEqual(deploy._read_cookie(), 'Q=manual; T=manual')
        self.assertEqual(self.shorten.call_count, 2)

    def test_account_change_during_login_prevents_old_account_write(self):
        def login(*args):
            self.config['username'] = 'new-account'
            recovery._atomic_json(self.config_path, self.config)
            return 'Q=browser; T=browser'
        self.login.side_effect = login
        with self.assertRaises(deploy.NamiDeployError):
            self.renew()
        self.assertEqual(self.cookie_path.read_bytes(), self.original)

    def test_challenge_stops_subsequent_login_even_after_cooldown(self):
        self.login.side_effect = recovery.LoginWorkerError(20)
        with self.assertRaises(deploy.NamiDeployError):
            self.renew()
        with patch.object(recovery.time, 'time', return_value=10**12):
            with self.assertRaisesRegex(deploy.NamiDeployError, '人工验证'):
                self.renew()
        self.login.assert_called_once()
        self.assertEqual(self.cookie_path.read_bytes(), self.original)

    def test_runtime_failure_has_persistent_cooldown(self):
        self.login.side_effect = recovery.LoginWorkerError(22)
        with self.assertRaises(deploy.NamiDeployError):
            self.renew()
        with self.assertRaisesRegex(deploy.NamiDeployError, '冷却期'):
            self.renew()
        self.login.assert_called_once()
        self.assertFalse(recovery._private_json(self.state_path)['manual_required'])

    def test_parallel_worker_cannot_start_another_login(self):
        with recovery._lock(self.folder / '.nami-login.lock'):
            with self.assertRaisesRegex(deploy.NamiDeployError, '正在进行'):
                self.renew()
        self.login.assert_not_called()

    def test_setup_checks_disabled_configuration_without_enabling(self):
        self.config['enabled'] = False
        recovery._atomic_json(self.config_path, self.config)
        self.assertEqual(self.renew(setup=True)[1], SHORT)
        self.assertFalse(recovery._private_json(self.config_path)['enabled'])

    def test_foreign_probe_is_rejected_before_login(self):
        with self.assertRaises(deploy.NamiDeployError):
            self.renew(probe_base_url='https://untrusted.example/report')
        self.login.assert_not_called()

    def test_terms_approval_is_only_for_explicit_check(self):
        with self.assertRaises(deploy.NamiDeployError):
            self.renew(accept_terms_once=True)
        self.login.assert_not_called()
        self.renew(setup=True, accept_terms_once=True)
        self.login.assert_called_once_with(self.config_path, self.config, accept_terms_once=True)
        self.assertNotIn('_accept_current_terms_once', recovery._private_json(self.config_path))


class WorkerBoundaryTests(unittest.TestCase):
    def test_standalone_worker_imports_in_the_full_application_directory(self):
        result = subprocess.run([sys.executable, '-I', str(Path(worker.__file__).resolve())],
                                capture_output=True, text=True, timeout=15)
        # Missing arguments are handled by main; imports must not crash first.
        self.assertEqual(result.returncode, 22)
        self.assertEqual(result.stdout + result.stderr, '')

    def test_terms_approval_uses_a_private_temporary_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_python = root / 'python'
            runtime_python.touch()
            config_path = root / 'config.json'
            config = {'python_path': str(runtime_python), 'password': 'fake-password'}
            recovery._atomic_json(config_path, config)
            def launch(argv, **kwargs):
                self.assertEqual(argv[1], '-I')
                temporary = Path(argv[3])
                self.assertNotEqual(temporary, config_path)
                self.assertTrue(recovery._private_json(temporary)['_accept_current_terms_once'])
                recovery._atomic_json(Path(argv[4]), {'cookie': 'Q=fake; T=fake'})
                return Mock(wait=Mock(return_value=0))
            with patch.object(recovery.subprocess, 'Popen', side_effect=launch):
                recovery._run_worker(config_path, config, accept_terms_once=True)
            self.assertEqual(recovery._private_json(config_path), config)
            self.assertEqual(list(root.glob('.nami-renew-*')), [])

    def test_worker_uses_private_file_paths_and_discards_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / 'private.json'
            runtime_python = root / 'python'
            runtime_python.touch()
            def launch(argv, **kwargs):
                self.assertEqual(argv[1], '-I')
                self.assertEqual(argv[3], str(config_path))
                self.assertNotIn('fake-password', ' '.join(argv))
                self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
                self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
                self.assertTrue(kwargs['start_new_session'])
                recovery._atomic_json(Path(argv[4]), {'cookie': 'Q=fake; T=fake'})
                return Mock(wait=Mock(return_value=0))
            with patch.object(recovery.subprocess, 'Popen', side_effect=launch):
                self.assertEqual(recovery._run_worker(config_path, {'python_path': str(runtime_python), 'password': 'fake-password'}), 'Q=fake; T=fake')
            self.assertEqual(list(root.glob('.nami-renew-*')), [])

    def test_timeout_kills_process_group_and_removes_temporary_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_python = root / 'python'
            runtime_python.touch()
            process = Mock(pid=123456, wait=Mock(side_effect=[subprocess.TimeoutExpired('worker', 95), -9]))
            with patch.object(recovery.subprocess, 'Popen', return_value=process), patch.object(recovery.os, 'killpg') as kill:
                with self.assertRaises(recovery.LoginWorkerError) as error:
                    recovery._run_worker(root / 'config.json', {'python_path': str(runtime_python)})
                self.assertEqual(error.exception.code, 23)
                kill.assert_called_once_with(process.pid, recovery.signal.SIGKILL)
            self.assertEqual(list(root.glob('.nami-renew-*')), [])


class TermsVersionTests(unittest.TestCase):
    def test_saved_consent_only_matches_the_same_account_and_document_contents(self):
        documents = {url: 'original-hash' for url in worker.CONFIRMED_TERMS}
        config = {'username': 'test-account', 'approved_terms': {
            'account': worker.terms_account('test-account'), 'documents': documents}}
        with patch.object(worker, 'terms_snapshot', return_value=documents):
            self.assertTrue(worker.may_reuse_terms(config, worker.CONFIRMED_TERMS))
        changed = {url: 'changed-hash' for url in worker.CONFIRMED_TERMS}
        with patch.object(worker, 'terms_snapshot', return_value=changed):
            self.assertFalse(worker.may_reuse_terms(config, worker.CONFIRMED_TERMS))
        config['username'] = 'different-account'
        with patch.object(worker, 'terms_snapshot') as fetch:
            self.assertFalse(worker.may_reuse_terms(config, worker.CONFIRMED_TERMS))
            fetch.assert_not_called()

    def test_one_time_approval_does_not_accept_changed_links(self):
        config = {'username': 'test-account', '_accept_current_terms_once': True}
        with patch.object(worker, 'terms_snapshot') as fetch:
            self.assertTrue(worker.may_reuse_terms(config, worker.CONFIRMED_TERMS))
            self.assertFalse(worker.may_reuse_terms(config, {'https://work.n.cn/new-terms'}))
            fetch.assert_not_called()

    def test_absent_consent_and_failed_document_fetch_never_allow_acceptance(self):
        self.assertFalse(worker.may_reuse_terms({'username': 'test'}, worker.CONFIRMED_TERMS))
        config = {'username': 'test', 'approved_terms': {'account': worker.terms_account('test')}}
        with patch.object(worker, 'terms_snapshot', side_effect=OSError('network unavailable')):
            with self.assertRaises(OSError):
                worker.may_reuse_terms(config, worker.CONFIRMED_TERMS)


class IntegrationTests(unittest.TestCase):
    def test_only_explicit_auth_errors_trigger_recovery(self):
        with patch.object(deploy, '_get_short', side_effect=deploy.NamiDeployError('HTTP 503')), patch.object(recovery, 'renew_cookie') as renew:
            with self.assertRaises(deploy.NamiDeployError):
                deploy._short_with_recovery('test-vm', PROBE, 'old')
            renew.assert_not_called()
        with patch.object(deploy, '_get_short', side_effect=deploy.NamiAuthError()), patch.object(recovery, 'renew_cookie', return_value=('new', SHORT)) as renew:
            self.assertEqual(deploy._short_with_recovery('test-vm', PROBE, 'old'), SHORT)
            renew.assert_called_once_with('old', probe_base_url=PROBE, vm_id='test-vm')

    def test_provider_auth_response_classification(self):
        response = Mock(status_code=200, text='Forbidden')
        with patch.object(deploy.requests, 'post', return_value=response):
            response.json.return_value = {'code': 110005}
            for call in (lambda: deploy._get_short('test-vm', PROBE, 'fake'), lambda: deploy._upload('html', 'fake')):
                with self.assertRaises(deploy.NamiAuthError):
                    call()
            response.status_code = 401
            with self.assertRaises(deploy.NamiAuthError):
                deploy._get_short('test-vm', PROBE, 'fake')
            response.status_code = 403
            with self.assertRaises(deploy.NamiDeployError) as error:
                deploy._get_short('test-vm', PROBE, 'fake')
            self.assertNotIsInstance(error.exception, deploy.NamiAuthError)

    def test_short_recovery_reuses_uploaded_report(self):
        with patch.object(deploy, '_read_cookie', return_value='old'), patch.object(deploy, '_read_vm_id', return_value='test-vm'), patch.object(deploy, '_upload', return_value=PROBE) as upload, patch.object(deploy, '_get_short', side_effect=deploy.NamiAuthError()), patch.object(recovery, 'renew_cookie', return_value=('new', SHORT)) as renew:
            self.assertEqual(deploy.deploy_html('html', prefer_shortlink=True), SHORT)
            upload.assert_called_once_with('html', 'old')
            renew.assert_called_once_with('old', probe_base_url=PROBE, vm_id='test-vm')

    def test_upload_auth_retries_at_most_once(self):
        with patch.object(deploy, '_read_cookie', return_value='old'), patch.object(deploy, '_upload', side_effect=deploy.NamiAuthError()) as upload, patch.object(recovery, 'renew_cookie', return_value=('new', SHORT)) as renew:
            with self.assertRaises(deploy.NamiAuthError):
                deploy.deploy_html('html', prefer_shortlink=True)
            self.assertEqual(upload.call_count, 2)
            renew.assert_called_once_with('old')

    def test_login_origins_are_exact_https_hosts(self):
        self.assertTrue(worker._trusted('https://work.n.cn/launcher'))
        for url in ('http://work.n.cn/', 'https://work.n.cn.evil.test/', 'https://user@work.n.cn/', 'https://work.n.cn:443/'):
            self.assertFalse(worker._trusted(url))


if __name__ == '__main__':
    unittest.main()
