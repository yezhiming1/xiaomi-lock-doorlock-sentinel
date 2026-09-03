from __future__ import annotations

from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    CannotLink,
    Event,
    FacePrototype,
    FaceTrack,
    ManualOperation,
    Person,
    PersonIndex,
    PersonObservation,
    UnknownCluster,
    UnknownClusterMember,
    utcnow,
)
from .recognition import (
    IdentityMatcher,
    UnknownClusterer,
    admit_prototype,
    rebuild_person_index,
)
from .vector import pack_vector, unpack_vector

RELATIONSHIP_LABELS = {
    "self": "我",
    "family": "家人",
    "friend": "朋友",
    "neighbor": "邻居",
    "courier": "快递员",
    "cleaner": "保洁",
    "visitor": "访客",
    "stranger": "陌生人",
    "other": "其他",
}
RELATIONSHIPS = frozenset(RELATIONSHIP_LABELS)


def _automatic_display_name(
    session: Session,
    relationship: str,
    *,
    current_person: Person | None = None,
) -> str:
    label = RELATIONSHIP_LABELS[relationship]
    prefix = f"{label} "
    if (
        current_person is not None
        and current_person.relationship == relationship
        and current_person.display_name.startswith(prefix)
    ):
        suffix = current_person.display_name.removeprefix(prefix)
        if suffix.isascii() and suffix.isdigit() and int(suffix) >= 1:
            return current_person.display_name

    maximum = 0
    for existing_name in session.scalars(
        select(Person.display_name).where(Person.relationship == relationship)
    ):
        if not existing_name.startswith(prefix):
            continue
        suffix = existing_name.removeprefix(prefix)
        if suffix.isascii() and suffix.isdigit() and int(suffix) >= 1:
            maximum = max(maximum, int(suffix))
    return f"{prefix}{maximum + 1}"


def _resolve_display_name(
    session: Session,
    display_name: str,
    relationship: str,
    *,
    current_person: Person | None = None,
) -> str:
    name = display_name.strip()
    if name:
        if len(name) > 128:
            raise ValueError("人物名称长度不能超过 128 个字符")
        return name
    return _automatic_display_name(
        session,
        relationship,
        current_person=current_person,
    )


def _has_cannot_link(
    session: Session,
    left_track_ids: set[str],
    right_track_ids: set[str],
) -> bool:
    if not left_track_ids or not right_track_ids:
        return False
    return bool(
        session.scalar(
            select(CannotLink.id).where(
                or_(
                    (
                        CannotLink.left_track_id.in_(left_track_ids)
                        & CannotLink.right_track_id.in_(right_track_ids)
                    ),
                    (
                        CannotLink.left_track_id.in_(right_track_ids)
                        & CannotLink.right_track_id.in_(left_track_ids)
                    ),
                )
            )
        )
    )


def _existing_result(session: Session, idempotency_key: str) -> dict[str, Any] | None:
    operation = session.scalar(
        select(ManualOperation).where(ManualOperation.idempotency_key == idempotency_key)
    )
    return operation.after_json if operation else None


def _record(
    session: Session,
    *,
    idempotency_key: str,
    operation: str,
    subject_type: str,
    subject_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    undo_operation_id: str | None = None,
) -> ManualOperation:
    row = ManualOperation(
        idempotency_key=idempotency_key,
        operation=operation,
        subject_type=subject_type,
        subject_id=subject_id,
        actor="owner",
        before_json=before,
        after_json=after,
        undo_operation_id=undo_operation_id,
    )
    session.add(row)
    session.flush()
    return row


def _cluster_members(
    session: Session,
    cluster_id: str,
) -> list[tuple[UnknownClusterMember, FaceTrack]]:
    return list(
        session.execute(
            select(UnknownClusterMember, FaceTrack)
            .join(FaceTrack, UnknownClusterMember.track_id == FaceTrack.id)
            .where(UnknownClusterMember.cluster_id == cluster_id)
            .order_by(UnknownClusterMember.created_at.asc())
        ).all()
    )


def _refresh_person(session: Session, person: Person) -> None:
    observations = list(
        session.scalars(select(PersonObservation).where(PersonObservation.person_id == person.id))
    )
    person.matched_events = len(observations)
    person.distinct_days = len({item.event_day for item in observations})
    if not observations:
        person.first_seen = None
        person.last_seen = None
        return
    times = [
        value
        for value in session.scalars(
            select(Event.occurred_at).where(Event.id.in_([item.event_id for item in observations]))
        )
    ]
    person.first_seen = min(times) if times else None
    person.last_seen = max(times) if times else None


def label_cluster(
    session: Session,
    settings: Settings,
    *,
    cluster_id: str,
    display_name: str,
    relationship: str,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    if relationship not in RELATIONSHIPS:
        raise ValueError("不支持的人物关系")
    name = _resolve_display_name(session, display_name, relationship)
    cluster = session.get(UnknownCluster, cluster_id)
    if not cluster or cluster.status not in {"candidate", "review_ready"}:
        raise ValueError("未知人物簇不存在或不可标记")
    members = _cluster_members(session, cluster.id)
    if not members:
        raise ValueError("未知人物簇没有可用样本")
    before = {
        "cluster_status": cluster.status,
        "cluster_version": cluster.version,
        "track_ids": [track.id for _member, track in members],
    }
    person = Person(
        display_name=name,
        relationship=relationship,
        status="provisional",
    )
    session.add(person)
    session.flush()
    matcher = IdentityMatcher(settings)
    prototype_ids: list[str] = []
    for _member, track in members:
        track.person_id = person.id
        track.decision = "known"
        track.decision_reason = "manual_cluster_label"
        event = session.get(Event, track.event_id)
        if event:
            matcher.observe_person(session, person, event, track, None)
        prototype = admit_prototype(
            session,
            settings,
            person,
            track,
            reason="manual_cluster_label",
        )
        if prototype:
            prototype_ids.append(prototype.id)
            track.representative = True
    cluster.status = "labeled"
    cluster.labeled_person_id = person.id
    cluster.version += 1
    result = {
        "status": "labeled",
        "cluster_id": cluster.id,
        "person_id": person.id,
        "display_name": person.display_name,
        "relationship": person.relationship,
        "prototype_count": len(prototype_ids),
        "prototype_ids": prototype_ids,
    }
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="label_cluster",
        subject_type="cluster",
        subject_id=cluster.id,
        before=before,
        after=result,
    )
    return result


def assign_cluster_to_person(
    session: Session,
    settings: Settings,
    *,
    cluster_id: str,
    target_person_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    cluster = session.get(UnknownCluster, cluster_id)
    if not cluster or cluster.status not in {"candidate", "review_ready"}:
        raise ValueError("未知人物簇不存在或不可合并")
    person = session.get(Person, target_person_id)
    if not person or person.status in {"merged", "deleted"}:
        raise ValueError("目标人物不存在或不可合并")
    members = _cluster_members(session, cluster.id)
    if not members:
        raise ValueError("未知人物簇没有可用样本")

    source_track_ids = {track.id for _member, track in members}
    target_track_ids = set(
        session.scalars(select(FaceTrack.id).where(FaceTrack.person_id == person.id))
    )
    if _has_cannot_link(session, source_track_ids, target_track_ids):
        raise ValueError("该人物与人物簇包含同一画面中的不同人物，不能合并")

    before = {
        "cluster_status": cluster.status,
        "cluster_version": cluster.version,
        "cluster_labeled_person_id": cluster.labeled_person_id,
        "person_status": person.status,
        "track_states": [
            {
                "id": track.id,
                "person_id": track.person_id,
                "decision": track.decision,
                "decision_reason": track.decision_reason,
                "representative": track.representative,
            }
            for _member, track in members
        ],
    }
    matcher = IdentityMatcher(settings)
    existing_prototype_ids = set(
        session.scalars(select(FacePrototype.id).where(FacePrototype.person_id == person.id))
    )
    created_observation_ids: set[str] = set()
    created_prototype_ids: set[str] = set()
    created_model_ids: set[str] = set()
    for _member, track in members:
        existing_observation_id = session.scalar(
            select(PersonObservation.id).where(
                PersonObservation.person_id == person.id,
                PersonObservation.event_id == track.event_id,
            )
        )
        track.person_id = person.id
        track.decision = "known"
        track.decision_reason = "manual_cluster_assignment"
        event = session.get(Event, track.event_id)
        if event:
            matcher.observe_person(session, person, event, track, None)
            if existing_observation_id is None:
                observation_id = session.scalar(
                    select(PersonObservation.id).where(
                        PersonObservation.person_id == person.id,
                        PersonObservation.event_id == event.id,
                    )
                )
                if observation_id:
                    created_observation_ids.add(observation_id)
        prototype = admit_prototype(
            session,
            settings,
            person,
            track,
            reason="manual_cluster_assignment",
        )
        if prototype and prototype.id not in existing_prototype_ids:
            created_prototype_ids.add(prototype.id)
            created_model_ids.add(prototype.model_id)
            track.representative = True

    _refresh_person(session, person)
    cluster.status = "labeled"
    cluster.labeled_person_id = person.id
    cluster.version += 1
    before["created_observation_ids"] = sorted(created_observation_ids)
    before["created_prototype_ids"] = sorted(created_prototype_ids)
    before["created_model_ids"] = sorted(created_model_ids)
    result = {
        "status": "assigned",
        "cluster_id": cluster.id,
        "target_person_id": person.id,
        "display_name": person.display_name,
        "prototype_count": len(created_prototype_ids),
    }
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="assign_cluster_to_person",
        subject_type="cluster",
        subject_id=cluster.id,
        before=before,
        after=result,
    )
    return result


def rename_person(
    session: Session,
    *,
    person_id: str,
    display_name: str,
    relationship: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    person = session.get(Person, person_id)
    if not person or person.status == "merged":
        raise ValueError("人物不存在或已合并")
    if relationship is not None and relationship not in RELATIONSHIPS:
        raise ValueError("不支持的人物关系")
    resolved_relationship = relationship or person.relationship
    name = _resolve_display_name(
        session,
        display_name,
        resolved_relationship,
        current_person=person,
    )
    before = {
        "display_name": person.display_name,
        "relationship": person.relationship,
    }
    person.display_name = name
    if relationship is not None:
        person.relationship = relationship
    result = {
        "status": "updated",
        "person_id": person.id,
        "display_name": person.display_name,
        "relationship": person.relationship,
    }
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="rename_person",
        subject_type="person",
        subject_id=person.id,
        before=before,
        after=result,
    )
    return result


def merge_people(
    session: Session,
    settings: Settings,
    *,
    source_person_id: str,
    target_person_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    if source_person_id == target_person_id:
        raise ValueError("不能把人物合并到自身")
    source = session.get(Person, source_person_id)
    target = session.get(Person, target_person_id)
    if not source or not target or source.status == "merged" or target.status == "merged":
        raise ValueError("源人物或目标人物不可合并")
    source_tracks = list(session.scalars(select(FaceTrack).where(FaceTrack.person_id == source.id)))
    target_track_ids = set(
        session.scalars(select(FaceTrack.id).where(FaceTrack.person_id == target.id))
    )
    if _has_cannot_link(
        session,
        {track.id for track in source_tracks},
        target_track_ids,
    ):
        raise ValueError("两个人物曾在同一画面中出现，不能合并")
    moved_track_ids = [track.id for track in source_tracks]
    for track in source_tracks:
        track.person_id = target.id

    prototypes = list(
        session.scalars(select(FacePrototype).where(FacePrototype.person_id == source.id))
    )
    prototype_ids = [prototype.id for prototype in prototypes]
    model_ids = {prototype.model_id for prototype in prototypes}
    for prototype in prototypes:
        prototype.person_id = target.id

    target_observation_events = set(
        session.scalars(
            select(PersonObservation.event_id).where(PersonObservation.person_id == target.id)
        )
    )
    moved_observation_ids: list[str] = []
    deleted_observations: list[dict[str, Any]] = []
    for observation in list(
        session.scalars(select(PersonObservation).where(PersonObservation.person_id == source.id))
    ):
        if observation.event_id in target_observation_events:
            deleted_observations.append(
                {
                    "id": observation.id,
                    "event_id": observation.event_id,
                    "event_day": observation.event_day,
                    "source_track_id": observation.source_track_id,
                    "similarity": observation.similarity,
                }
            )
            session.delete(observation)
        else:
            observation.person_id = target.id
            moved_observation_ids.append(observation.id)
    before = {
        "source_status": source.status,
        "source_merged_into_id": source.merged_into_id,
        "moved_track_ids": moved_track_ids,
        "prototype_ids": prototype_ids,
        "moved_observation_ids": moved_observation_ids,
        "deleted_observations": deleted_observations,
    }
    source.status = "merged"
    source.merged_into_id = target.id
    _refresh_person(session, source)
    _refresh_person(session, target)
    session.execute(delete(PersonIndex).where(PersonIndex.person_id == source.id))
    for model_id in model_ids:
        rebuild_person_index(session, settings, target.id, model_id)
    result = {
        "status": "merged",
        "source_person_id": source.id,
        "target_person_id": target.id,
        "moved_tracks": len(moved_track_ids),
    }
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="merge_people",
        subject_type="person",
        subject_id=source.id,
        before=before,
        after=result,
    )
    return result


def merge_clusters(
    session: Session,
    settings: Settings,
    *,
    source_cluster_id: str,
    target_cluster_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    if source_cluster_id == target_cluster_id:
        raise ValueError("不能把人物簇合并到自身")
    source = session.get(UnknownCluster, source_cluster_id)
    target = session.get(UnknownCluster, target_cluster_id)
    if not source or not target:
        raise ValueError("源人物簇或目标人物簇不存在")
    if source.model_id != target.model_id:
        raise ValueError("不同模型版本的人物簇不能合并")
    source_members = _cluster_members(session, source.id)
    target_track_ids = {track.id for _member, track in _cluster_members(session, target.id)}
    if _has_cannot_link(
        session,
        {track.id for _member, track in source_members},
        target_track_ids,
    ):
        raise ValueError("两个人物簇包含同一画面中的不同人物，不能合并")
    member_ids: list[str] = []
    track_ids: list[str] = []
    for member, track in source_members:
        member.cluster_id = target.id
        track.unknown_cluster_id = target.id
        member_ids.append(member.id)
        track_ids.append(track.id)
    before = {
        "source_status": source.status,
        "source_merged_into_id": source.merged_into_id,
        "member_ids": member_ids,
        "track_ids": track_ids,
    }
    source.status = "merged"
    source.merged_into_id = target.id
    source.version += 1
    UnknownClusterer(settings).refresh(session, target)
    result = {
        "status": "merged",
        "source_cluster_id": source.id,
        "target_cluster_id": target.id,
        "moved_tracks": len(track_ids),
    }
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="merge_clusters",
        subject_type="cluster",
        subject_id=source.id,
        before=before,
        after=result,
    )
    return result


def split_cluster(
    session: Session,
    settings: Settings,
    *,
    cluster_id: str,
    track_ids: list[str],
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    cluster = session.get(UnknownCluster, cluster_id)
    if not cluster or cluster.status not in {"candidate", "review_ready"}:
        raise ValueError("人物簇不存在或不可拆分")
    unique_ids = list(dict.fromkeys(track_ids))
    members = _cluster_members(session, cluster.id)
    member_by_track = {track.id: (member, track) for member, track in members}
    if not unique_ids or any(track_id not in member_by_track for track_id in unique_ids):
        raise ValueError("拆分轨迹不属于该人物簇")
    if len(unique_ids) >= len(members):
        raise ValueError("拆分后原人物簇必须至少保留一条轨迹")
    first_track = member_by_track[unique_ids[0]][1]
    new_cluster = UnknownCluster(
        model_id=cluster.model_id,
        centroid=pack_vector(unpack_vector(first_track.embedding, first_track.embedding_dimension)),
        embedding_dimension=first_track.embedding_dimension,
        first_seen=cluster.first_seen,
        last_seen=cluster.last_seen,
    )
    session.add(new_cluster)
    session.flush()
    for track_id in unique_ids:
        member, track = member_by_track[track_id]
        member.cluster_id = new_cluster.id
        track.unknown_cluster_id = new_cluster.id
    clusterer = UnknownClusterer(settings)
    clusterer.refresh(session, cluster)
    clusterer.refresh(session, new_cluster)
    result = {
        "status": "split",
        "source_cluster_id": cluster.id,
        "new_cluster_id": new_cluster.id,
        "moved_track_ids": unique_ids,
    }
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="split_cluster",
        subject_type="cluster",
        subject_id=cluster.id,
        before={"source_version": cluster.version - 1},
        after=result,
    )
    return result


def mark_cluster_false_positive(
    session: Session,
    *,
    cluster_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    cluster = session.get(UnknownCluster, cluster_id)
    if not cluster:
        raise ValueError("人物簇不存在")
    before = {"status": cluster.status, "version": cluster.version}
    cluster.status = "false_positive"
    cluster.version += 1
    result = {"status": "false_positive", "cluster_id": cluster.id}
    _record(
        session,
        idempotency_key=idempotency_key,
        operation="cluster_false_positive",
        subject_type="cluster",
        subject_id=cluster.id,
        before=before,
        after=result,
    )
    return result


def undo_operation(
    session: Session,
    settings: Settings,
    *,
    operation_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    replay = _existing_result(session, idempotency_key)
    if replay is not None:
        return replay
    original = session.get(ManualOperation, operation_id)
    if not original or original.undone_at is not None:
        raise ValueError("操作不存在或已经撤销")
    before = original.before_json
    after = original.after_json
    if original.operation == "rename_person":
        person = session.get(Person, original.subject_id)
        if not person:
            raise ValueError("人物已不存在，不能撤销")
        person.display_name = before["display_name"]
        person.relationship = before["relationship"]
    elif original.operation == "label_cluster":
        cluster = session.get(UnknownCluster, original.subject_id)
        person = session.get(Person, after["person_id"])
        if not cluster or not person or person.status == "merged":
            raise ValueError("标记结果已经发生后续变化，不能安全撤销")
        track_ids = before["track_ids"]
        tracks = list(session.scalars(select(FaceTrack).where(FaceTrack.id.in_(track_ids))))
        if any(track.person_id != person.id for track in tracks):
            raise ValueError("人物轨迹已经发生后续变化，不能安全撤销")
        for track in tracks:
            track.person_id = None
            track.decision = "unknown"
            track.decision_reason = "manual_label_undone"
            track.representative = False
        session.execute(delete(PersonObservation).where(PersonObservation.person_id == person.id))
        session.execute(delete(FacePrototype).where(FacePrototype.person_id == person.id))
        session.execute(delete(PersonIndex).where(PersonIndex.person_id == person.id))
        cluster.status = before["cluster_status"]
        cluster.labeled_person_id = None
        cluster.version += 1
        session.delete(person)
    elif original.operation == "assign_cluster_to_person":
        cluster = session.get(UnknownCluster, original.subject_id)
        person = session.get(Person, after["target_person_id"])
        track_states = before["track_states"]
        tracks = {
            track.id: track
            for track in session.scalars(
                select(FaceTrack).where(FaceTrack.id.in_([item["id"] for item in track_states]))
            )
        }
        if (
            not cluster
            or not person
            or person.status in {"merged", "deleted"}
            or cluster.labeled_person_id != person.id
            or len(tracks) != len(track_states)
            or any(track.person_id != person.id for track in tracks.values())
        ):
            raise ValueError("人物合并结果已经发生后续变化，不能安全撤销")
        created_observation_ids = before["created_observation_ids"]
        if created_observation_ids:
            session.execute(
                delete(PersonObservation).where(PersonObservation.id.in_(created_observation_ids))
            )
        created_prototype_ids = before["created_prototype_ids"]
        if created_prototype_ids:
            session.execute(
                delete(FacePrototype).where(FacePrototype.id.in_(created_prototype_ids))
            )
        for item in track_states:
            track = tracks[item["id"]]
            track.person_id = item["person_id"]
            track.decision = item["decision"]
            track.decision_reason = item["decision_reason"]
            track.representative = item["representative"]
        cluster.status = before["cluster_status"]
        cluster.labeled_person_id = before["cluster_labeled_person_id"]
        cluster.version += 1
        session.flush()
        person.status = before["person_status"]
        _refresh_person(session, person)
        for model_id in before["created_model_ids"]:
            rebuild_person_index(session, settings, person.id, model_id)
    elif original.operation == "merge_people":
        source = session.get(Person, after["source_person_id"])
        target = session.get(Person, after["target_person_id"])
        if not source or not target or source.merged_into_id != target.id:
            raise ValueError("人物合并结果已经变化，不能安全撤销")
        for track in session.scalars(
            select(FaceTrack).where(FaceTrack.id.in_(before["moved_track_ids"]))
        ):
            track.person_id = source.id
        model_ids: set[str] = set()
        for prototype in session.scalars(
            select(FacePrototype).where(FacePrototype.id.in_(before["prototype_ids"]))
        ):
            prototype.person_id = source.id
            model_ids.add(prototype.model_id)
        for observation in session.scalars(
            select(PersonObservation).where(
                PersonObservation.id.in_(before["moved_observation_ids"])
            )
        ):
            observation.person_id = source.id
        for item in before["deleted_observations"]:
            session.add(
                PersonObservation(
                    id=item["id"],
                    person_id=source.id,
                    event_id=item["event_id"],
                    event_day=item["event_day"],
                    source_track_id=item["source_track_id"],
                    similarity=item["similarity"],
                )
            )
        source.status = before["source_status"]
        source.merged_into_id = before["source_merged_into_id"]
        _refresh_person(session, source)
        _refresh_person(session, target)
        for model_id in model_ids:
            rebuild_person_index(session, settings, source.id, model_id)
            rebuild_person_index(session, settings, target.id, model_id)
    elif original.operation == "merge_clusters":
        source = session.get(UnknownCluster, after["source_cluster_id"])
        target = session.get(UnknownCluster, after["target_cluster_id"])
        if not source or not target or source.merged_into_id != target.id:
            raise ValueError("人物簇合并结果已经变化，不能安全撤销")
        member_ids = before["member_ids"]
        for member in session.scalars(
            select(UnknownClusterMember).where(UnknownClusterMember.id.in_(member_ids))
        ):
            member.cluster_id = source.id
        for track in session.scalars(
            select(FaceTrack).where(FaceTrack.id.in_(before["track_ids"]))
        ):
            track.unknown_cluster_id = source.id
        source.status = before["source_status"]
        source.merged_into_id = before["source_merged_into_id"]
        clusterer = UnknownClusterer(settings)
        clusterer.refresh(session, source)
        clusterer.refresh(session, target)
    elif original.operation == "split_cluster":
        source = session.get(UnknownCluster, after["source_cluster_id"])
        created = session.get(UnknownCluster, after["new_cluster_id"])
        if not source or not created:
            raise ValueError("拆分结果已经变化，不能安全撤销")
        track_ids = after["moved_track_ids"]
        for member in session.scalars(
            select(UnknownClusterMember).where(UnknownClusterMember.track_id.in_(track_ids))
        ):
            member.cluster_id = source.id
        for track in session.scalars(select(FaceTrack).where(FaceTrack.id.in_(track_ids))):
            track.unknown_cluster_id = source.id
        session.flush()
        session.delete(created)
        UnknownClusterer(settings).refresh(session, source)
    elif original.operation == "cluster_false_positive":
        cluster = session.get(UnknownCluster, original.subject_id)
        if not cluster or cluster.status != "false_positive":
            raise ValueError("人物簇状态已经变化，不能安全撤销")
        cluster.status = before["status"]
        cluster.version += 1
    else:
        raise ValueError("该操作暂不支持撤销")

    original.undone_at = utcnow()
    result = {
        "status": "undone",
        "operation_id": original.id,
        "operation": original.operation,
    }
    undo = _record(
        session,
        idempotency_key=idempotency_key,
        operation="undo",
        subject_type="operation",
        subject_id=original.id,
        before={"original_after": original.after_json},
        after=result,
        undo_operation_id=original.id,
    )
    return {**result, "undo_id": undo.id}
