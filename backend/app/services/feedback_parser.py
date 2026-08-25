"""反馈测试 md 结构化解析器（纯函数，不依赖 DB，可脱机验证）。

机器人产出的用例 md 是固定模板：
  # 测试用例 · <需求名> · [编号] · V1.0
  ## 需求信息            —— 需求链接
  ## / ### 反馈概述       —— 反馈来源/用户原声/现象（容错 ## vs ###）
  ### 测试点 P-101 <点名>
  | 用例编号 | 优先级 | 本期状态 | 标题 | 前置条件 | 步骤 | 预期结果 | 后续变更 | 类型 | 自动化(可行/优先级/理由) | 自动化状态 | 状态 |
  | C-1 | P1 | 🆕新增 | ... |

鲁棒性要点：**靠表头特征识别用例表格**（含「用例编号」+「预期结果」），
而非无脑解析所有 `|` 行——机器人的运行总结 md 也含表格（概览表），必须跳过不误解析。
"""
from __future__ import annotations

import io
import re
import zipfile

# 一级标题：# 测试用例 · <需求名> · [编号] · V1.0
_H1_RE = re.compile(r"^#\s+(.*)")
_REQ_URL_RE = re.compile(r"需求链接[:：]\s*(\S+)")
# ### 测试点 P-101 <点名>
_POINT_RE = re.compile(r"测试点\s*(P-?\d+)\s*(.*)")


def _split_row(line: str) -> list[str]:
    """md 表格行 → 单元格列表（去行首尾 `|` 与空白）。"""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_case_header(cells: list[str]) -> bool:
    """是否为用例表头行（含「用例编号」+「预期结果」两个特征列）。"""
    joined = "".join(cells)
    return "用例编号" in joined and "预期结果" in joined


def _is_sep_row(line: str) -> bool:
    """md 表格分隔行（|---|---|...）。"""
    s = line.strip().strip("|").replace("|", "").replace("-", "").replace(":", "").strip()
    return s == "" and "-" in line


def _norm_feasible(raw: str) -> tuple[str, str]:
    """「自动化(可行/优先级/理由)」列 `yes / high / 理由` → (feasible, reason)。

    feasible 归一到 yes/partial/no（无法识别 → no）；reason 为剩余段落合并。
    """
    parts = [p.strip() for p in (raw or "").split("/")]
    head = (parts[0] if parts else "").lower()
    feasible = head if head in ("yes", "partial", "no") else "no"
    reason = " / ".join(p for p in parts[1:] if p) or None
    return feasible, reason


def parse_md(text: str) -> dict:
    """解析单个用例 md → {req_title, req_url, feedback_summary, cases: [...]}。

    非用例格式的 md（如机器人运行总结）→ cases 为 []（不抛异常）。
    每个 case: {point_code, point_title, case_no, title, precondition, steps,
               expected, category, priority, auto_feasible, auto_reason, exec_kind}。
    """
    lines = text.splitlines()
    req_title: str | None = None
    req_url: str | None = None
    summary_lines: list[str] = []
    cases: list[dict] = []

    in_summary = False       # 是否在「反馈概述」段内
    in_case_table = False    # 是否在用例表格内（表头之后）
    point_code: str | None = None
    point_title: str | None = None

    for line in lines:
        stripped = line.strip()

        # ---- 标题行（# / ## / ###）----
        if re.match(r"^#{1,6}\s", stripped):
            title_text = re.sub(r"^#{1,6}\s+", "", stripped)
            # 一级标题 → 需求名（· 分段取第 2 段；无 · 用整行去前缀）
            if req_title is None and stripped.startswith("# "):
                segs = [s.strip() for s in title_text.split("·")]
                req_title = segs[1] if len(segs) >= 2 else title_text.replace("测试用例", "").strip()
            # 反馈概述段开始
            in_summary = "反馈概述" in title_text
            # 测试点
            m = _POINT_RE.search(title_text)
            if m:
                point_code = m.group(1)
                point_title = m.group(2).strip() or None
            in_case_table = False  # 任何标题都结束上一个表格
            continue

        # ---- 反馈概述正文收集 ----
        if in_summary:
            if stripped:
                summary_lines.append(stripped)
            continue

        # ---- 需求链接 ----
        if req_url is None:
            mu = _REQ_URL_RE.search(stripped)
            if mu:
                req_url = mu.group(1)

        # ---- 表格 ----
        if stripped.startswith("|"):
            cells = _split_row(stripped)
            if _is_case_header(cells):
                in_case_table = True
                continue
            if _is_sep_row(stripped):
                continue
            if in_case_table:
                # 用例数据行：12 列（列数不足视为脏行跳过）
                if len(cells) < 12:
                    continue
                case_no, priority, _status2, title, precond, steps, expected, \
                    _change, category, auto_col, _auto_status, _status = cells[:12]
                if not title or title in ("-", "—"):
                    continue
                feasible, reason = _norm_feasible(auto_col)
                cases.append({
                    "point_code": point_code,
                    "point_title": point_title,
                    "case_no": case_no or None,
                    "title": title,
                    "precondition": precond or None,
                    "steps": (steps or "").replace("<br>", "\n").replace("<br/>", "\n") or None,
                    "expected": expected or None,
                    "category": category or None,
                    "priority": (priority or "").upper() or None,
                    "auto_feasible": feasible,
                    "auto_reason": reason,
                    "exec_kind": "manual" if feasible == "no" else "gui",
                })
            continue
        else:
            in_case_table = False  # 表格被非表格行打断

    return {
        "req_title": req_title,
        "req_url": req_url,
        "feedback_summary": "\n".join(summary_lines) or None,
        "cases": cases,
    }


def iter_zip(data: bytes) -> list[tuple[str, str]]:
    """解压 zip bytes → [(filename, md_text), ...]，仅 .md 文件。

    文本 utf-8 优先、gbk 回退；跳过目录项与 __MACOSX。
    """
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or name.startswith("__MACOSX") or not name.lower().endswith(".md"):
                continue
            raw = zf.read(info)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            # zip 内路径只留文件名展示
            base = name.replace("\\", "/").split("/")[-1]
            out.append((base, text))
    return out
