"""Install the pinned, private Jikuyun reporting bundle without changing the service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

ARCHIVE_SHA256 = '7662ea967100c8a22c98c2b0e78800c0b16e428ba463bddb940a0dea7ba5135f'
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / '.skills' / 'jikuyun-bug-report-package'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def install(archive, destination):
    if digest(archive.read_bytes()) != ARCHIVE_SHA256:
        raise ValueError('技能包 SHA-256 与指定版本不符')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source, tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        manifest_name, = [n for n in source.namelist() if n.endswith('/MANIFEST.json')]
        manifest = source.read(manifest_name)
        files = json.loads(manifest)['files']
        staging = Path(temp) / 'package'
        staging.mkdir(mode=0o700)
        for row in files:
            relative = Path(row['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('技能清单包含无效路径')
            data = source.read(str(Path(manifest_name).parent / 'skills' / relative))
            if len(data) != row['size'] or digest(data) != row['sha256']:
                raise ValueError(f'技能文件校验失败 {relative}')
            output = staging / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod(0o700 if 'bin' in relative.parts else 0o600)
        (staging / 'MANIFEST.json').write_bytes(manifest)
        if destination.exists():
            for file in staging.rglob('*'):
                if file.is_file() and (destination / file.relative_to(staging)).read_bytes() != file.read_bytes():
                    raise ValueError(f'现有安装与清单不同 {file.name}')
        else:
            os.rename(staging, destination)
        print(f'校验通过 {len(files)} 个文件 {ARCHIVE_SHA256} {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, default=DEFAULT_ROOT)
    options = parser.parse_args()
    install(options.archive, options.destination)
