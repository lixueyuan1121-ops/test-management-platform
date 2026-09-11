"""Trace artifact access checks with fake DB and temporary storage only."""
import asyncio
import io
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from app.api import exec_queue as api
from app.api.runner_update import _iter_bundle_files


def trace_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('trace.trace', '{"type":"context-options"}')
    return output.getvalue()


async def main():
    run = SimpleNamespace(id=1, runner='runner-a', runner_device_id=5, project_id=9)
    db = SimpleNamespace(get=lambda model, id: run if id == 1 else None)
    ctx = SimpleNamespace(device=SimpleNamespace(id=5, runner_id='runner-a'))
    with tempfile.TemporaryDirectory() as root, patch.object(api, '_EXEC_TRACE_ROOT', root):
        file = lambda data: UploadFile(filename='trace.zip', file=io.BytesIO(data))
        result = await api.upload_exec_trace(1, file(trace_bytes()), 'ignored', db, ctx)
        assert result['data']['trace_url'] == '/api/exec-queue/1/trace'
        assert Path(root, '1.zip').is_file()
        for data, identity, expected in [
            (b'not zip', ctx, 400),
            (trace_bytes(), SimpleNamespace(device=SimpleNamespace(id=6, runner_id='runner-a')), 403),
            (trace_bytes(), SimpleNamespace(device=None), 403),
        ]:
            try:
                await api.upload_exec_trace(1, file(data), 'runner-a', db, identity)
                raise AssertionError('bad artifact/owner accepted')
            except HTTPException as e:
                assert e.status_code == expected
        with patch.object(api, 'assert_project_role') as access:
            response = api.download_exec_trace(1, db, SimpleNamespace(id=7))
            assert str(response.path) == str(Path(root, '1.zip'))
            assert access.call_args.args[2] == 9
        with patch.object(api, 'assert_project_role', side_effect=HTTPException(403)):
            try:
                api.download_exec_trace(1, db, SimpleNamespace(id=8))
                raise AssertionError('nonmember downloaded trace')
            except HTTPException as e:
                assert e.status_code == 403
    bundle = dict((rp, ap) for ap, rp in _iter_bundle_files())
    assert Path(bundle['gui-mcp/playwright-runtime.mjs']).resolve() == Path('app/services/playwright_runtime.mjs').resolve()
    print('OK trace upload/download: format, device ownership, project authorization, shared runtime bundle')


if __name__ == '__main__':
    asyncio.run(main())
