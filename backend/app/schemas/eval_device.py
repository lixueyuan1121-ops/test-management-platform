from pydantic import BaseModel, Field


class EvalDeviceItem(BaseModel):
    vm_id: str = Field(..., max_length=64)
    label: str | None = Field(None, max_length=96)
    name: str | None = Field(None, max_length=128)
    status: str | None = Field(None, max_length=16)
    device_type: int | None = None


class EvalDeviceReportIn(BaseModel):
    runner: str = Field("mac-01", max_length=64)
    devices: list[EvalDeviceItem] = Field(default_factory=list)
