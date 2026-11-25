"""Read package contents in a separate process without mounting or executing them."""
import argparse
from io import BytesIO
import json
import mmap
from pathlib import Path
from uuid import UUID
import stat
import shutil
import struct
import sys
import tarfile
import tempfile
import zipfile

from .macho import MAGIC, inspect_file

ZIP = (".zip", ".whl", ".egg", ".jar")
TAR = (".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz")
LIMITATIONS = [
    "仅读取签名元数据并核对主 CodeDirectory 的代码页摘要。未验证 CMS 公钥签名、证书信任链、吊销状态或时间戳信任链。",
    "未执行 macOS codesign、Gatekeeper、外层 DMG 签名、APP 资源封装或 Apple 公证检查。",
]
MAX_BYTES = 16 * 1024**3
MAX_ENTRIES = 100000


class BudgetReached(Exception):
    pass


class Scan:
    def __init__(self, team, work):
        self.team, self.work = team, work
        self.results, self.warnings = [], []
        self.entries = self.size = 0

    def account(self, size):
        self.entries += 1
        self.size += size
        if len(self.results) >= 5000 or len(self.warnings) >= 1000:
            raise BudgetReached("达到报告明细上限，后续内容未检查")
        if self.entries > MAX_ENTRIES or self.size > MAX_BYTES:
            raise BudgetReached("达到展开上限（10 万项或累计 16 GiB），后续内容未检查")

    def member(self, stream, size, label, depth):
        if len(label) > 2048:
            self.warnings.append(label[:160] + "… 路径过长，未检查")
            return
        if label.lower().endswith((".pkg", ".dmg", ".7z", ".xar")):
            self.warnings.append(label + " 未展开此嵌套格式")
            return
        head = stream.read(4)
        if len(head) != min(size, 4):
            raise ValueError("文件头与声明大小不一致")
        archive = label.lower().endswith(ZIP + TAR)
        if head not in MAGIC and not archive:
            return
        if archive and depth >= 6:
            self.warnings.append("嵌套层数过深，未展开：" + label)
            return
        # Never use an archive member's path when writing the temporary file.
        with tempfile.TemporaryFile(dir=self.work) as temp:
            temp.write(head)
            while True:
                chunk = stream.read(1024**2)
                if not chunk:
                    break
                if temp.tell() + len(chunk) > size:
                    raise ValueError("展开内容超出声明大小")
                temp.write(chunk)
            if temp.tell() != size:
                raise ValueError("展开长度与目录记录不一致")
            temp.seek(0)
            if head in MAGIC:
                with mmap.mmap(temp.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    item = inspect_file(data, label, self.team, self.warnings)
                    if item:
                        self.results.append(item)
            else:
                self.archive(temp, label, depth + 1)

    def archive(self, src, label, depth):
        if label.lower().endswith(ZIP):
            with zipfile.ZipFile(src) as archive:
                for info in archive.infolist():
                    self.account(info.file_size)
                    if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                        continue
                    path = f"{label}/{info.filename}"
                    try:
                        with archive.open(info) as stream:
                            self.member(stream, info.file_size, path, depth)
                    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
                        self.warnings.append(f"{path} 未完整检查：{exc}")
        else:
            with tarfile.open(fileobj=src, mode="r|*") as archive:
                for info in archive:
                    self.account(info.size)
                    if info.isfile():
                        path = f"{label}/{info.name}"
                        try:
                            with archive.extractfile(info) as stream:
                                self.member(stream, info.size, path, depth)
                        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
                            self.warnings.append(f"{path} 未完整检查：{exc}")

    def scan_volume(self, volume, label):
        directories = ["/"]
        while directories:
            directory = directories.pop()
            for entry in volume.list_directory(directory):
                self.account(entry.size)
                name = entry.name
                if name in (".HFS+ Private Directory Data\r", "\0\0\0\0HFS+ Private Data"):
                    continue  # HFS filesystem bookkeeping
                if not name or "/" in name or name in (".", ".."):
                    self.warnings.append(label + " 跳过无效目录条目")
                    continue
                path = directory.rstrip("/") + "/" + name
                if len(path) > 2048:
                    self.warnings.append(label + path[:120] + "… 路径过长，未检查")
                elif entry.kind == "directory":
                    directories.append(path)
                elif entry.kind == "file":
                    if entry.size > 768 * 1024**2:
                        self.warnings.append(label + path + " 文件超过单项读取上限")
                        continue
                    try:
                        data = volume.read_file(path)
                        if len(data) != entry.size:
                            raise ValueError("文件实际长度与目录记录不一致")
                        with BytesIO(data) as stream:
                            self.member(stream, entry.size, label + path, 0)
                    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
                        self.warnings.append(f"{label}{path} 未完整检查：{exc}")
                elif entry.kind != "symlink":
                    self.warnings.append(label + path + " 未读取此类型的目录条目")

    def disk_image(self, target, label):
        import dpp
        found = False
        partition_file = self.work / "partition.img"
        with dpp.DmgArchive.open(str(target)) as image:
            partitions = image.partitions
            if len(partitions) > 128 or sum(p.size for p in partitions) > MAX_BYTES:
                raise BudgetReached("磁盘映像的分区数量或展开大小超过上限")
            seen = set()
            for part in partitions:
                if part.id in seen:
                    raise ValueError("磁盘映像存在重复的分区标识")
                seen.add(part.id)
                if part.size > min(8 * 1024**3, shutil.disk_usage(self.work).free // 2):
                    raise BudgetReached("磁盘映像分区超过临时空间上限")
                image.extract_partition_to(part.id, str(partition_file))
                try:
                    with partition_file.open("rb") as f:
                        header = f.read(32768)
                    if partition_file.stat().st_size != part.size:
                        raise ValueError("解码后的分区长度不一致")
                    if header[1024:1026] in (b"H+", b"HX"):
                        volume_reader = dpp.HfsVolume
                    elif header[32:36] == b"NXSB":
                        volume_reader = dpp.ApfsVolume
                        # The reader opens the default APFS volume. Record extra volumes.
                        volume_ids = struct.unpack_from("<100Q", header, 184)
                        if sum(bool(v) for v in volume_ids) > 1:
                            self.warnings.append(f"{label} 分区 {part.id} 仅检查默认 APFS 卷")
                    elif (part.size <= len(header) and (not header.strip(b"\0")
                          or header[510:512] == b"\x55\xaa" or header.startswith((b"EFI PART", b"PM"))
                          or header[:16] in APPLE_GPT_TYPES)):
                        continue
                    else:
                        self.warnings.append(f"{label} 未识别分区 {part.id} 的文件系统")
                        continue
                    with volume_reader.open(str(partition_file)) as volume:
                        self.scan_volume(volume, f"{label}/分区{part.id}")
                    found = True
                finally:
                    partition_file.unlink(missing_ok=True)
        if not found:
            self.warnings.append("DMG 中未读取到 HFS+ 或 APFS 文件系统")


# First GPT partition type GUID (little-endian on disk) in small partition tables.
APPLE_GPT_TYPES = {UUID(guid).bytes_le for guid in (
    "48465300-0000-11AA-AA11-00306543ECAC", "7C3457EF-0000-11AA-AA11-00306543ECAC",
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--team-id")
    args = parser.parse_args()
    if sys.platform != "win32":
        import resource
        limits = {resource.RLIMIT_CPU: 290, resource.RLIMIT_FSIZE: 8 * 1024**3}
        if sys.platform.startswith("linux"):
            limits[resource.RLIMIT_AS] = 3 * 1024**3
        for kind, maximum in limits.items():
            soft, hard = resource.getrlimit(kind)
            if soft == resource.RLIM_INFINITY or soft > maximum:
                resource.setrlimit(kind, (maximum, hard))
    scan = Scan(args.team_id, args.report.parent)
    try:
        if args.label.lower().endswith(".dmg"):
            scan.disk_image(args.target, args.label)
        else:
            with args.target.open("rb") as file:
                scan.archive(file, args.label, 1)
    except Exception as exc:
        scan.warnings.append(f"{args.label} 未完整检查：{str(exc)[:500]}")
    failed = sum(not item["ok"] for item in scan.results)
    report = dict(engine="server-static", target=args.label, teamId=args.team_id,
                  checked=len(scan.results), failed=failed, results=scan.results,
                  warnings=scan.warnings, limitations=LIMITATIONS)
    args.report.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
