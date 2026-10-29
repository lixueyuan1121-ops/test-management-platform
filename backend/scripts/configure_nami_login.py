"""Provision Nami login credentials through a trusted terminal, never argv/logs."""
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import warnings


def main():
    path = Path.home() / '.openclaw/workspace/config/.nami-login.json'
    print('Credentials are stored only on this server (mode 600), outside Git.')
    print('Automatic login stays DISABLED until the end-to-end check passes.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        username = getpass.getpass('Nami account (hidden): ').strip()
        password = getpass.getpass('Nami password (hidden): ')
        confirmation = getpass.getpass('Repeat password (hidden): ')
    if not username or not password or password != confirmation:
        print('Input is empty or passwords differ. No configuration changed.')
        return 1
    runtime = Path.home() / '.local/share/qalab-nami-login'
    config = {'enabled': False, 'username': username, 'password': password,
              'python_path': str(runtime / 'venv/bin/python'),
              'browser_binary': str(runtime / 'chrome-linux64/chrome'),
              'driver_binary': str(runtime / 'chromedriver-linux64/chromedriver'),
              'terms_acknowledged': True,
              'probe_base_url': 'https://ns.chat.360.cn/zhaomi-so/client-up/df1b6ed35dad345397b2fdc7bd59385138f79e5b'}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.nami-login.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('CREDENTIALS_SAVED_DISABLED: configuration saved with mode 600.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print('Provisioning cancelled; credentials are not displayed.')
        sys.exit(1)
