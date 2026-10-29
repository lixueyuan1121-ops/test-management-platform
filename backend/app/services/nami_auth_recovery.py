"""On-demand Nami authentication recovery; no scheduler and no credentials in logs."""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from app.core.config import settings
from app.services import nami_deploy as nd

log = logging.getLogger(__name__)
COOLDOWN_SECONDS = 15 * 60
WORKER_TIMEOUT = 110


def config_path():
    return Path(settings.NAMI_LOGIN_CONFIG_PATH or '~/.openclaw/workspace/config/.nami-login.json').expanduser()


def _private_json(path):
    info = path.stat()
    if os.name == 'posix' and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid()):
        raise ValueError('private file required')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('object required')
    return data


def _atomic_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.new-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(path):
    # This opt-in worker is deployed on Linux. Unsupported hosts fail closed.
    import fcntl
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise nd.NamiDeployError('Nami 自动补救正在进行，请稍后重试') from None
        yield
    finally:
        os.close(fd)


class LoginWorkerError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Nami login worker did not complete')


def _run_worker(path, config, *, accept_terms_once=False):
    python = Path(config.get('python_path', '')).expanduser()
    if not python.is_absolute() or not python.is_file():
        raise LoginWorkerError(22)
    with tempfile.TemporaryDirectory(prefix='.nami-renew-', dir=path.parent) as folder:
        output = Path(folder) / 'result.json'
        worker_config_path = path
        if accept_terms_once:
            # This approval exists for this explicit check only, never in the saved config.
            worker_config_path = Path(folder) / 'login.json'
            _atomic_json(worker_config_path, {**config, '_accept_current_terms_once': True})
        worker = Path(__file__).with_name('nami_login_worker.py')
        # The application has selectors.py; never let it shadow Python's stdlib.
        process = subprocess.Popen([str(python), '-I', str(worker), str(worker_config_path), str(output)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            code = process.wait(timeout=WORKER_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Kill ChromeDriver and browser children too, not just their parent.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise LoginWorkerError(23) from None
        if code:
            raise LoginWorkerError(code)
        candidate = _private_json(output).get('cookie')
        if not isinstance(candidate, str) or not candidate or any(ord(c) < 32 for c in candidate):
            raise LoginWorkerError(21)
        return candidate


def _valid_probe(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    import re
    return bool(parsed.scheme == 'https' and parsed.netloc == 'ns.chat.360.cn'
                and re.fullmatch(r'/zhaomi-so/client-up/[A-Za-z0-9_-]+', parsed.path)
                and not parsed.query and not parsed.fragment)


def renew_cookie(failed_cookie, *, probe_base_url=None, vm_id=None, setup=False, accept_terms_once=False):
    """Return (verified cookie, real short URL); all writes follow live validation.

    setup=True is for an administrator's explicit initial check, not HTTP input.
    It does not enable the feature; the setup caller does so only after success.
    """
    path = config_path()
    try:
        if accept_terms_once and not setup:
            raise nd.NamiDeployError('条款确认仅可用于管理员主动检查')
        config = _private_json(path)
        if not setup and config.get('enabled') is not True:
            raise nd.NamiDeployError('Nami 自动登录未启用')
        if not config.get('username') or not config.get('password') or config.get('terms_acknowledged') is not True:
            raise nd.NamiDeployError('Nami 自动登录配置不完整')
        probe = probe_base_url or config.get('probe_base_url')
        if not _valid_probe(probe):
            raise nd.NamiDeployError('Nami 自动登录缺少有效的短链验证地址')
        vm_id = vm_id or nd._read_vm_id()
        if not vm_id:
            raise nd.NamiDeployError('Nami 自动登录缺少 vm_id')
        fingerprint = hashlib.sha256((config['username'] + '\0' + config['password']).encode()).hexdigest()
        state_path = path.with_name('.nami-login-state.json')
        with _lock(path.with_name('.nami-login.lock')):
            # Another worker or manual refresh may have fixed the credentials already.
            current = nd._read_cookie()
            if not setup and current != failed_cookie:
                try:
                    return current, nd._get_short(vm_id, probe, current)
                except nd.NamiAuthError:
                    pass
            state = _private_json(state_path) if state_path.exists() else {}
            now = time.time()
            if not setup and state.get('fingerprint') == fingerprint:
                if state.get('manual_required'):
                    raise nd.NamiDeployError('Nami 自动登录需要人工验证或更新账号密码')
                if now - state.get('attempt_at', 0) < COOLDOWN_SECONDS:
                    raise nd.NamiDeployError('Nami 自动登录处于冷却期，请稍后重试')
            # Save before starting, so process crashes cannot cause a retry storm.
            state = {'fingerprint': fingerprint, 'attempt_at': now, 'status': 'running', 'manual_required': False}
            _atomic_json(state_path, state)
            try:
                candidate = (_run_worker(path, config, accept_terms_once=True) if accept_terms_once
                             else _run_worker(path, config))
                short = nd._get_short(vm_id, probe, candidate)
                if not nd.is_short_url(short):
                    raise LoginWorkerError(21)
                # Do not apply an old account after an administrator changes configuration.
                if _private_json(path) != config:
                    raise LoginWorkerError(27)
                # A manual refresh must not be overwritten while the browser was logging in.
                if nd._read_cookie() != current:
                    latest = nd._read_cookie()
                    short = nd._get_short(vm_id, probe, latest)
                    candidate = latest
                else:
                    cookie_path = nd._cookie_path()
                    fd, backup = tempfile.mkstemp(prefix=cookie_path.name + '.bak-auto-', dir=cookie_path.parent)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(cookie_path.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                    _atomic_json(cookie_path, {'cookie': candidate})
            except Exception as error:
                code = error.code if isinstance(error, LoginWorkerError) else 26
                # Credential/challenge failure stays blocked until explicit admin action.
                state.update(status='failed', reason=code,
                             manual_required=code in (20, 21, 23, 24, 25) or isinstance(error, nd.NamiAuthError))
                _atomic_json(state_path, state)
                log.warning('Nami automatic login failed (reason=%s, manual_required=%s)', code, state['manual_required'])
                raise nd.NamiDeployError('Nami 自动补救未完成，保留原 Cookie；请检查自动登录状态') from None
            state.update(status='success', completed_at=time.time())
            _atomic_json(state_path, state)
            log.info('Nami automatic login verified and cookie configuration refreshed')
            return candidate, short
    except nd.NamiDeployError:
        raise
    except Exception:
        raise nd.NamiDeployError('Nami 自动登录不可用，请检查安全配置及浏览器运行环境') from None
