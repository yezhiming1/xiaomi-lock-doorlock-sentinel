from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .models import WebSession
from .security import SecurityService


@dataclass(slots=True)
class AuthContext:
    database_session: Session
    web_session: WebSession
    security: SecurityService
    runtime: Any


def runtime_from(request: Request) -> Any:
    return request.app.state.runtime


def authenticated(request: Request):
    runtime = runtime_from(request)
    database_session = runtime.database.session_factory()
    try:
        row = runtime.security.session_from_request(database_session, request)
        yield AuthContext(database_session, row, runtime.security, runtime)
        database_session.commit()
    except Exception:
        database_session.rollback()
        raise
    finally:
        database_session.close()


def writable(
    request: Request,
    context: Annotated[AuthContext, Depends(authenticated)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthContext:
    context.security.require_csrf(context.web_session, request, csrf_token)
    return context


def require_internal_token(
    request: Request,
    supplied: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    runtime = runtime_from(request)
    try:
        expected = runtime.settings.api_secret_value
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="内部服务密钥未配置") from exc
    if not SecurityService.constant_time_equal(supplied or "", expected):
        raise HTTPException(status_code=401, detail="内部服务认证失败")
