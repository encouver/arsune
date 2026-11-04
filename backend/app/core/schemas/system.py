from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ConfigResponse(BaseModel):
    modes: list[Literal["scripted", "model"]]
    demo_urls: dict[str, str]
    max_pages: int
    access: str
