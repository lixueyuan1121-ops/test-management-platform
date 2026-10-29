"""Operator-only Nami recovery status/activation; never prints credentials."""
import argparse
import hashlib
import time

from app.services import nami_auth_recovery as recovery
from app.services import nami_deploy as deploy
from app.services import nami_login_worker as worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'check', 'enable', 'disable', 'remember-terms'))
    parser.add_argument('--accept-current-terms-once', action='store_true',
                        help='Only after the administrator explicitly confirms the displayed terms for this check')
    args = parser.parse_args()
    if args.accept_current_terms_once and args.action != 'check':
        parser.error('--accept-current-terms-once is only valid with check')
    path = recovery.config_path()
    try:
        config = recovery._private_json(path)
        if args.action == 'status':
            state_path = path.with_name('.nami-login-state.json')
            state = recovery._private_json(state_path) if state_path.exists() else {}
            print('enabled:', config.get('enabled') is True)
            print('last_status:', state.get('status', 'not_checked'))
            print('manual_required:', bool(state.get('manual_required')))
            print('reason:', state.get('reason', 'none'))
            print('terms_version_saved:', bool(config.get('approved_terms')))
            return 0
        if args.action == 'remember-terms':
            # Operator action only: saving this consent is never an automatic side effect of login.
            state = recovery._private_json(path.with_name('.nami-login-state.json'))
            fingerprint = hashlib.sha256((config['username'] + '\0' + config['password']).encode()).hexdigest()
            if (state.get('status') != 'success' or state.get('fingerprint') != fingerprint
                    or time.time() - state.get('completed_at', 0) > 1800):
                raise deploy.NamiDeployError('请先完成该账号的登录验证，再保存已确认的条款版本')
            documents = worker.terms_snapshot()
            with recovery._lock(path.with_name('.nami-login.lock')):
                if recovery._private_json(path) != config:
                    raise deploy.NamiDeployError('配置已变化，请重新确认条款')
                config['approved_terms'] = {'account': worker.terms_account(config['username']),
                                            'documents': documents, 'confirmed_at': time.time()}
                recovery._atomic_json(path, config)
            print('CONFIRMED_TERMS_VERSION_SAVED')
            return 0
        if args.action == 'disable':
            with recovery._lock(path.with_name('.nami-login.lock')):
                config = recovery._private_json(path)
                config['enabled'] = False
                recovery._atomic_json(path, config)
            print('AUTOMATIC_LOGIN_DISABLED')
            return 0
        # An explicit administrator check may retry after fixing a manual blocker.
        _, short = recovery.renew_cookie(deploy._read_cookie(), setup=True,
                                         accept_terms_once=args.accept_current_terms_once)
        if args.action == 'enable':
            with recovery._lock(path.with_name('.nami-login.lock')):
                if recovery._private_json(path) != config:
                    raise deploy.NamiDeployError('配置已变化，请重新验证后启用')
                config['enabled'] = True
                recovery._atomic_json(path, config)
            print('AUTOMATIC_LOGIN_ENABLED')
        print('SHORTLINK_VERIFIED:', short)
        return 0
    except (OSError, ValueError):
        print('Configuration missing or unsafe; no credentials displayed.')
    except deploy.NamiDeployError as error:
        print(str(error))
    except worker.LoginStopped:
        print('Terms verification failed; no consent version saved.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
