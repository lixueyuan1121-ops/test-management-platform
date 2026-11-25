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
    failed_cases: tuple[str, ...] = ()


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


def _case_results(lines: list[str]) -> tuple[list[tuple[str, str]], int, int]:
    failed = []
    passed = blocked = 0
    columns = {}
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            columns = {}
            continue
        cells = [c.strip("*_` \t").replace(r"\|", "|") for c in re.split(r"(?<!\\)\|", line[1:])]
        if "用例" in cells and "结论" in cells:
            columns = {name: i for i, name in enumerate(cells) if name}
        elif columns and columns["结论"] < len(cells):
            value = cells[columns["结论"]].casefold()
            if value in {"fail", "failed", "失败", "不通过"}:
                row = {name: cells[i] for name, i in columns.items() if i < len(cells) and cells[i]}
                title = row.get("用例") or f"未命名失败用例 {len(failed) + 1}"
                detail = "\n".join(f"{name}：{v}" for name, v in row.items() if name not in {"用例", "结论"})
                failed.append((title, detail))
            elif value in {"pass", "passed", "通过", "正常"}:
                passed += 1
            elif value in {"blocked", "skip", "skipped", "阻塞", "受阻", "跳过", "未执行"}:
                blocked += 1
    return failed, passed, blocked


def _no_defects(text: str) -> bool:
    text = text.strip().strip("*").strip()
    return text.rstrip("。.") in {"无", "无缺陷", "无产品缺陷"} or bool(_NO_DEFECT.search(text))


def _defects(lines: list[str], visible: list[str], has_failures: bool) -> tuple[list[dict], list[dict], bool]:
    headings = [(i, m[1].rstrip("：:")) for i, line in enumerate(visible) if (m := _SECTION.match(line))]
    headings.append((len(lines), ""))
    findings = {}
    case_notes = []
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
        if not titles:
            text = "\n".join(body).strip()
            no_defects = _no_defects(text)
            negative |= no_defects
            # A list without issue titles may just be instructions for reproducing a failed case.
            if has_failures and text and not _no_defects(next((s for s in body if s.strip()), "")):
                case_notes.append({"title": "", "detail": "\n".join(lines[start + 1:end]).strip()})
            if has_failures or no_defects:
                continue
        if not titles and section == "缺陷":
            # Previous runners also requested flat lists of defects. An introductory
            # label (e.g. 复现步骤) means the bullets below it are supporting detail.
            first = next((s for s in body if s.strip()), "")
            if first.startswith(("- ", "* ")):
                titles = [(i, s[2:].strip()) for i, s in enumerate(body, start + 1) if s.startswith(("- ", "* "))]
        for index, (line_no, title) in enumerate(titles):
            stop = titles[index + 1][0] if index + 1 < len(titles) else end
            if _no_defects(title):
                negative = True
                continue
            detail = "\n".join(lines[line_no + 1:stop]).strip()
            item = {"title": title, "detail": detail, "severity": "major"}
            if section == "缺陷" and _no_defects("\n".join(visible[line_no + 1:stop])):
                case_notes.append(item)
                negative = True
            else:
                findings.setdefault(title.casefold(), item)
    return list(findings.values()), case_notes, negative and not findings


_CASE_ID = re.compile(r"(?<![\w-])(?:[a-z]+[-_]\d+|(?:tc|case|c)\d+)(?:[-_]\d+)*(?![\w-])", re.I | re.ASCII)
_CASE_LINK = re.compile(r"^关联用例\s*[:：]\s*(.+)$")


def case_keys(label: str) -> set[str]:
    return {label.strip().casefold()}.union(s.casefold() for s in _CASE_ID.findall(label))


def linked_cases(item: dict) -> set[str]:
    keys = {s.casefold() for s in _CASE_ID.findall(item["title"])}
    for line in _visible_lines((item.get("detail") or "").splitlines()):
        if link := _CASE_LINK.match(line.strip().replace("**", "")):
            for label in re.split(r"[、,，;；]", link[1]):
                keys.update(case_keys(label))
    return keys


def _include_failed_cases(failed: list[tuple[str, str]], findings: list[dict], notes: list[dict]) -> None:
    references = set().union(*(linked_cases(item) for item in findings))
    covered = references | {item["title"].casefold() for item in findings}
    evidence = {}
    for note in notes:
        for key in linked_cases(note) | {note["title"].casefold()}:
            evidence.setdefault(key, []).append(note["detail"])
    # Older reports with one case could describe several separate issues without links.
    if len(failed) == 1 and findings and not notes and not references:
        return
    for title, detail in failed:
        keys = case_keys(title)
        if keys & covered:
            continue
        extra = [part for key in sorted(keys) for part in evidence.get(key, ())]
        if len(failed) == 1 and len(notes) == 1 and not extra:
            extra = [notes[0]["detail"]]
        description = "\n\n".join(dict.fromkeys(part for part in [detail, *extra] if part))
        findings.append({"title": title, "detail": description, "severity": "major"})
        covered.update(keys)


def analyze_result(markdown: str | None) -> MdResult:
    lines = (markdown or "").splitlines()
    visible = _visible_lines(lines)
    failed, passed, blocked = _case_results(visible)
    findings, notes, negative = _defects(lines, visible, bool(failed))
    if failed:
        _include_failed_cases(failed, findings, notes)
        return MdResult("fail", f"{len(failed)} 条用例失败", findings, negative, tuple(title for title, _ in failed))
    if findings:
        return MdResult("fail", f"{len(findings)} 条产品缺陷", findings)
    if blocked or not passed:
        reason = f"{blocked} 条用例未完成" if blocked else "agent 未产出可判定的用例结果"
        return MdResult("blocked", reason, [], negative)
    return MdResult("pass", None, [], negative)
