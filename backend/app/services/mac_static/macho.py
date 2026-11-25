"""Read-only Mach-O signature metadata. This does not evaluate Apple's trust policy."""
import hashlib
import plistlib
import struct

from asn1crypto import cms, tsp  # tsp registers RFC 3161 content types

THIN = {bytes.fromhex(m): (order, header_size) for m, order, header_size in (
    ("feedface", ">", 28), ("cefaedfe", "<", 28),
    ("feedfacf", ">", 32), ("cffaedfe", "<", 32))}
FAT = {bytes.fromhex(m): (order, wide) for m, order, wide in (
    ("cafebabe", ">", False), ("bebafeca", "<", False),
    ("cafebabf", ">", True), ("bfbafeca", "<", True))}
MAGIC = THIN.keys() | FAT.keys()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def architectures(data):
    magic = data[:4]
    if magic in THIN:
        return [(0, len(data))]
    order, wide = FAT[magic]
    require(len(data) >= 8, "架构表不完整")
    count, = struct.unpack_from(order + "I", data, 4)
    if magic == b"\xca\xfe\xba\xbe" and 45 <= (count & 0xffff) <= 120:
        return []  # Java class version
    require(0 < count <= 32, "通用二进制架构表异常")
    require(8 + count * (32 if wide else 20) <= len(data), "架构表越界")
    spans = []
    for i in range(count):
        start, size = struct.unpack_from(order + ("QQ" if wide else "II"), data,
                                         16 + i * (32 if wide else 20))
        require(start >= 8 + count * (32 if wide else 20) and start + size <= len(data) and size >= 28, "架构切片越界")
        spans.append((start, size))
    ordered = sorted(spans)
    require(all(a + n <= b for (a, n), (b, _) in zip(ordered, ordered[1:])), "架构切片重叠")
    return spans


def signature_slots(blob):
    magic, size, count = struct.unpack_from(">III", blob)
    require(magic == 0xfade0cc0 and 0 < count <= 128 and 12 + count * 8 <= size <= len(blob), "签名索引无效")
    spans = {}
    for i in range(count):
        slot, offset = struct.unpack_from(">II", blob, 12 + i * 8)
        require(12 + count * 8 <= offset <= size - 8, "签名槽位置无效")
        length, = struct.unpack_from(">I", blob, offset + 4)
        require(8 <= length <= size - offset and slot not in spans, "签名槽长度或索引无效")
        spans[slot] = (offset, offset + length)
    ordered = sorted(spans.values())
    require(all(end <= start for (_, end), (start, _) in zip(ordered, ordered[1:])), "签名槽重叠")
    return {slot: blob[start:end] for slot, (start, end) in spans.items()}


def code_pages(data, base, sig_offset, directory, problems):
    version, flags, hashes, _, _, count, limit = struct.unpack_from(">7I", directory, 8)
    width, algo, _, exponent = struct.unpack_from("4B", directory, 36)
    if version >= 0x20300 and limit == 0:
        limit, = struct.unpack_from(">Q", directory, 56)
    if version >= 0x20100 and struct.unpack_from(">I", directory, 44)[0]:
        raise ValueError("未支持分散页哈希布局")
    algorithm = {1: ("sha1", 20), 2: ("sha256", 32), 3: ("sha256", 20), 4: ("sha384", 48)}.get(algo)
    require(algorithm and width == algorithm[1] and exponent <= 30, "未支持此页哈希格式")
    step = 1 << exponent if exponent else limit
    require(step and limit == sig_offset and count == (limit + step - 1) // step
            and hashes >= 40 and hashes + width * count <= len(directory), "代码页哈希范围异常")
    for i in range(count):
        start = base + step * i
        with memoryview(data)[start:min(start + step, base + limit)] as page:
            digest = hashlib.new(algorithm[0], page).digest()
        if digest[:width] != directory[hashes + i * width:hashes + (i + 1) * width]:
            problems.append("代码页摘要不匹配，请检查签名后是否修改了文件")
            break


def signer_metadata(wrapper, directory, result):
    require(wrapper[:4] == bytes.fromhex("fade0b01"), "CMS 签名槽无效")
    signed = cms.ContentInfo.load(wrapper[8:])["content"]
    require(len(signed["signer_infos"]) == 1, "无法判读 CMS 签名者")
    signer = signed["signer_infos"][0]
    sid = signer["sid"]
    require(sid.name == "issuer_and_serial_number", "未支持此签名者标识")
    cert = next((c.chosen for c in signed["certificates"] if c.name == "certificate"
                 and c.chosen.issuer == sid.chosen["issuer"]
                 and c.chosen.serial_number == sid.chosen["serial_number"].native), None)
    require(cert is not None, "无法定位签名者证书")
    result["authority"] = cert.subject.native.get("common_name", "")
    extension_ids = {entry["extn_id"].dotted for entry in cert["tbs_certificate"]["extensions"]}
    if "1.2.840.113635.100.6.1.13" not in extension_ids:
        result["problems"].append("签名者证书缺少 Developer ID Application 标识")
    if result["team"] and cert.subject.native.get("organizational_unit_name") != result["team"]:
        result["problems"].append("证书组织单元与 CodeDirectory 的 Team ID 不一致")
    attrs = signer["signed_attrs"]
    if not any(attr["type"].native == "message_digest" for attr in attrs):
        result["problems"].append("CMS 缺少签名目录摘要")
    for attr in attrs:
        if attr["type"].native == "message_digest":
            digest = hashlib.new(signer["digest_algorithm"]["algorithm"].native, directory).digest()
            if attr["values"][0].native != digest:
                result["problems"].append("CMS 中的签名目录摘要不匹配")
    for attr in signer["unsigned_attrs"]:
        if attr["type"].dotted == "1.2.840.113549.1.9.16.2.14":
            token = attr["values"][0]["content"]["encap_content_info"]["content"].parsed
            imprint = token["message_imprint"]
            digest = hashlib.new(imprint["hash_algorithm"]["algorithm"].native, signer["signature"].native).digest()
            if digest != imprint["hashed_message"].native:
                result["problems"].append("时间戳摘要与签名值不一致")
            result["timestamp"] = token["gen_time"].native.isoformat()
            return
    result["problems"].append("缺少 RFC 3161 时间戳，请使用安全时间戳重新签名")


def inspect_slice(data, base, size, expected_team, result):
    order, header = THIN[data[base:base + 4]]
    cpu, _, kind, count, commands = struct.unpack_from(order + "5I", data, base + 4)
    result["arch"] = {0x100000c: "arm64", 0x1000007: "x86_64", 12: "arm", 7: "x86"}.get(cpu, hex(cpu))
    result["executable"] = kind == 2
    require(header + commands <= size and count <= commands // 8, "Mach-O 加载命令越界")
    pos = header
    signature = None
    for _ in range(count):
        cmd, length = struct.unpack_from(order + "II", data, base + pos)
        require(length >= 8 and pos + length <= header + commands, "Mach-O 加载命令长度异常")
        if cmd == 0x1d:  # LC_CODE_SIGNATURE
            require(length >= 16 and signature is None, "签名加载命令异常")
            signature = struct.unpack_from(order + "II", data, base + pos + 8)
        pos += length
    require(pos == header + commands, "加载命令长度不一致")
    if signature is None:
        result["problems"].append("未签名，请使用 Developer ID Application 重新签名")
        return
    start, length = signature
    require(start >= pos and start + length <= size and length <= 32 * 1024 * 1024, "签名数据越界或过大")
    slots = signature_slots(data[base + start:base + start + length])
    directory = slots.get(0, b"")
    require(directory[:4] == bytes.fromhex("fade0c02"), "缺少有效的主 CodeDirectory")
    version, flags = struct.unpack_from(">II", directory, 8)
    problems = result["problems"]
    if flags & 2:
        problems.append("临时签名（ad hoc），请使用 Developer ID Application 重新签名")
    if kind == 2 and not flags & 0x10000:
        problems.append("可执行文件未开启 hardened runtime")
    if version >= 0x20200:
        offset, = struct.unpack_from(">I", directory, 48)
        if offset:
            end = directory.find(b"\0", offset)
            require(end > offset, "Team ID 字段无效")
            result["team"] = directory[offset:end].decode("ascii")
    if expected_team and result["team"] != expected_team:
        problems.append("Team ID 不匹配，预期 " + expected_team)
    if 5 in slots:
        ent = plistlib.loads(slots[5][8:])
        if ent.get("com.apple.security.get-task-allow") is True:
            result["notes"].append("已开启调试权限 get-task-allow=true（仅提示）")
    elif 7 in slots:
        result["coverage"].append("仅有 DER 权限，未读取调试权限")
    code_pages(data, base, start, directory, problems)
    if 0x10000 in slots:
        signer_metadata(slots[0x10000], directory, result)
    elif not flags & 2:
        problems.append("缺少 CMS 签名者证书")


def inspect_file(data, path, team_id, warnings):
    slices = []
    for i, (base, size) in enumerate(architectures(data)):
        item = dict(arch=str(i + 1), executable=False, authority=None, team=None, timestamp=None,
                    problems=[], notes=[], coverage=[])
        try:
            inspect_slice(data, base, size, team_id, item)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, struct.error, OverflowError, OSError) as exc:
            warnings.append(f"{path} [{item['arch']}] 签名元数据读取不完整：{exc}")
        for msg in item["coverage"]:
            warnings.append(f"{path} [{item['arch']}] {msg}")
        slices.append(item)
    if not slices:
        return None
    def messages(key):
        return [f"[{s['arch']}] {msg}" for s in slices for msg in s[key]]
    problems = messages("problems")
    return dict(path=path, executable=any(s["executable"] for s in slices), ok=not problems,
                problems=problems, notes=messages("notes"), authority=slices[0]["authority"],
                team=slices[0]["team"], slices=slices)
