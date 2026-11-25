"""Interpret a completed desktop runner's result.md."""
import re
from dataclasses import dataclass


_SECTION = re.compile(r"^#{1,2}[ \t]+(.+?)\s*$")
_TITLE = re.compile(r"^###[ \t]+(.+)")
_LEGACY_TITLE = re.compile(r"^(?:[-*] |\d+[.)] )?\*\*(.+?)\*\*\s*$")
_DETAIL_LABELS = {"复现", "复现步骤", "操作步骤", "预期", "预期结果", "实际", "实际结果", "证据", "截图", "日志"}
_NO_DEFECT = re.compile(
    r"(?:^|[。；，：\n])[ \t]*(?:本次|本轮|本用例)?[ \t]*(?:未发现|没有发现|没有|无)"
    r"(?:需(?:要)?(?:向极库云)?上报的|可上报的|任何)?(?:产品)?缺陷[。！.\s]*$"
)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


@dataclass
class MdResult:
    verdict: str
    reason: str | None
    findings: list[dict]
    explicit_no_defects: bool = False


def _visible_lines(lines: list[str]) -> list[str]:
    """Ignore fenced examples during parsing, keeping indexes into the original evidence."""
    visible = []
    fence = ""
    for line in lines:
        marker = _FENCE.match(line)
        visible.append("" if fence or marker else line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
    return visible


def _case_counts(lines: list[str]) -> tuple[int, int, int]:
    failed = passed = blocked = 0
    verdict_column = None
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            verdict_column = None
            continue
        cells = [c.strip("*_` \t").casefold() for c in re.split(r"(?<!\\)\|", line[1:])]
        if "用例" in cells and "结论" in cells:
            verdict_column = cells.index("结论")
        elif verdict_column is not None and verdict_column < len(cells):
            value = cells[verdict_column]
            if value in {"fail", "failed", "失败", "不通过"}:
                failed += 1
            elif value in {"pass", "passed", "通过", "正常"}:
                passed += 1
            elif value in {"blocked", "skip", "skipped", "阻塞", "受阻", "跳过", "未执行"}:
                blocked += 1
    return failed, passed, blocked


def _no_defects(text: str) -> bool:
    text = text.strip().strip("*").strip()
    return text.rstrip("。.") in {"无", "无缺陷", "无产品缺陷"} or bool(_NO_DEFECT.search(text))


def _defects(lines: list[str], visible: list[str]) -> tuple[list[dict], bool]:
    headings = [(i, m[1].rstrip("：:")) for i, line in enumerate(visible) if (m := _SECTION.match(line))]
    headings.append((len(lines), ""))
    findings = {}
    negative = False
    for (start, section), (end, _) in zip(headings, headings[1:]):
        if section not in {"产品缺陷", "缺陷"}:
            continue
        body = visible[start + 1:end]
        titles = []
        for i, line in enumerate(body, start + 1):
            match = _TITLE.match(line)
            if not match and section == "缺陷":
                match = _LEGACY_TITLE.match(line)
            if match:
                title = match[1].strip().strip("*").strip()
                if title and title.rstrip("：:") not in _DETAIL_LABELS:
                    titles.append((i, title))
        if not titles and _no_defects("\n".join(body)):
            negative = True
            continue
        if not titles and section == "缺陷":
            # Previous runners also requested flat lists of defects. An introductory
            # label (e.g. 复现步骤) means the bullets below it are supporting detail.
            first = next((s for s in body if s.strip()), "")
            if first.startswith(("- ", "* ")):
                titles = [(i, s[2:].strip()) for i, s in enumerate(body, start + 1) if s.startswith(("- ", "* "))]
        for index, (line_no, title) in enumerate(titles):
            stop = titles[index + 1][0] if index + 1 < len(titles) else end
            if _no_defects(title) or (section == "缺陷" and _no_defects("\n".join(visible[line_no + 1:stop]))):
                negative = True
                continue
            detail = "\n".join(lines[line_no + 1:stop]).strip()
            findings.setdefault(title.casefold(), {"title": title, "detail": detail, "severity": "major"})
    return list(findings.values()), negative and not findings


def analyze_result(markdown: str | None) -> MdResult:
    lines = (markdown or "").splitlines()
    visible = _visible_lines(lines)
    failed, passed, blocked = _case_counts(visible)
    findings, negative = _defects(lines, visible)
    if failed:
        return MdResult("fail", f"{failed} 条用例失败", findings, negative)
    if findings:
        return MdResult("fail", f"{len(findings)} 条产品缺陷", findings)
    if blocked or not passed:
        reason = f"{blocked} 条用例未完成" if blocked else "agent 未产出可判定的用例结果"
        return MdResult("blocked", reason, [], negative)
    return MdResult("pass", None, [], negative)
