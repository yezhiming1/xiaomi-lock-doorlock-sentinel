from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import update

from .models import WebSession, utcnow
from .web_common import AuthContext, runtime_from, writable

router = APIRouter(prefix="/api/session", tags=["session"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _set_cookie(response: Response, request: Request, token: str) -> None:
    settings = runtime_from(request).settings
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )


@router.get("")
def status(request: Request) -> dict[str, object]:
    runtime = runtime_from(request)
    database_session = runtime.database.session_factory()
    try:
        try:
            row = runtime.security.session_from_request(database_session, request)
        except HTTPException:
            database_session.rollback()
            return {"authenticated": False}
        csrf = runtime.security.issue_csrf(row)
        database_session.commit()
        return {
            "authenticated": True,
            "csrf_token": csrf,
            "expires_at": row.expires_at.isoformat(),
        }
    finally:
        database_session.close()


@router.post("/login")
def login(request: Request, response: Response, body: LoginRequest) -> dict[str, object]:
    runtime = runtime_from(request)
    database_session = runtime.database.session_factory()
    try:
        try:
            grant = runtime.security.login(database_session, request, body.password)
        except HTTPException:
            # Invalid attempts and the persistent throttle are security records,
            # not a failed application transaction.
            database_session.commit()
            raise
        database_session.commit()
        _set_cookie(response, request, grant.token)
        return {
            "authenticated": True,
            "csrf_token": grant.csrf_token,
            "expires_at": grant.session.expires_at.isoformat(),
        }
    finally:
        database_session.close()


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, bool]:
    context.security.revoke(context.web_session)
    context.security.audit(context.database_session, action="logout")
    response.delete_cookie(
        context.runtime.settings.cookie_name,
        path="/",
        httponly=True,
        secure=context.runtime.settings.environment == "production",
        samesite="strict",
    )
    return {"ok": True}


@router.post("/revoke-all")
def revoke_all(
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, int]:
    result = context.database_session.execute(
        update(WebSession)
        .where(WebSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    count = int(result.rowcount or 0)
    context.security.audit(
        context.database_session,
        action="sessions.revoke_all",
        details={"count": count},
    )
    return {"revoked": count}
