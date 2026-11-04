from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer


class CreateJob(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    mode: Literal["scripted", "model"] = "scripted"
    demo_fault_injection: bool = False


class JobEvent(BaseModel):
    id: str
    role: str
    message: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class JobDetail(BaseModel):
    id: str
    url: str
    mode: Literal["scripted", "model"]
    status: str
    progress: dict[str, Any]
    generation: int
    events: list[JobEvent]
    report: dict[str, Any] | None
    findings: list[dict[str, Any]]
    error: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()
