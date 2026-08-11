from __future__ import annotations

from pydantic import BaseModel, Field


class AcquireRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    lease_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="unspecified", max_length=300)
    ttl_seconds: int | None = Field(default=None, ge=15, le=3600)
    wait_for_ready: bool = True


class TouchRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="unspecified", max_length=300)
    wait_for_ready: bool = True


class ReleaseRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    lease_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="released", max_length=300)


class RenewRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    lease_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="active playback", max_length=300)
    ttl_seconds: int | None = Field(default=None, ge=15, le=3600)
