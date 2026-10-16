"""Platform extension installed into the user-supplied tuitui-daily-report script.

The installer preserves the original generate_report/main functions under
_release_generate_report/_release_main. This extension uses the skill's item
renderer and adds a JSON stdin entry point; it never fetches or sends messages.
"""

PLATFORM_ADAPTER_VERSION = 1
TASK_STATUSES = [
    ('online', '已上线'), ('ready_online', '测试完成'), ('testing', '测试中'),
    ('regressing', '回归中'), ('waiting_fix', '待开发修复'), ('pending', '待跟进'),
    ('unassigned', '待分配'), ('at_risk', '有风险'), ('blocked', '阻塞'),
    ('paused', '暂停'), ('closed', '已关闭'),
]


def _platform_item(task):
    lines = [task['title']]
    fields = [('测试负责人', 'owners'), ('开发负责人', 'developer'),
              ('优先级', 'priority'), ('归属版本', 'target_version'),
              ('当前卡点/进度', 'progress_note'), ('日报反馈', 'daily_feedback'),
              ('风险等级', 'risk_level'), ('预计完成', 'expected_completion'),
              ('下一步动作', 'next_action'), ('动作负责人', 'action_owner'),
              ('最晚反馈时间', 'feedback_due_at'), ('结论/备注', 'conclusion'),
              ('需求文档', 'requirement_url')]
    goodcases = []
    def without_goodcases(value):
        remaining, capture_next = [], False
        for line in str(value).splitlines():
            match = re.match(r'^\s*goodcase\s*[:：]\s*(.*)$', line, re.I)
            if match:
                if match.group(1).strip():
                    goodcases.append(match.group(1).strip())
                capture_next = not match.group(1).strip()
            elif capture_next and line.strip():
                goodcases.append(line.strip())
                capture_next = False
            else:
                remaining.append(line)
        return '\n'.join(remaining).strip()
    for label, key in fields:
        value = task.get(key)
        if key == 'owners':
            value = '、'.join(value or []) or '未指定'
        elif key == 'risk_level':
            value = {'red': '红', 'yellow': '黄', 'green': '绿'}.get(value, '未评估')
        elif key == 'priority' and value:
            value = value.upper()
        if value:
            value = without_goodcases(value)
            if value:
                lines.append(f'{label}：{value}')
    impact = task.get('affects_release')
    lines.append('是否影响发版：' + ('未评估' if impact is None else '是' if impact else '否'))
    description = without_goodcases(task.get('description') or '')
    if description:
        lines.append('任务说明：' + description)
    return {'text': '\n'.join(lines), 'goodcase': list(dict.fromkeys(goodcases))}


def generate_report(date_str=None, version=None, web_items=None, upgrade_items=None, *, platform=None):
    if platform is None:
        return _release_generate_report(date_str, version, web_items or [], upgrade_items or [])
    tasks = platform['tasks']
    known = dict(TASK_STATUSES)
    if any(t['status'] not in known for t in tasks):
        raise ValueError('存在不支持的任务状态')
    title = f"{platform['project_name']} · {platform['report_date']} 测试日报"
    done = sum(t['status'] in ('online', 'ready_online') for t in tasks)
    testing = sum(t['status'] in ('testing', 'regressing') for t in tasks)
    risk = sum(t.get('risk_level') in ('red', 'yellow') or t['status'] in ('at_risk', 'blocked')
               or t.get('affects_release') is True for t in tasks)
    lines = [f'**{title}**', '', '**今日提测概览**',
             f'共 {len(tasks)} 个功能：测试完成/已上线 {done} 个，测试中/回归中 {testing} 个，风险/阻塞 {risk} 个。']
    for status, label in TASK_STATUSES:
        group = [t for t in tasks if t['status'] == status]
        if not group:
            continue
        lines.extend(['', f'**{label}（{len(group)} 项）**'])
        for index, task in enumerate(group, 1):
            text, goodcases = _split_item(_platform_item(task))
            lines.append(f"{index}. " + text.replace('\n', '\n   '))
            lines.extend(f'   goodcase：{item}' for item in goodcases)
    return '\n'.join(lines)


def main():
    if sys.argv[1:] != ['--tasks-json']:
        return _release_main()
    platform = json.load(sys.stdin)
    content = generate_report(platform=platform)
    print(json.dumps({'title': f"{platform['project_name']} · {platform['report_date']} 测试日报",
                      'content': content}, ensure_ascii=False))
