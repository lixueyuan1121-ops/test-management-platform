"""对话测评用例「模板导入」解析器:把 CSV / TSV 模板文本解析成 EvalQuery 行。

单一事实来源(前后端不重复解析):本地粘贴/上传的 CSV、飞书电子表格取回的 TSV
(`# 表名` 前缀 + Tab 分隔)统一走这里。分隔符按表头自适应(含 Tab → TSV,否则 CSV),
表头列名认中英文别名,维度值认 key 或中文标签。缺 标题/提问 的行整行跳过并记录行号。

失败(表头缺必需列)抛 ValueError,由路由转 400 友好提示。
"""
import csv
import io

from app.services.claude_runner import EVAL_DIMENSIONS, EVAL_DIM_LABELS

# 表头列名 → 规范字段。英文比对时转小写;中文精确匹配。
_HEADER_ALIASES = {
    "title": {"标题", "题目", "名称", "title"},
    "dimension": {"维度", "dimension", "dim"},
    "prompt": {"提问prompt", "提问", "提问内容", "问题", "prompt", "query"},
    "expected": {"预期expected", "预期", "预期结果", "期望", "expected"},
    "conversation_group": {"对话组", "会话组", "组", "conversation_group", "group"},
    "turn_index": {"轮次", "轮", "turn_index", "turn"},
}
# 维度中文标签 → key(反查 EVAL_DIM_LABELS),供模板里填中文
_LABEL_TO_KEY = {label: key for key, label in EVAL_DIM_LABELS.items()}


def _canon_header(cell: str) -> str | None:
    """把一个表头单元格归一到规范字段名;不认识返回 None(该列忽略)。"""
    c = (cell or "").strip()
    if not c:
        return None
    low = c.lower()
    for field, names in _HEADER_ALIASES.items():
        if c in names or low in names:
            return field
    return None


def _norm_dimension(v: str | None) -> str | None:
    """维度值:key 原样、中文标签反查为 key、其余(含空)→ None。"""
    s = (v or "").strip()
    if not s:
        return None
    if s in EVAL_DIMENSIONS:
        return s
    return _LABEL_TO_KEY.get(s)


def _int0(v) -> int:
    try:
        return max(0, int(str(v).strip()))
    except (TypeError, ValueError):
        return 0


def parse_eval_template(text: str) -> tuple[list[dict], list[dict]]:
    """解析模板文本 → (rows, skipped)。

    rows: [{title, prompt, dimension, expected, conversation_group, turn_index}]
    skipped: [{line, reason}]  line 为数据行序号(不含表头,从 1 起)。
    表头缺 标题/提问 列 → 抛 ValueError。
    """
    text = (text or "").lstrip("﻿")  # 去 Excel/飞书 CSV 的 UTF-8 BOM
    # 过滤空行与飞书多表 `# 表名` 前缀行
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise ValueError("模板内容为空,请粘贴含表头的用例表格")

    header_line = lines[0]
    delim = "\t" if "\t" in header_line else ","
    header_cells = next(csv.reader([header_line], delimiter=delim), [])
    # 列序号 → 规范字段
    col_field = {i: f for i, cell in enumerate(header_cells) if (f := _canon_header(cell))}
    fields = set(col_field.values())
    if "title" not in fields or "prompt" not in fields:
        raise ValueError("模板表头缺少必需列:标题、提问prompt(其余列可选)")

    rows: list[dict] = []
    skipped: list[dict] = []
    row_no = 0
    reader = csv.reader(io.StringIO("\n".join(lines[1:])), delimiter=delim)
    for cells in reader:
        # 多表拼接时重复出现的表头行 → 跳过,不当数据、不计序号
        if cells == header_cells:
            continue
        row_no += 1
        rec = {col_field[i]: (cells[i] if i < len(cells) else "") for i in col_field}
        title = (rec.get("title") or "").strip()
        prompt = (rec.get("prompt") or "").strip()
        if not title:
            skipped.append({"line": row_no, "reason": "缺少标题,整行跳过"})
            continue
        if not prompt:
            skipped.append({"line": row_no, "reason": "缺少提问prompt,整行跳过"})
            continue
        rows.append({
            "title": title[:512],
            "prompt": prompt,
            "dimension": _norm_dimension(rec.get("dimension")),
            "expected": (rec.get("expected") or "").strip() or None,
            "conversation_group": (rec.get("conversation_group") or "").strip() or None,
            "turn_index": _int0(rec.get("turn_index")),
        })
    return rows, skipped
