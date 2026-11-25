"""签名校验：360Base_test.exe 输出解析 + 总体结论 + 上传文件落盘位置。

原程序（32 位控制台程序）对每个文件打印四行，形如：

    <<*********************FilePath:C:\\...\\a.exe
    EmbeddedSign(微软数字签名)->\t签名状态：签名有效\t
    Old360Sign(360旧签名)->\t\t签名状态：没有签名\t
    New360Sign(360超级签名)->\t签名状态：签名有效\t
    S3360Sign(s3签名)->\t\t签名状态：签名有效\t
    *********************FilePath:C:\\...\\a.exe>>

某项取不到状态时，该行 "->" 后是失败说明（如 "VerifyTrust调用失败!"）。
中文按系统 ANSI 代码页（简体中文 = GBK）输出，所以 agent 回传原始字节、由这里解码。
"""
from pathlib import Path

KINDS = ("EmbeddedSign", "Old360Sign", "New360Sign", "S3360Sign")
KIND_LABELS = {
    "EmbeddedSign": "微软数字签名",
    "Old360Sign": "360 旧签名",
    "New360Sign": "360 超级签名",
    "S3360Sign": "360 第三代签名（S3）",
}
# 顺序有讲究："不支持的签名状态" 必须先于其余项判断（它不含其余关键字，但语义上最特殊）。
_STATES = (
    ("不支持的签名状态", "unsupported"),
    ("签名有效", "valid"),
    ("没有签名", "unsigned"),
    ("签名无效", "invalid"),
    ("签名过期", "expired"),
)
_RECOGNIZED = {"valid", "unsigned", "invalid", "expired", "unsupported"}

# 缺运行库时 Windows 给的退出码：STATUS_DLL_NOT_FOUND / STATUS_SXS_CANT_GEN_ACTCTX / ERROR_SXS_CANT_GEN_ACTCTX
_RUNTIME_MISSING_CODES = {0xC0000135, 0xC0150002, 14001}

MSG_RUNTIME = "原校验工具缺少运行依赖。请检查校验机是否安装了 Visual C++ 2008 x86 运行库。"
MSG_LOCALE = "原校验工具未返回可识别的状态。请检查校验机 Windows 的「非 Unicode 程序的语言」是否为中文（简体，中国）。"

STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage" / "signcheck"
MAX_OUTPUT_CHARS = 60000


def file_path(check_id: int) -> Path:
    return STORAGE_DIR / f"{check_id}.bin"


def remove_file(check_id: int) -> None:
    try:
        file_path(check_id).unlink()
    except OSError:
        pass


def decode_output(raw: bytes) -> str:
    """校验程序输出解码：优先 GBK（gb18030 是其超集），其次 UTF-8，最后带替换符兜底。"""
    if not raw:
        return ""
    for enc in ("gb18030", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace")


def parse_output(stdout: str) -> list[dict]:
    """从原始输出提取四项签名状态。每项 {kind, label, state, detail}。

    state: valid / unsigned / invalid / expired / unsupported / error(该项校验出错) / missing(输出里没有这一项)
    """
    found: dict[str, dict] = {}
    for line in (stdout or "").splitlines():
        for kind in KINDS:
            if kind in found:
                continue
            head = line.find(kind + "(")
            if head < 0:
                continue
            arrow = line.find(")->", head)
            if arrow < 0:
                continue
            rest = line[arrow + 3:].strip()
            state, detail = "error", rest
            for needle, name in _STATES:
                if needle in rest:
                    state, detail = name, ""
                    break
            found[kind] = {"kind": kind, "label": KIND_LABELS[kind], "state": state, "detail": detail}
    return [
        found.get(k) or {"kind": k, "label": KIND_LABELS[k], "state": "missing", "detail": ""}
        for k in KINDS
    ]


def summarize(signatures: list[dict], exit_code: int | None, stdout: str) -> tuple[str, str | None]:
    """四项状态 → (verdict, diagnostic)。口径与原桌面客户端一致：

    没有任何一项拿到可识别状态 → error；有无效 → invalid；有过期 → expired；
    四项没全拿到 → partial；有至少一项有效 → passed；四项都是无签名 → unsigned。
    """
    states = [s["state"] for s in signatures]
    recognized = [s for s in states if s in _RECOGNIZED]
    if not recognized:
        code = exit_code & 0xFFFFFFFF if isinstance(exit_code, int) else None
        if code in _RUNTIME_MISSING_CODES:
            return "error", MSG_RUNTIME
        if "不存在" in (stdout or ""):
            return "error", "校验程序找不到待校验文件（校验机临时目录可能被清理或路径过长）。"
        if not (stdout or "").strip() and exit_code not in (0, None):
            return "error", MSG_RUNTIME
        return "error", MSG_LOCALE
    known = [s for s in states if s in ("valid", "unsigned", "invalid", "expired")]
    if "invalid" in states:
        return "invalid", None
    if "expired" in states:
        return "expired", None
    if len(known) < len(KINDS):
        return "partial", None
    if "valid" in states:
        return "passed", None
    return "unsigned", None


def clip(text: str | None) -> str | None:
    if text and len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n…（输出过长，已截断）"
    return text
