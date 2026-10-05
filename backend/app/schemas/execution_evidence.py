from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ExecutionEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal['qalab-execution-v1']
    script_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    contract_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    report_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    runtime_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    node_version: str = Field(max_length=64)
    platform: str = Field(max_length=32)
    mode: Literal['script', 'claude_precondition', 'claude_judge', 'claude_fallback', 'codex_precondition', 'codex_judge', 'codex_fallback', 'not_started']
    strict_replay: bool
    reset: bool
    precondition: bool
    precondition_ok: bool | None = None
    fallback_reason: str | None = Field(default=None, max_length=2000)
    repeat_count: int = Field(default=1, ge=1, le=2)
    repeat_report_sha256: list[str] = Field(default_factory=list, max_length=2)


class ExecutionTimings(BaseModel):
    """Optional device-side phases; old reports omit these rather than invent 0s."""
    model_config = ConfigDict(extra='forbid')
    runner_total_ms: int = Field(ge=0, le=2147483647)
    prepare_ms: int | None = Field(default=None, ge=0, le=2147483647)
    reset_ms: int | None = Field(default=None, ge=0, le=2147483647)
    navigation_ms: int | None = Field(default=None, ge=0, le=2147483647)
    script_ms: int | None = Field(default=None, ge=0, le=2147483647)
    agent_ms: int | None = Field(default=None, ge=0, le=2147483647)
    trace_ms: int | None = Field(default=None, ge=0, le=2147483647)
    upload_ms: int | None = Field(default=None, ge=0, le=2147483647)
    evidence_ms: int | None = Field(default=None, ge=0, le=2147483647)
