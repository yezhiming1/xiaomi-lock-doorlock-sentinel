import numpy as np

from doorlock_sentinel.face_backend import DetectedFace
from doorlock_sentinel.tracking import FaceSample, build_tracks, cooccurring_track_pairs


def _sample(
    frame_index: int,
    timestamp: float,
    x: int,
    vector: list[float],
    quality: float = 0.9,
) -> FaceSample:
    frame = np.zeros((120, 240, 3), dtype=np.uint8)
    embedding = np.array(vector, dtype=np.float32)
    face = DetectedFace(
        bbox=(x, 20, 50, 50),
        landmarks=np.zeros((5, 2), dtype=np.float32),
        detector_score=0.99,
        aligned_face=np.zeros((112, 112, 3), dtype=np.uint8),
        embedding=embedding,
        quality_score=quality,
        blur_score=100,
        brightness=100,
    )
    return FaceSample(frame_index, timestamp, frame, face)


def test_same_frame_faces_are_independent_tracks_and_cannot_link_pair():
    samples = [
        _sample(0, 0.0, 20, [1, 0, 0, 0]),
        _sample(0, 0.0, 160, [0, 1, 0, 0]),
        _sample(1, 0.5, 25, [1, 0, 0, 0]),
        _sample(1, 0.5, 155, [0, 1, 0, 0]),
    ]
    tracks = build_tracks(
        samples,
        min_similarity=0.34,
        strong_similarity=0.62,
        min_iou=0.08,
        max_center_distance=1.35,
        max_gap_seconds=2.5,
        max_samples=8,
    )
    assert len(tracks) == 2
    assert cooccurring_track_pairs(tracks) == {(0, 1)}


def test_sequential_fragments_are_not_marked_as_same_frame_conflict():
    samples = [
        _sample(0, 0.0, 20, [1, 0, 0, 0]),
        _sample(10, 10.0, 20, [1, 0, 0, 0]),
    ]
    tracks = build_tracks(
        samples,
        min_similarity=0.34,
        strong_similarity=0.62,
        min_iou=0.08,
        max_center_distance=1.35,
        max_gap_seconds=2.5,
        max_samples=8,
    )
    assert len(tracks) == 2
    assert not cooccurring_track_pairs(tracks)


def test_same_frame_constraint_survives_representative_sample_trimming():
    samples = [
        _sample(0, 0.0, 20, [1, 0, 0, 0], quality=0.5),
        _sample(0, 0.0, 160, [0, 1, 0, 0], quality=0.5),
        _sample(1, 0.5, 22, [1, 0, 0, 0], quality=0.8),
        _sample(2, 1.0, 24, [1, 0, 0, 0], quality=0.9),
        _sample(3, 1.5, 26, [1, 0, 0, 0], quality=0.99),
    ]
    tracks = build_tracks(
        samples,
        min_similarity=0.34,
        strong_similarity=0.62,
        min_iou=0.08,
        max_center_distance=1.35,
        max_gap_seconds=2.5,
        max_samples=2,
    )
    assert len(tracks) == 2
    assert all(len(track.samples) <= 2 for track in tracks)
    assert cooccurring_track_pairs(tracks) == {(0, 1)}
