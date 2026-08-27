"""runner 自升级分发：版本查询 + zip 包下载。

- GET /api/runner/version  当前可分发的 runner 版本号；runner token。
- GET /api/runner/bundle   实时打包 tools/qalab-runner/ 为 zip 流；runner token。

版本号 = 打包清单内所有文件 (相对路径, mtime_ns, size) 的 sha256 前 8 位:
不需要人工维护版本号,服务器 git pull 后文件一变,版本随之变化。
打包排除运行时本地产物与机器私有配置(.env/node_modules/evidence 等),
也排除启动脚本(run.sh/run.cmd/run-eval.*)——它们由人工分发,用户会在本机
填入真实 RUNNER_TOKEN 等配置,绝不能被升级包的仓库占位版覆盖;
runner 端解压覆盖时同样绝不触碰本机 .env 与 node_modules。
"""
import hashlib
import io
import os
import zipfile

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.responses import StreamingResponse

from app.core.deps import require_runner_ctx
from app.schemas.common import ok

router = APIRouter(prefix="/api/runner", tags=["runner-update"])

# 仓库根 = backend/app/api/runner_update.py 往上四级（api→app→backend→repo_root）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_RUNNER_DIR = os.path.join(_REPO_ROOT, "tools", "qalab-runner")

# 排除规则：目录名（任意层级命中即剪枝）与文件名模式
# eval(对话测评执行器)随包分发——设备切换等能力靠升级包送达执行机;
# 其下机器私有/运行产物目录必须剪掉:accounts(登录态 storageState,覆盖=所有账号掉登录)、
# output/screenshots/logs(运行产物)、test(测试代码,执行机不需要)。
_EXCLUDE_DIRS = {"node_modules", "evidence", "cases", "platform", "__pycache__", ".git", ".remember",
                 "accounts", "output", "screenshots", "logs", "test"}
# 启动脚本走人工分发、.mjs 走升级通道：run.cmd/run.sh 内含用户手工填入的
# RUNNER_TOKEN 等本机配置，若进包会被 Expand-Archive -Force 用占位版覆盖 → 静默 401。
_EXCLUDE_FILES = {
    ".env", ".DS_Store", "selectors.json.bak", ".runner-version",
    "run.sh", "run.cmd", "run-eval.sh", "run-eval.cmd",
}
_EXCLUDE_SUFFIXES = (".test.mjs", ".zip", ".log")


def _ensure_runner_dir() -> None:
    """分发目录不存在时报 503,避免静默返回空哈希/空 zip(docker 只打包 backend/ 时会踩中)。"""
    if not os.path.isdir(_RUNNER_DIR):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="runner 分发目录不存在")


def _iter_bundle_files():
    """走一遍 runner 目录，yield (绝对路径, zip 内相对路径)，按相对路径排序保证哈希稳定。"""
    out = []
    for root, dirs, files in os.walk(_RUNNER_DIR):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            if fn in _EXCLUDE_FILES or fn.endswith(_EXCLUDE_SUFFIXES):
                continue
            ap = os.path.join(root, fn)
            rp = os.path.relpath(ap, _RUNNER_DIR)
            out.append((ap, rp))
    out.sort(key=lambda t: t[1])
    return out


def _bundle_version() -> str:
    """打包清单指纹：所有文件 (相对路径, mtime_ns, size) 聚合 sha256 前 8 位。"""
    h = hashlib.sha256()
    for ap, rp in _iter_bundle_files():
        st = os.stat(ap)
        h.update(f"{rp}|{st.st_mtime_ns}|{st.st_size}\n".encode())
    return h.hexdigest()[:8]


@router.get("/version")
def runner_version(_=Depends(require_runner_ctx)):
    _ensure_runner_dir()
    return ok({"version": _bundle_version()})


@router.get("/bundle")
def runner_bundle(_=Depends(require_runner_ctx)):
    """实时打包 zip 到内存并流式返回。包体量级 ~几百 KB，内存打包足够。"""
    _ensure_runner_dir()
    ver = _bundle_version()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ap, rp in _iter_bundle_files():
            zf.write(ap, rp)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=qalab-runner.zip",
            "X-Bundle-Version": ver,
        },
    )
