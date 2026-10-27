"""Reject obvious execution-action dumps in human-facing imported case steps."""
import re
from app.services.script_targets import VALID_ACTIONS

_PREFIX = re.compile(r'^\s*(?:\d+[.、)）．]\s*|[-*•]\s+|第\s*\d+\s*步[：:、.．]?\s*)')
_BARE = {a.casefold() for a in VALID_ACTIONS} | {
    'goto', 'judge', '点击', '单击', '双击', '输入', '等待', '连接', '按键',
    '断言', '操作', 'click()', 'press()', '待补充', '...', '…',
}


def validate_case_steps(value, title=''):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{title}: 步骤须写明操作对象、操作及检查点')
    for number, line in enumerate(value.splitlines(), 1):
        if not line.strip():
            continue
        content = _PREFIX.sub('', line, count=1).strip().rstrip('。.;；:：').strip()
        if not content or content.casefold() in _BARE:
            raise ValueError(f'{title}: 步骤第 {number} 行缺少可读说明；请写明操作对象和检查点，不能仅填动作名或空序号')
