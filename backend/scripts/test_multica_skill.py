"""验证 multica-add-task CLI 参数与模板,不实际创建任务。"""
import json
from types import SimpleNamespace
from unittest.mock import patch
from app.services import multica


def main():
    run = SimpleNamespace(id=555, project_id=1, share_link='https://example.com/share/555',
        artifact_share_link=None, session_id='s', verdict='pass', verdict_reason='结果正确，文件路径需重试',
        payload=json.dumps({'prompt': '读取 C:\\new\\报告.csv\n保留原文'}))
    query = SimpleNamespace(prompt='旧提问', expected='输出完整报表')
    with patch.object(multica.settings, 'MULTICA_MODE', 'skill'), \
         patch.object(multica.shutil, 'which', return_value='C:/tools/multica.exe'), \
         patch.object(multica.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"id":"issue-123","identifier":"QA-1"}', stderr='')) as call:
        multica.check_skill_ready()
        assert multica.push_abnormal_run(run, query) == 'issue-123'
        assert call.call_args_list[0].args[0][1:] == ['auth', 'status']
        assert call.call_args_list[1].args[0][1:] == ['workspace', 'list']
        command = call.call_args.args[0]
        assert command == ['C:/tools/multica.exe', 'issue', 'create', '--title',
            '【测评反馈】结果正确，文件路径需重试', '--project',
            'fe648247-d5b5-43bb-876e-e31afa63d2a6', '--description-stdin', '--output', 'json']
        assert call.call_args.kwargs['input'] == (
            '1.【对话分享链接】https://example.com/share/555\n'
            '2.【对话提问的prompt】读取 C:\\new\\报告.csv\n保留原文\n'
            '3.【对话预期expected】输出完整报表')
        assert not call.call_args.kwargs.get('shell', False)
        run.payload = json.dumps({'expected': '执行时预期'})
        assert multica._payload(run, query)['expected'] == '执行时预期'
        assert multica._payload(run, query)['prompt'] == '旧提问'
        for output in ('not-json', '{}', '{"identifier":"QA-1"}'):
            call.return_value = SimpleNamespace(returncode=0, stdout=output, stderr='')
            try:
                multica.push_abnormal_run(run, query)
            except ValueError:
                pass
            else:
                raise AssertionError('缺少任务ID不可标记成功')
        call.return_value = SimpleNamespace(returncode=1, stdout='', stderr='not logged in')
        try:
            multica.check_skill_ready()
        except ValueError:
            pass
        else:
            raise AssertionError('未登录应阻断推送')
    print('PASS skill前置检查、固定项目、精确模板、原文stdin、expected回退及错误处理')


if __name__ == '__main__':
    main()
