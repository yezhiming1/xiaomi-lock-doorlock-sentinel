from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .models import OutboxMessage, OutboxState, utcnow


def enqueue(
    session: Session,
    *,
    topic: str,
    dedupe_key: str,
    payload: dict[str, Any],
    priority: int = 50,
) -> OutboxMessage | None:
    existing = session.scalar(select(OutboxMessage).where(OutboxMessage.dedupe_key == dedupe_key))
    if existing:
        return existing
    message = OutboxMessage(
        topic=topic,
        dedupe_key=dedupe_key,
        payload=payload,
        priority=priority,
        state=OutboxState.PENDING.value,
    )
    try:
        with session.begin_nested():
            session.add(message)
            session.flush()
    except IntegrityError:
        return session.scalar(select(OutboxMessage).where(OutboxMessage.dedupe_key == dedupe_key))
    return message


def claim_messages(
    session: Session, settings: Settings, worker: str, limit: int = 10
) -> list[OutboxMessage]:
    now = utcnow()
    query = (
        select(OutboxMessage)
        .where(
            or_(
                OutboxMessage.state == OutboxState.PENDING.value,
                (OutboxMessage.state == OutboxState.LEASED.value)
                & (OutboxMessage.lease_until < now),
            ),
            OutboxMessage.available_at <= now,
        )
        .order_by(OutboxMessage.priority.desc(), OutboxMessage.created_at.asc())
        .limit(max(1, min(limit, 50)))
    )
    messages = list(session.scalars(query))
    lease_until = now + timedelta(seconds=settings.outbox_lease_seconds)
    for message in messages:
        message.state = OutboxState.LEASED.value
        message.lease_owner = worker
        message.lease_until = lease_until
        message.attempts += 1
    session.flush()
    return messages


def acknowledge(session: Session, message_id: str, worker: str) -> bool:
    message = session.get(OutboxMessage, message_id)
    if not message or message.state == OutboxState.SENT.value:
        return bool(message)
    if message.lease_owner != worker:
        return False
    message.state = OutboxState.SENT.value
    message.sent_at = utcnow()
    message.lease_owner = None
    message.lease_until = None
    message.last_error = None
    return True


def reject(
    session: Session,
    message_id: str,
    worker: str,
    error: str,
    retry_after_seconds: int = 30,
    max_attempts: int = 12,
) -> bool:
    message = session.get(OutboxMessage, message_id)
    if not message or message.lease_owner != worker:
        return False
    message.last_error = error[:4000]
    message.lease_owner = None
    message.lease_until = None
    if message.attempts >= max_attempts:
        message.state = OutboxState.DEAD.value
    else:
        message.state = OutboxState.PENDING.value
        message.available_at = utcnow() + timedelta(seconds=max(1, retry_after_seconds))
    return True
