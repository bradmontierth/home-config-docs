from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from .lease_manager import LeaseManager, LeaseManagerError
from .models import AcquireRequest, ReleaseRequest, RenewRequest, TouchRequest


router = APIRouter()


def manager(request: Request) -> LeaseManager:
    return request.app.state.manager


def authenticate(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    expected = f"Bearer {request.app.state.config.api_token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def translate_error(exc: LeaseManagerError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(instance: LeaseManager = Depends(manager)) -> dict[str, str]:
    if not instance.initialized or not instance.safety_valid or not instance.ha_ok or not instance.ma_ok:
        raise HTTPException(status_code=503, detail="Initial reconciliation is not healthy")
    return {"status": "ready"}


@router.get("/v1/status", dependencies=[Depends(authenticate)])
async def get_status(instance: LeaseManager = Depends(manager)) -> dict:
    return instance.status()


@router.post("/v1/acquire", dependencies=[Depends(authenticate)])
async def acquire(payload: AcquireRequest, instance: LeaseManager = Depends(manager)) -> dict:
    try:
        return await instance.acquire(
            payload.owner,
            payload.lease_id,
            payload.reason,
            payload.ttl_seconds,
            payload.wait_for_ready,
        )
    except LeaseManagerError as exc:
        raise translate_error(exc) from exc


@router.post("/v1/touch", dependencies=[Depends(authenticate)])
async def touch(payload: TouchRequest, instance: LeaseManager = Depends(manager)) -> dict:
    try:
        return await instance.touch(payload.owner, payload.reason, payload.wait_for_ready)
    except LeaseManagerError as exc:
        raise translate_error(exc) from exc


@router.post("/v1/release", dependencies=[Depends(authenticate)])
async def release(payload: ReleaseRequest, instance: LeaseManager = Depends(manager)) -> dict:
    return await instance.release(payload.owner, payload.lease_id, payload.reason)


@router.post("/v1/renew", dependencies=[Depends(authenticate)])
async def renew(payload: RenewRequest, instance: LeaseManager = Depends(manager)) -> dict:
    return await instance.renew(
        payload.owner, payload.lease_id, payload.reason, payload.ttl_seconds
    )
