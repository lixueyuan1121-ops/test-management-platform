"""Install the opt-in Linux browser runtime, isolated from backend dependencies."""
import os
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import platform
import stat
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import venv
import zipfile

CHROME_VERSION = '156.0.8078.73'
SELENIUM_VERSION = '4.51.0'
BASE = f'https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION}/linux64'


def download(package, cache, *, url=None, block_size=4 * 1024 * 1024):
    """Bounded range downloads survive interrupted terminal/SSH sessions."""
    url = url or f'{BASE}/{package}.zip'
    with urlopen(Request(url, method='HEAD'), timeout=30) as response:
        size = int(response.headers['Content-Length'])
    if not 0 < size < 1024 * 1024 * 1024:
        raise ValueError('Unexpected browser archive size')
    folder = cache / package
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    blocks = list(range(0, size, block_size))

    def part(start):
        end = min(size, start + block_size) - 1
        path = folder / f'{start}.part'
        if path.is_file() and path.stat().st_size == end - start + 1:
            return path
        temporary = path.with_suffix('.partial')
        for attempt in range(3):
            try:
                deadline = time.monotonic() + max(120, block_size / 8192)
                request = Request(url, headers={'Range': f'bytes={start}-{end}'})
                with urlopen(request, timeout=60) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                        raise ValueError('Official download did not honor the requested range')
                    with temporary.open('wb') as output:
                        remaining = end - start + 1
                        while remaining:
                            if time.monotonic() > deadline:
                                raise TimeoutError('Browser archive part exceeded its time budget')
                            block = response.read1(min(64 * 1024, remaining))
                            if not block:
                                raise OSError('Incomplete browser archive part')
                            output.write(block)
                            remaining -= len(block)
                os.replace(temporary, path)
                print(f'{package}: part {start // block_size + 1}/{len(blocks)} saved', flush=True)
                return path
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    print(f'{package}: {size // (1024 * 1024)} MiB, up to 6 download connections', flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        parts = list(pool.map(part, blocks))
    archive = folder / 'download.zip'
    with archive.open('wb') as output:
        for path in parts:
            with path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    output.write(block)
    return archive


def selenium_wheel(runtime):
    """Use the official PyPI wheel and verify its published SHA-256."""
    with urlopen(f'https://pypi.org/pypi/selenium/{SELENIUM_VERSION}/json', timeout=30) as response:
        metadata = json.load(response)
    wheel, = [item for item in metadata['urls'] if item['filename'].endswith('-py3-none-any.whl')]
    from urllib.parse import urlsplit
    if urlsplit(wheel['url']).netloc != 'files.pythonhosted.org':
        raise ValueError('Unexpected PyPI download host')
    cache = runtime / 'downloads' / 'selenium'
    archive = download(SELENIUM_VERSION, cache, url=wheel['url'], block_size=512 * 1024)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != wheel['digests']['sha256']:
        raise ValueError('Selenium wheel checksum mismatch')
    target = archive.with_name(wheel['filename'])
    os.replace(archive, target)
    return target


def main():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise SystemExit('This installer requires Linux x86_64.')
    runtime = Path.home() / '.local/share/qalab-nami-login'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Prevent two installers from changing the same runtime/cache concurrently.
    import fcntl
    lock = (runtime / '.install.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for package in ('chrome-linux64', 'chromedriver-linux64'):
        destination = runtime / package
        if destination.exists():
            print(f'Keeping existing {package}; checking versions below.', flush=True)
            continue
        print(f'Downloading {package} {CHROME_VERSION} from Google...', flush=True)
        archive = download(package, runtime / 'downloads' / CHROME_VERSION)
        with tempfile.TemporaryDirectory(prefix='.install-', dir=runtime) as folder:
            with zipfile.ZipFile(archive) as source:
                for item in source.infolist():
                    relative = Path(item.filename)
                    mode = item.external_attr >> 16
                    if (relative.is_absolute() or '..' in relative.parts or
                            relative.parts[0] != package or stat.S_ISLNK(mode)):
                        raise ValueError('Unexpected archive entry')
                    target = Path(source.extract(item, folder))
                    if not item.is_dir():
                        target.chmod(0o700 if mode & 0o111 else 0o600)
            os.rename(Path(folder) / package, destination)
    for binary in (runtime / 'chrome-linux64/chrome', runtime / 'chromedriver-linux64/chromedriver'):
        result = subprocess.run([str(binary), '--version'], check=True, text=True, capture_output=True)
        if CHROME_VERSION not in result.stdout:
            raise SystemExit('Existing browser version differs; update the runtime explicitly.')
        print(result.stdout.strip(), flush=True)
    python = runtime / 'venv/bin/python'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(runtime / 'venv')
    wheel = selenium_wheel(runtime)
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--index-url', 'https://pypi.org/simple', str(wheel)], check=True)
    print('RUNTIME_INSTALLED: browser launch still requires a smoke check.', flush=True)


if __name__ == '__main__':
    main()
