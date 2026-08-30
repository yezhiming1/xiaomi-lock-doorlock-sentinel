import numpy as np
from conftest import create_event

from doorlock_sentinel.models import FacePrototype, FaceTrack, Person
from doorlock_sentinel.recognition import IdentityMatcher, rebuild_person_index
from doorlock_sentinel.vector import pack_vector


def test_matcher_requires_score_and_margin(database, settings):
    matcher = IdentityMatcher(settings)
    with database.session() as session:
        event = create_event(session, 30)
        source = FaceTrack(
            event_id=event.id,
            track_index=0,
            model_id=settings.model_id,
            embedding=pack_vector(np.array([1, 0, 0, 0], dtype=np.float32)),
            embedding_dimension=4,
            quality_score=0.9,
            sample_count=3,
        )
        session.add(source)
        session.flush()
        person = Person(display_name="家人 A", relationship="family")
        session.add(person)
        session.flush()
        session.add(
            FacePrototype(
                person_id=person.id,
                source_track_id=source.id,
                model_id=settings.model_id,
                embedding=source.embedding,
                embedding_dimension=4,
                quality_score=0.9,
            )
        )
        session.flush()
        rebuild_person_index(session, settings, person.id, settings.model_id)
        decision = matcher.match(
            session,
            np.array([1, 0, 0, 0], dtype=np.float32),
            settings.model_id,
        )
        assert decision.decision == "known"
        assert decision.person.id == person.id
