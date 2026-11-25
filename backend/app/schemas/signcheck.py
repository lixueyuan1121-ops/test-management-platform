"""签名校验模块入参 schema。"""
from pydantic import BaseModel, Field


class SignCheckResultIn(BaseModel):
    """校验机 agent 回传一次校验的原始结果（POST /api/signcheck/agent/{id}/result）。

    stdout/stderr 传 base64 原始字节：原程序按系统 ANSI 代码页(GBK)输出中文，由后端统一解码，
    避免 agent 侧运行时缺 GBK 解码表时把中文弄丢。
    """
    agent: str | None = Field(None, max_length=64)
    exit_code: int | None = None
    stdout_b64: str | None = None
    stderr_b64: str | None = None
    duration_ms: int | None = None
    error: str | None = Field(None, max_length=2000)   # agent 自身失败（下载失败/启动失败/超时），有它即判 failed
