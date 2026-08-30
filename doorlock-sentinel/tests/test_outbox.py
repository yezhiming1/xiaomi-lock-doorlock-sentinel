from doorlock_sentinel.outbox import acknowledge, claim_messages, enqueue, reject


def test_outbox_is_deduplicated_and_leased(database, settings):
    with database.session() as session:
        first = enqueue(session, topic="x", dedupe_key="same", payload={"a": 1})
        second = enqueue(session, topic="x", dedupe_key="same", payload={"a": 2})
        assert first is not None
        assert second is not None
        assert first.id == second.id
    with database.session() as session:
        messages = claim_messages(session, settings, "worker-1", 10)
        assert len(messages) == 1
        message_id = messages[0].id
    with database.session() as session:
        assert acknowledge(session, message_id, "worker-2") is False
        assert reject(session, message_id, "worker-1", "temporary", 1) is True
    with database.session() as session:
        message = session.get(type(first), message_id)
        message.available_at = message.created_at
    with database.session() as session:
        messages = claim_messages(session, settings, "worker-1", 10)
        assert len(messages) == 1
        assert acknowledge(session, message_id, "worker-1") is True
