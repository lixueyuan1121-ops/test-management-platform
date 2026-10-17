"""Install the supplied archive privately; no network, shell or deployment changes."""
import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
import uuid
from pathlib import Path, PurePosixPath

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = BACKEND / '.skills' / 'tuitui-daily-report'
ADAPTER = BACKEND / 'skill_adapters' / 'tuitui_daily_report.py'
SCRIPT = 'scripts/fetch_daily_report.py'
PLATFORM_STYLE = BACKEND / 'skill_adapters' / 'feature_report.css'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def install(archive, destination=DEFAULT_DESTINATION, expected_sha256=None, upgrade=False):
    archive, destination = Path(archive), Path(destination)
    archive_hash = sha256(archive.read_bytes())
    if expected_sha256 and archive_hash != expected_sha256:
        raise ValueError('安装包 SHA256 不匹配')
    adapter = ADAPTER.read_bytes()
    platform_style = PLATFORM_STYLE.read_bytes()
    if destination.exists():
        manifest = json.loads((destination / 'platform-install.json').read_text())
        if (manifest['archive_sha256'] == archive_hash and manifest['adapter_sha256'] == sha256(adapter)
                and manifest['script_sha256'] == sha256((destination / SCRIPT).read_bytes())
                and manifest.get('skill_sha256') == sha256((destination / 'SKILL.md').read_bytes())
                and manifest.get('template_sha256') == sha256((destination / 'references/template.html').read_bytes())
                and manifest.get('platform_style_sha256') == sha256(platform_style)
                and (destination / 'references/platform.css').exists()
                and manifest['platform_style_sha256'] == sha256((destination / 'references/platform.css').read_bytes())):
            return manifest
        if not upgrade:
            raise ValueError('目标目录已存在不同版本，请用 --upgrade 备份后升级')
        if manifest['archive_sha256'] != archive_hash:
            raise ValueError('升级仅支持同一原始安装包，请另选目录安装不同包')
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
    for required in ('SKILL.md', SCRIPT, 'references/template.html'):
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
    manifest = {'name': 'tuitui-daily-report', 'platform_adapter_version': 11,
                'skill_sha256': sha256(files['SKILL.md']),
                'template_sha256': sha256(files['references/template.html']),
                'platform_style_sha256': sha256(platform_style),
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
        (staging / 'references/platform.css').write_bytes(platform_style)
        (staging / 'scripts/fetch_daily_report.upstream.py').write_bytes(files[SCRIPT])
        # Preserve the exact bytes hashed in the manifest on every platform.
        (staging / SCRIPT).write_bytes(adapted.encode('utf-8'))
        (staging / 'platform-install.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (staging / 'PLATFORM.md').write_text(
            '# 平台任务输入\n\n使用 `python scripts/fetch_daily_report.py --tasks-json`，从 stdin 读取 JSON。\n'
            '--summary-prompt-json 准备总结提示，平台现有 AI 服务总结后，经 --validate-summary-json 校验再生成正文。保留所有功能/需求条目，不拉取群消息、不发消息。--html-json 使用用户指定参考网页的渐变页头与编号列表版式渲染当前正文，由平台复用测评报告服务发布。\n'
            'AI 总结与事实审校只接收功能/需求列（title）和条目 ID。项目名仅用于标题前缀；规划版本与状态仅用于本地排版，按版本分组、测试完成和已上线在前；明确版本且所选条目均为测试完成/已上线时采用版本更新说明，其他版本采用测试日报。其他列不进入模型或正文。原入口和原脚本副本均保留。\n', encoding='utf-8')
        backup = None
        if destination.exists():
            backup = destination.with_name(destination.name + '.backup-' + uuid.uuid4().hex[:12])
            destination.rename(backup)
        try:
            staging.rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument('--sha256')
    parser.add_argument('--upgrade', action='store_true', help='备份旧目录后升级同一安装包的适配器')
    args = parser.parse_args()
    print(json.dumps(install(args.archive, args.destination, args.sha256, args.upgrade), ensure_ascii=False, indent=2))
