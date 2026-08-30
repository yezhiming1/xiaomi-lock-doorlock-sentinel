from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .artifacts import promote_track_artifacts
from .config import Settings
from .models import (
    CannotLink,
    Event,
    FacePrototype,
    FaceTrack,
    Person,
    PersonIndex,
    PersonObservation,
    UnknownCluster,
    UnknownClusterMember,
    ensure_utc,
)
from .vector import cosine_similarity, pack_vector, unpack_vector, weighted_centroid


@dataclass(slots=True)
class MatchDecision:
    decision: str
    person: Person | None
    top_similarity: float | None
    second_similarity: float | None
    reason: str


def rebuild_person_index(
    session: Session,
    settings: Settings,
    person_id: str,
    model_id: str,
) -> PersonIndex | None:
    prototypes = list(
        session.scalars(
            select(FacePrototype)
            .where(
                FacePrototype.person_id == person_id,
                FacePrototype.model_id == model_id,
            )
            .order_by(FacePrototype.quality_score.desc(), FacePrototype.created_at.desc())
        )
    )
    existing = session.get(PersonIndex, (person_id, model_id))
    if not prototypes:
        if existing:
            session.delete(existing)
        return None

    selected: list[FacePrototype] = []
    selected_vectors: list[np.ndarray] = []
    limit = max(1, settings.prototype_search_limit_per_person)
    for prototype in prototypes:
        vector = unpack_vector(prototype.embedding, prototype.embedding_dimension)
        is_diverse = not selected_vectors or all(
            cosine_similarity(vector, candidate)
            < settings.prototype_diversity_similarity
            for candidate in selected_vectors
        )
        if is_diverse and len(selected) < limit:
            selected.append(prototype)
            selected_vectors.append(vector)
    if not selected:
        selected = [prototypes[0]]
        selected_vectors = [
            unpack_vector(prototypes[0].embedding, prototypes[0].embedding_dimension)
        ]
    selected_ids = {prototype.id for prototype in selected}
    for prototype in prototypes:
        prototype.search_enabled = prototype.id in selected_ids

    all_vectors = [
        unpack_vector(prototype.embedding, prototype.embedding_dimension)
        for prototype in prototypes
    ]
    centroid = weighted_centroid(
        all_vectors,
        [max(prototype.quality_score, 0.05) for prototype in prototypes],
    )
    if existing is None:
        existing = PersonIndex(
            person_id=person_id,
            model_id=model_id,
            centroid=pack_vector(centroid),
            embedding_dimension=centroid.size,
            prototype_count=len(prototypes),
            search_prototype_count=len(selected),
        )
        session.add(existing)
    else:
        existing.centroid = pack_vector(centroid)
        existing.embedding_dimension = centroid.size
        existing.prototype_count = len(prototypes)
        existing.search_prototype_count = len(selected)
        existing.version += 1
    session.flush()
    return existing


def admit_prototype(
    session: Session,
    settings: Settings,
    person: Person,
    track: FaceTrack,
    *,
    reason: str,
) -> FacePrototype | None:
    if track.quality_score < settings.prototype_quality_score:
        return None
    existing_source = session.scalar(
        select(FacePrototype).where(
            FacePrototype.person_id == person.id,
            FacePrototype.source_track_id == track.id,
        )
    )
    if existing_source:
        return existing_source
    vector = unpack_vector(track.embedding, track.embedding_dimension)
    existing = list(
        session.scalars(
            select(FacePrototype).where(
                FacePrototype.person_id == person.id,
                FacePrototype.model_id == track.model_id,
            )
        )
    )
    if any(
        cosine_similarity(
            vector,
            unpack_vector(prototype.embedding, prototype.embedding_dimension),
        )
        >= settings.prototype_diversity_similarity
        for prototype in existing
    ):
        return None
    prototype = FacePrototype(
        person_id=person.id,
        source_track_id=track.id,
        model_id=track.model_id,
        embedding=track.embedding,
        embedding_dimension=track.embedding_dimension,
        quality_score=track.quality_score,
        search_enabled=True,
        admitted_reason=reason,
    )
    session.add(prototype)
    session.flush()
    promote_track_artifacts(session, track)
    rebuild_person_index(session, settings, person.id, track.model_id)
    return prototype


class IdentityMatcher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _candidate_people(
        self,
        session: Session,
        embedding: np.ndarray,
        model_id: str,
        excluded_person_ids: set[str],
    ) -> list[Person]:
        rows = session.execute(
            select(PersonIndex, Person)
            .join(Person, PersonIndex.person_id == Person.id)
            .where(
                PersonIndex.model_id == model_id,
                Person.status.not_in(["merged", "deleted"]),
            )
        ).all()
        ranked: list[tuple[float, Person]] = []
        for index, person in rows:
            if person.id in excluded_person_ids:
                continue
            score = cosine_similarity(
                embedding,
                unpack_vector(index.centroid, index.embedding_dimension),
            )
            if score >= self.settings.identity_coarse_similarity:
                ranked.append((score, person))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [person for _score, person in ranked[:4]]

    def match(
        self,
        session: Session,
        embedding: np.ndarray,
        model_id: str,
        excluded_person_ids: set[str] | None = None,
    ) -> MatchDecision:
        excluded = excluded_person_ids or set()
        candidates = self._candidate_people(session, embedding, model_id, excluded)
        candidate_ids = [person.id for person in candidates]
        if not candidate_ids:
            reason = "same_event_person_conflict" if excluded else "no_known_people"
            return MatchDecision("unknown", None, None, None, reason)
        query = (
            select(FacePrototype, Person)
            .join(Person, FacePrototype.person_id == Person.id)
            .where(
                FacePrototype.model_id == model_id,
                FacePrototype.search_enabled.is_(True),
                Person.status.not_in(["merged", "deleted"]),
            )
        )
        query = query.where(Person.id.in_(candidate_ids))
        rows = session.execute(query).all()
        by_person: dict[str, tuple[Person, float]] = {}
        for prototype, person in rows:
            if person.id in excluded:
                continue
            score = cosine_similarity(
                embedding,
                unpack_vector(prototype.embedding, prototype.embedding_dimension),
            )
            current = by_person.get(person.id)
            if current is None or score > current[1]:
                by_person[person.id] = (person, score)
        ranked = sorted(by_person.values(), key=lambda item: item[1], reverse=True)
        if not ranked:
            reason = "same_event_person_conflict" if excluded else "no_known_people"
            return MatchDecision("unknown", None, None, None, reason)
        person, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        if (
            top >= self.settings.identity_accept_similarity
            and top - second >= self.settings.identity_min_margin
        ):
            return MatchDecision(
                "known",
                person,
                top,
                second if second >= 0 else None,
                "score_and_margin_passed",
            )
        return MatchDecision(
            "uncertain",
            None,
            top,
            second if second >= 0 else None,
            "score_or_margin_failed",
        )

    def observe_person(
        self,
        session: Session,
        person: Person,
        event: Event,
        track: FaceTrack,
        similarity: float | None,
    ) -> None:
        existing = session.scalar(
            select(PersonObservation).where(
                PersonObservation.person_id == person.id,
                PersonObservation.event_id == event.id,
            )
        )
        if existing:
            return
        session.add(
            PersonObservation(
                person_id=person.id,
                event_id=event.id,
                event_day=event.occurred_at.date().isoformat(),
                source_track_id=track.id,
                similarity=similarity,
            )
        )
        session.flush()
        person.matched_events = int(
            session.scalar(
                select(func.count(PersonObservation.id)).where(
                    PersonObservation.person_id == person.id
                )
            )
            or 0
        )
        person.distinct_days = int(
            session.scalar(
                select(func.count(func.distinct(PersonObservation.event_day))).where(
                    PersonObservation.person_id == person.id
                )
            )
            or 0
        )
        occurred = ensure_utc(event.occurred_at)
        person.first_seen = min(
            ensure_utc(person.first_seen) if person.first_seen else occurred,
            occurred,
        )
        person.last_seen = max(
            ensure_utc(person.last_seen) if person.last_seen else occurred,
            occurred,
        )
        if (
            person.status == "provisional"
            and person.matched_events >= self.settings.trusted_person_events
            and person.distinct_days >= self.settings.trusted_person_days
        ):
            person.status = "trusted"


@dataclass(slots=True)
class ClusterAssignment:
    cluster: UnknownCluster
    created: bool
    became_review_ready: bool
    similarity: float


class UnknownClusterer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def refresh(self, session: Session, cluster: UnknownCluster) -> None:
        members = list(
            session.scalars(
                select(UnknownClusterMember).where(
                    UnknownClusterMember.cluster_id == cluster.id
                )
            )
        )
        if not members:
            session.delete(cluster)
            return
        tracks = {
            track.id: track
            for track in session.scalars(
                select(FaceTrack).where(
                    FaceTrack.id.in_([member.track_id for member in members])
                )
            )
        }
        vectors = [
            unpack_vector(tracks[member.track_id].embedding, cluster.embedding_dimension)
            for member in members
            if member.track_id in tracks
        ]
        weights = [
            max(member.quality_score, 0.05)
            for member in members
            if member.track_id in tracks
        ]
        if vectors:
            cluster.centroid = pack_vector(weighted_centroid(vectors, weights))
        cluster.member_count = len(members)
        cluster.event_count = len({member.event_id for member in members})
        cluster.distinct_days = len({member.event_day for member in members})
        cluster.high_quality_count = sum(
            member.quality_score >= self.settings.minimum_quality_score
            for member in members
        )
        event_times = [
            ensure_utc(value)
            for value in session.scalars(
                select(Event.occurred_at).where(
                    Event.id.in_([member.event_id for member in members])
                )
            )
        ]
        if event_times:
            cluster.first_seen = min(event_times)
            cluster.last_seen = max(event_times)
        ready = (
            cluster.event_count >= self.settings.cluster_review_events
            and cluster.distinct_days >= self.settings.cluster_review_days
            and cluster.high_quality_count >= self.settings.cluster_review_tracks
        )
        if cluster.status in {"candidate", "review_ready"}:
            cluster.status = "review_ready" if ready else "candidate"
        cluster.version += 1

    def assign(
        self,
        session: Session,
        event: Event,
        track: FaceTrack,
        embedding: np.ndarray,
        quality_score: float,
    ) -> ClusterAssignment:
        candidates = list(
            session.scalars(
                select(UnknownCluster).where(
                    UnknownCluster.model_id == track.model_id,
                    UnknownCluster.status.in_(["candidate", "review_ready"]),
                )
            )
        )
        best: tuple[float, UnknownCluster] | None = None
        for cluster in candidates:
            member_track_ids = select(UnknownClusterMember.track_id).where(
                UnknownClusterMember.cluster_id == cluster.id
            )
            cannot_link = session.scalar(
                select(CannotLink.id).where(
                    or_(
                        (
                            (CannotLink.left_track_id == track.id)
                            & (CannotLink.right_track_id.in_(member_track_ids))
                        ),
                        (
                            (CannotLink.right_track_id == track.id)
                            & (CannotLink.left_track_id.in_(member_track_ids))
                        ),
                    )
                )
            )
            if cannot_link:
                continue
            score = cosine_similarity(
                embedding,
                unpack_vector(cluster.centroid, cluster.embedding_dimension),
            )
            if score >= self.settings.cluster_similarity and (
                best is None or score > best[0]
            ):
                best = (score, cluster)
        created = best is None
        if created:
            cluster = UnknownCluster(
                model_id=track.model_id,
                centroid=pack_vector(embedding),
                embedding_dimension=embedding.size,
                first_seen=event.occurred_at,
                last_seen=event.occurred_at,
            )
            session.add(cluster)
            session.flush()
            similarity = 1.0
        else:
            similarity, cluster = best
        was_ready = cluster.status == "review_ready"
        session.add(
            UnknownClusterMember(
                cluster_id=cluster.id,
                track_id=track.id,
                event_id=event.id,
                event_day=event.occurred_at.date().isoformat(),
                similarity=similarity,
                quality_score=quality_score,
            )
        )
        track.unknown_cluster_id = cluster.id
        session.flush()
        self.refresh(session, cluster)
        return ClusterAssignment(
            cluster=cluster,
            created=created,
            became_review_ready=cluster.status == "review_ready" and not was_ready,
            similarity=similarity,
        )


def remove_person_index(session: Session, person_id: str) -> None:
    session.execute(delete(PersonIndex).where(PersonIndex.person_id == person_id))
