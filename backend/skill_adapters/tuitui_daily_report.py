"""Platform extension installed into the user-supplied tuitui-daily-report script.

The installer preserves the original generate_report/main functions under
_release_generate_report/_release_main. This extension uses the skill's item
renderer and adds a JSON stdin entry point; it never fetches or sends messages.
"""

PLATFORM_ADAPTER_VERSION = 2
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


def render_platform_html(report):
    """Render the edited report snapshot using the installed skill's template styles."""
    from html import escape
    from pathlib import Path
    template = (Path(__file__).resolve().parents[1] / 'references/template.html').read_text(encoding='utf-8')
    style = re.search(r'<style[^>]*>(.*?)</style>', template, re.S | re.I)
    if not style:
        raise ValueError('skill HTML 模板缺少样式')

    def inline(value):
        # Task notes are untrusted text. Never render raw HTML, images or unsafe links.
        parts = re.split(r'(https?://[^\s<>"\x27（）【】。，；]+)', value)
        rendered = []
        for part in parts:
            safe = escape(part)
            if re.match(r'^https?://', part, re.I):
                safe = f'<a href="{safe}" target="_blank" rel="noopener noreferrer">{safe}</a>'
            rendered.append(safe)
        return ''.join(rendered)

    groups, current = [], {'heading': '日报内容', 'items': [], 'paragraphs': []}
    groups.append(current)
    for line_no, line in enumerate(report['content'].splitlines()):
        heading = re.fullmatch(r'\*\*(.+)\*\*', line) or re.fullmatch(r'#{1,3} (.+)', line)
        if heading:
            label = heading.group(1)
            if line_no == 0 and (label == report['title'] or label.endswith('测试日报')):
                continue
            current = {'heading': label, 'items': [], 'paragraphs': []}
            groups.append(current)
        elif re.match(r'^\d+\. ', line):
            number, value = line.split('. ', 1)
            current['items'].append([number, [value]])
        elif line.startswith('   ') and current['items']:
            current['items'][-1][1].append(line[3:])
        elif line.strip():
            # Preserve free-form edits, including additions after the generated items.
            if current['items']:
                current['items'][-1][1].append(line)
            else:
                current['paragraphs'].append(line)
    sections, stats = [], []
    colors = {'已上线': 'online', '测试完成': 'online', '测试中': 'testing', '回归中': 'testing',
              '有风险': 'legacy', '阻塞': 'legacy', '待开发修复': 'legacy'}
    status_labels = set(dict(TASK_STATUSES).values())
    for group in groups:
        if not group['items'] and not group['paragraphs']:
            continue
        label = re.sub(r'（\d+ 项）$', '', group['heading'])
        color = colors.get(label, 'pending')
        badge = ''
        if label in status_labels:
            count = len(group['items'])
            badge = f'<span class="badge badge-{color}">{count} 项</span>'
            stats.append(f'<div class="stat-card stat-{color}"><div class="num">{count}</div><div class="label">{escape(label)}</div></div>')
        paragraphs = ''.join('<p>' + inline(line) + '</p>' for line in group['paragraphs'])
        items = ''.join('<li><span class="item-num">' + escape(number) + '.</span><span class="item-text">' +
                        '<br>'.join(inline(line) for line in lines) + '</span></li>' for number, lines in group['items'])
        sections.append(f'<section class="section"><div class="section-header"><h2>{escape(label)}</h2>{badge}</div>{paragraphs}<ol class="item-list">{items}</ol></section>')
    extra_style = "a { color: #1565c0; } .item-text, p { overflow-wrap: anywhere; white-space: pre-wrap; } .item-text { min-width: 0; } .stat-card { min-width: 120px; } @media(max-width:600px) { body { padding:12px; } .section { padding:16px; } .header h1 { font-size:22px; } .stats { gap:8px; } }"
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            '<meta name="robots" content="noindex,nofollow"><title>' + escape(report['title']) + '</title><style>' +
            style.group(1) + extra_style + '</style></head><body><div class="container">'
            '<header class="header"><h1>' + escape(report['title']) + '</h1></header>' +
            '<div class="stats">' + ''.join(stats) + '</div>' + ''.join(sections) +
            '<footer class="footer">测试进度日报 · ' + escape(report['report_date']) + '</footer></div></body></html>')


def main():
    if sys.argv[1:] == ['--html-json']:
        print(json.dumps({'html': render_platform_html(json.load(sys.stdin))}, ensure_ascii=False))
        return
    if sys.argv[1:] != ['--tasks-json']:
        return _release_main()
    platform = json.load(sys.stdin)
    content = generate_report(platform=platform)
    print(json.dumps({'title': f"{platform['project_name']} · {platform['report_date']} 测试日报",
                      'content': content}, ensure_ascii=False))
