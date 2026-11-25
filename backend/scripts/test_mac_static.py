"""Run with python -m unittest scripts.test_mac_static."""
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import dpp

from app.services.mac_static.macho import code_pages
from app.services.mac_static.scan import Scan


def package(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path, value in entries:
            archive.writestr(path, value)
    buf.seek(0)
    return buf


class MacStaticTest(unittest.TestCase):
    def scan(self, archive):
        with tempfile.TemporaryDirectory() as tmp:
            scanner = Scan(None, Path(tmp))
            scanner.archive(archive, "sample.zip", 1)
            return scanner

    def test_nested_native_and_incomplete_archive(self):
        unsigned = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0)
        nested = package([("bin/helper", unsigned), ("Main.class", bytes.fromhex("cafebabe00000034"))])
        scan = self.scan(package([("lib.whl", nested.read()), ("broken.jar", b"invalid")]))
        self.assertEqual(len(scan.results), 1)
        self.assertEqual(scan.results[0]["path"], "sample.zip/lib.whl/bin/helper")
        self.assertIn("未签名", scan.results[0]["problems"][0])
        self.assertIn("broken.jar", scan.warnings[0])

    def test_depth_and_truncated_architecture_table(self):
        bad = package([("fat", bytes.fromhex("cafebabf00000002"))])
        self.assertIn("架构表越界", self.scan(bad).warnings[0])
        for _ in range(7):
            bad = package([("inner.zip", bad.getvalue())])
        self.assertIn("嵌套", self.scan(bad).warnings[0])

    def test_free_space_and_unknown_data_in_dmg(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "partitions.dmg"
            builder = dpp.DmgBuilder()
            padding = bytes(3 * 1024**2)
            builder.add_partition("Apple_Free", padding)
            builder.add_partition("Apple_Free", padding[:-1] + b"x")
            builder.build(str(target))
            scanner = Scan(None, Path(tmp))
            scanner.disk_image(target, target.name)
        self.assertFalse(scanner.results)
        self.assertEqual(len(scanner.warnings), 2)
        self.assertIn("分区 1 ", scanner.warnings[0])
        self.assertIn("未读取到 HFS+ 或 APFS", scanner.warnings[1])

    def test_checks_code_pages_against_signed_digest(self):
        code = b"x" * 4096 + b"last page"
        directory = bytearray(44)
        struct.pack_into(">7I", directory, 8, 0x20000, 0, 44, 0, 0, 2, len(code))
        directory[36:40] = bytes([32, 2, 0, 12])
        directory.extend(hashlib.sha256(code[:4096]).digest())
        directory.extend(hashlib.sha256(code[4096:]).digest())
        problems = []
        code_pages(code, 0, len(code), directory, problems)
        self.assertFalse(problems)
        code_pages(code[:-1] + b"!", 0, len(code), directory, problems)
        self.assertIn("代码页摘要不匹配", problems[0])


if __name__ == "__main__":
    unittest.main()
