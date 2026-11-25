"""Privately install the reviewed tuitui-report-suite ZIP on a QALab server."""
import argparse
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path

ARCHIVE_SHA256 = "6acb51d0477befc7efdba76a68df7f546bd0d869e05900863a5d2375cb62637d"
DESTINATION = Path(__file__).resolve().parents[1] / ".skills" / "tuitui-report-suite"


def install(archive, destination=DESTINATION):
    with Path(archive).open("rb") as source:
        if hashlib.sha256(source.read()).hexdigest() != ARCHIVE_SHA256:
            raise ValueError("安装包 SHA256 与本版附件不符")
        source.seek(0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as package, tempfile.TemporaryDirectory(dir=destination.parent) as staging:
            root = Path(staging) / "tuitui-report-suite"
            # Only the byte-exact reviewed archive can reach this extraction step.
            package.extractall(staging)
            if destination.exists():
                for member in root.rglob("*"):
                    if member.is_file() and member.read_bytes() != (destination / member.relative_to(root)).read_bytes():
                        raise ValueError("目标目录含不同版本，请保留原安装并选择新的目录")
                return destination
            if os.name != "nt":
                (root / "scripts/bin/qihoo-sso-cli").chmod(0o700)
            root.rename(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    args = parser.parse_args()
    print(install(args.archive, args.destination))
