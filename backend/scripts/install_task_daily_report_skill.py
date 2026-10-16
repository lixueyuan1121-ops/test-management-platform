"""Install the supplied archive privately; no network, shell or deployment changes."""
import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = BACKEND / '.skills' / 'tuitui-daily-report'
ADAPTER = BACKEND / 'skill_adapters' / 'tuitui_daily_report.py'
SCRIPT = 'scripts/fetch_daily_report.py'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def install(archive, destination=DEFAULT_DESTINATION, expected_sha256=None):
    archive, destination = Path(archive), Path(destination)
    archive_hash = sha256(archive.read_bytes())
    if expected_sha256 and archive_hash != expected_sha256:
        raise ValueError('安装包 SHA256 不匹配')
    adapter = ADAPTER.read_bytes()
    if destination.exists():
        manifest = json.loads((destination / 'platform-install.json').read_text())
        if (manifest['archive_sha256'] == archive_hash and manifest['adapter_sha256'] == sha256(adapter)
                and manifest['script_sha256'] == sha256((destination / SCRIPT).read_bytes())):
            return manifest
        raise ValueError('目标目录已存在不同版本，请使用新目录并保留原安装')
    with zipfile.ZipFile(archive) as package:
        files = {}
        total = 0
        for info in package.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
                raise ValueError('安装包包含不安全路径')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('安装包不能包含符号链接')
            if info.is_dir() or path.parts[0] == '__MACOSX' or path.name == '.DS_Store':
                continue
            if path.parts[0] != 'tuitui-daily-report-skill-latest' or len(path.parts) < 2:
                raise ValueError('安装包目录与预期不符')
            relative = str(PurePosixPath(*path.parts[1:]))
            total += info.file_size
            if total > 50_000_000 or len(files) >= 100 or relative in files:
                raise ValueError('安装包过大或包含重复文件')
            files[relative] = package.read(info)
    for required in ('SKILL.md', SCRIPT):
        if required not in files:
            raise ValueError(f'安装包缺少 {required}')
    original = files[SCRIPT].decode('utf-8')
    marker = 'if __name__ == "__main__":'
    if any(original.count(part) != 1 for part in ('def generate_report(', 'def main():', marker)):
        raise ValueError('skill 脚本接口发生变化，需检查适配器')
    adapted = original.replace('def generate_report(', 'def _release_generate_report(', 1)
    adapted = adapted.replace('def main():', 'def _release_main():', 1)
    adapted = adapted.replace(marker, adapter.decode('utf-8') + '\n\n' + marker, 1)
    compile(adapted, SCRIPT, 'exec')
    manifest = {'name': 'tuitui-daily-report', 'platform_adapter_version': 1,
                'archive_sha256': archive_hash, 'adapter_sha256': sha256(adapter),
                'upstream_script_sha256': sha256(files[SCRIPT]),
                'script_sha256': sha256(adapted.encode('utf-8'))}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.daily-report-', dir=destination.parent))
    try:
        for relative, content in files.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (staging / 'scripts/fetch_daily_report.upstream.py').write_bytes(files[SCRIPT])
        (staging / SCRIPT).write_text(adapted, encoding='utf-8')
        (staging / 'platform-install.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (staging / 'PLATFORM.md').write_text(
            '# 平台任务输入\n\n使用 `python scripts/fetch_daily_report.py --tasks-json`，从 stdin 读取 JSON。\n'
            '平台输入保留所有任务，按显式状态分组，不拉取群消息、不发消息、不部署 HTML。\n'
            '使用平台姓名，保留进度、风险、下一步动作及 goodcase。原入口和原脚本副本均保留。\n', encoding='utf-8')
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    print(json.dumps(install(args.archive, args.destination, args.sha256), ensure_ascii=False, indent=2))
