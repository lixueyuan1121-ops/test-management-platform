#!/usr/bin/env python3
"""安装经版本管理的 Mac 回填入口；默认只校验，加 --apply 才写文件。"""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_file(source, target):
    fd, temporary = tempfile.mkstemp(prefix='.platform-update-', dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(target, apply=False):
    source = Path(__file__).resolve().parent
    manifest = json.loads((source / 'manifest.json').read_text())
    lock = target / 'runs/.sync.lock'
    if apply:
        # 与 sync-platform 共享互斥锁，避免更新过程中执行到一半的新旧文件。
        lock.mkdir()
    try:
        changes = []
        for name, hashes in manifest.items():
            current = target / 'lib' / name
            incoming = source / 'lib' / name
            if current.is_symlink() or not current.is_file():
                raise RuntimeError(f'不是可更新的普通文件：{current}')
            if digest(incoming) != hashes['after_sha256']:
                raise RuntimeError(f'安装源校验失败：{name}')
            actual = digest(current)
            if actual == hashes['after_sha256']:
                continue
            if actual != hashes['before_sha256']:
                raise RuntimeError(f'{name} 有其他修改，请先人工合并；未覆盖文件')
            changes.append(name)
        if not apply or not changes:
            return {'ready': True, 'pending': changes, 'applied': False}
        backup = target / '.platform-report-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup.mkdir(parents=True)
        for name in changes:
            shutil.copy2(target / 'lib' / name, backup / name)
        replaced = []
        try:
            for name in changes:
                replace_file(source / 'lib' / name, target / 'lib' / name)
                replaced.append(name)
            for name in changes:
                if digest(target / 'lib' / name) != manifest[name]['after_sha256']:
                    raise RuntimeError(f'安装后校验失败：{name}')
        except Exception:
            for name in replaced:
                replace_file(backup / name, target / 'lib' / name)
            raise
        return {'ready': True, 'applied': True, 'updated': changes, 'backup': str(backup)}
    finally:
        if apply:
            lock.rmdir()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True, help='现有 qa-dispatch-agent 目录')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.target.resolve(), args.apply), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'更新中止：{error}\n')
