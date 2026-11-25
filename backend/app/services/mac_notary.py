"""按预检脚本的报告和实际退出码判读，保留失败项与覆盖告警。"""
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict, ValidationError


def agent_archive(base_url: str, token: str) -> bytes:
    source = Path(__file__).resolve().parents[2] / "resources" / "mac-notary"
    files = {p: (source / p).read_bytes() for p in
             ("agent.mjs", "mac-notary-precheck.mjs", "启动预检.command")}
    files["connection.json"] = json.dumps({"url": base_url.rstrip("/"), "token": token}).encode()
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            entry = ZipInfo("qalab-mac-notary/" + name)
            entry.create_system = 3
            entry.external_attr = (0o100700 if name.endswith(".command") else 0o100600) << 16
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED)
    return buffer.getvalue()


class NativeFile(BaseModel):
    model_config = ConfigDict(strict=True)
    path: str
    executable: bool
    ok: bool
    problems: list[str]
    notes: list[str]
    authority: str | None
    team: str | None


class Report(BaseModel):
    model_config = ConfigDict(strict=True)
    target: str
    teamId: str | None
    checked: int
    failed: int
    warnings: list[str]
    results: list[NativeFile]


def summarize(raw: dict | None, code: int | None, filename: str, team_id: str | None) -> tuple[str, str | None]:
    try:
        report = Report.model_validate(raw)
    except ValidationError:
        return "error", "校验机未产生有效 JSON 报告，请查看执行日志。"
    if PurePosixPath(report.target).name != filename or report.teamId != team_id:
        return "error", "报告的目标包或预期 Team ID 与任务不一致。"
    failed = sum(not item.ok for item in report.results)
    if (report.checked != len(report.results) or report.failed != failed
            or any(item.ok == bool(item.problems) for item in report.results)):
        return "error", "报告的文件明细和统计不一致。"
    expected_code = int(bool(failed or report.warnings or not report.checked))
    if code != expected_code:
        return "error", "执行中断或进程退出码与报告不一致，请查看执行日志。"
    if failed:
        return "invalid", None
    if not report.checked or report.warnings:
        return "partial", None
    return "passed", None
