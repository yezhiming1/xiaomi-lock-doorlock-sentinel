from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AuditLog, LoginThrottle, WebSession, ensure_utc, utcnow


@dataclass(slots=True)
class SessionGrant:
    session: WebSession
    token: str
    csrf_token: str


class SecurityService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.password_hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
        )

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def constant_time_equal(left: str, right: str) -> bool:
        return hmac.compare_digest(left, right)

    def _keyed_hash(self, value: str) -> str:
        return hmac.new(
            self.settings.security_pepper_value.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def client_key(self, request: Request) -> str:
        host = request.client.host if request.client else "unknown"
        agent = request.headers.get("user-agent", "")[:512]
        return self._keyed_hash(f"{host}\n{agent}")

    @staticmethod
    def user_agent_hash(request: Request) -> str:
        return hashlib.sha256(
            request.headers.get("user-agent", "")[:512].encode("utf-8")
        ).hexdigest()

    def _throttle(self, session: Session, key_hash: str) -> LoginThrottle:
        row = session.get(LoginThrottle, key_hash)
        if row is None:
            row = LoginThrottle(key_hash=key_hash)
            session.add(row)
            session.flush()
        return row

    def _check_rate_limit(self, row: LoginThrottle) -> None:
        now = utcnow()
        if row.locked_until and ensure_utc(row.locked_until) > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="尝试次数过多，请稍后再试",
            )
        window_age = (now - ensure_utc(row.window_started_at)).total_seconds()
        if window_age > self.settings.login_window_seconds:
            row.failed_count = 0
            row.window_started_at = now
            row.locked_until = None

    def _record_failure(self, row: LoginThrottle) -> None:
        row.failed_count += 1
        if row.failed_count >= self.settings.login_max_failures:
            row.locked_until = utcnow() + timedelta(
                seconds=self.settings.login_lock_seconds
            )

    def login(self, session: Session, request: Request, password: str) -> SessionGrant:
        if not password or len(password) > 256:
            raise HTTPException(status_code=401, detail="密码不正确")
        key = self.client_key(request)
        throttle = self._throttle(session, key)
        self._check_rate_limit(throttle)
        valid = False
        try:
            valid = self.password_hasher.verify(
                self.settings.password_hash_value,
                password,
            )
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            self._record_failure(throttle)
            session.add(
                AuditLog(
                    actor="anonymous",
                    action="login",
                    outcome="rejected",
                    details_json={"reason": "invalid_credentials"},
                )
            )
            raise HTTPException(status_code=401, detail="密码不正确")
        throttle.failed_count = 0
        throttle.window_started_at = utcnow()
        throttle.locked_until = None
        token = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(32)
        web_session = WebSession(
            token_hash=self._sha256(token),
            csrf_token_hash=self._sha256(csrf),
            user_agent_hash=self.user_agent_hash(request),
            expires_at=utcnow() + timedelta(hours=self.settings.session_hours),
        )
        session.add(web_session)
        session.flush()
        session.add(
            AuditLog(
                actor="owner",
                action="login",
                outcome="success",
                details_json={"session_id": web_session.id},
            )
        )
        return SessionGrant(web_session, token, csrf)

    def session_from_request(
        self,
        session: Session,
        request: Request,
        *,
        touch: bool = True,
    ) -> WebSession:
        token = request.cookies.get(self.settings.cookie_name, "")
        if not token:
            raise HTTPException(status_code=401, detail="请先登录")
        row = session.scalar(
            select(WebSession).where(WebSession.token_hash == self._sha256(token))
        )
        now = utcnow()
        if (
            row is None
            or row.revoked_at is not None
            or ensure_utc(row.expires_at) <= now
        ):
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        if row.user_agent_hash and not hmac.compare_digest(
            row.user_agent_hash,
            self.user_agent_hash(request),
        ):
            raise HTTPException(status_code=401, detail="登录环境已变化，请重新登录")
        if touch:
            row.last_seen_at = now
        return row

    def issue_csrf(self, row: WebSession) -> str:
        value = secrets.token_urlsafe(32)
        row.csrf_token_hash = self._sha256(value)
        return value

    def require_csrf(
        self,
        row: WebSession,
        request: Request,
        supplied: str | None,
    ) -> None:
        if not row.csrf_token_hash or not supplied:
            raise HTTPException(status_code=403, detail="页面安全校验已过期，请刷新")
        if not hmac.compare_digest(row.csrf_token_hash, self._sha256(supplied)):
            raise HTTPException(status_code=403, detail="页面安全校验失败，请刷新")
        origin = request.headers.get("origin")
        if origin:
            expected = urlparse(self.settings.public_base_url)
            actual = urlparse(origin)
            if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                raise HTTPException(status_code=403, detail="请求来源不受信任")

    def revoke(self, row: WebSession) -> None:
        row.revoked_at = utcnow()

    @staticmethod
    def audit(
        session: Session,
        *,
        action: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        outcome: str = "success",
        request_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        session.add(
            AuditLog(
                actor="owner",
                action=action,
                subject_type=subject_type,
                subject_id=subject_id,
                outcome=outcome,
                request_id=request_id,
                details_json=details or {},
            )
        )

    @staticmethod
    def prune_sessions(session: Session) -> int:
        result = session.execute(
            delete(WebSession).where(WebSession.expires_at < utcnow())
        )
        return int(result.rowcount or 0)
