from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .face_backend import DetectedFace
from .vector import cosine_similarity, weighted_centroid


@dataclass(slots=True)
class FaceSample:
    frame_index: int
    timestamp: float
    frame: np.ndarray
    face: DetectedFace


@dataclass(slots=True)
class FaceTrackResult:
    index: int
    embedding: np.ndarray
    quality_score: float
    samples: list[FaceSample]
    first_timestamp: float
    last_timestamp: float
    observed_frame_indices: frozenset[int]

    @property
    def best_sample(self) -> FaceSample:
        return max(self.samples, key=lambda item: item.face.quality_score)


@dataclass
class _Track:
    index: int
    samples: list[FaceSample] = field(default_factory=list)
    centroid: np.ndarray | None = None
    last_bbox: tuple[int, int, int, int] | None = None
    first_timestamp: float | None = None
    last_timestamp: float = 0.0
    observed_frame_indices: set[int] = field(default_factory=set)

    def add(self, sample: FaceSample, max_samples: int) -> None:
        self.observed_frame_indices.add(sample.frame_index)
        if self.first_timestamp is None:
            self.first_timestamp = sample.timestamp
        self.samples.append(sample)
        self.samples.sort(key=lambda item: item.face.quality_score, reverse=True)
        self.samples = self.samples[:max_samples]
        embeddings = [
            item.face.embedding
            for item in self.samples
            if item.face.embedding is not None
        ]
        if embeddings:
            weights = [
                max(item.face.quality_score, 0.05)
                for item in self.samples
                if item.face.embedding is not None
            ]
            self.centroid = weighted_centroid(embeddings, weights)
        self.last_bbox = sample.face.bbox
        self.last_timestamp = sample.timestamp


def _iou(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    x1 = max(lx, rx)
    y1 = max(ly, ry)
    x2 = min(lx + lw, rx + rw)
    y2 = min(ly + lh, ry + rh)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = lw * lh + rw * rh - intersection
    return intersection / union if union > 0 else 0.0


def _normalized_center_distance(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    left_center = np.array([lx + lw / 2, ly + lh / 2], dtype=np.float32)
    right_center = np.array([rx + rw / 2, ry + rh / 2], dtype=np.float32)
    scale = max((lw + lh + rw + rh) / 4, 1.0)
    return float(np.linalg.norm(left_center - right_center) / scale)


def _match_score(
    sample: FaceSample,
    track: _Track,
    *,
    min_similarity: float,
    strong_similarity: float,
    min_iou: float,
    max_center_distance: float,
) -> float | None:
    if sample.face.embedding is None or track.centroid is None or track.last_bbox is None:
        return None
    similarity = cosine_similarity(sample.face.embedding, track.centroid)
    overlap = _iou(sample.face.bbox, track.last_bbox)
    distance = _normalized_center_distance(sample.face.bbox, track.last_bbox)
    spatial_ok = overlap >= min_iou or distance <= max_center_distance
    if similarity < min_similarity or (not spatial_ok and similarity < strong_similarity):
        return None
    proximity = max(0.0, 1.0 - distance / max(max_center_distance, 0.01))
    return 0.58 * similarity + 0.27 * overlap + 0.15 * proximity


def build_tracks(
    detections: list[FaceSample],
    *,
    min_similarity: float,
    strong_similarity: float,
    min_iou: float,
    max_center_distance: float,
    max_gap_seconds: float,
    max_samples: int,
) -> list[FaceTrackResult]:
    """Greedy face association with one-to-one assignment per sampled frame."""

    tracks: list[_Track] = []
    by_frame: dict[int, list[FaceSample]] = {}
    for sample in detections:
        if sample.face.embedding is not None:
            by_frame.setdefault(sample.frame_index, []).append(sample)
    for frame_index in sorted(by_frame):
        frame_samples = sorted(
            by_frame[frame_index],
            key=lambda item: item.face.quality_score,
            reverse=True,
        )
        candidates: list[tuple[float, int, int]] = []
        for sample_index, sample in enumerate(frame_samples):
            for track_index, track in enumerate(tracks):
                gap = sample.timestamp - track.last_timestamp
                if gap < 0 or gap > max_gap_seconds:
                    continue
                score = _match_score(
                    sample,
                    track,
                    min_similarity=min_similarity,
                    strong_similarity=strong_similarity,
                    min_iou=min_iou,
                    max_center_distance=max_center_distance,
                )
                if score is not None:
                    candidates.append((score, sample_index, track_index))
        used_samples: set[int] = set()
        used_tracks: set[int] = set()
        for _score, sample_index, track_index in sorted(candidates, reverse=True):
            if sample_index in used_samples or track_index in used_tracks:
                continue
            tracks[track_index].add(frame_samples[sample_index], max_samples)
            used_samples.add(sample_index)
            used_tracks.add(track_index)
        for sample_index, sample in enumerate(frame_samples):
            if sample_index in used_samples:
                continue
            track = _Track(index=len(tracks))
            track.add(sample, max_samples)
            tracks.append(track)

    results: list[FaceTrackResult] = []
    for track in tracks:
        if not track.samples or track.centroid is None:
            continue
        weights = [max(item.face.quality_score, 0.05) for item in track.samples]
        results.append(
            FaceTrackResult(
                index=track.index,
                embedding=weighted_centroid(
                    [item.face.embedding for item in track.samples],
                    weights,
                ),
                quality_score=float(sum(weights) / len(weights)),
                samples=track.samples,
                first_timestamp=(
                    track.first_timestamp
                    if track.first_timestamp is not None
                    else min(item.timestamp for item in track.samples)
                ),
                last_timestamp=track.last_timestamp,
                observed_frame_indices=frozenset(track.observed_frame_indices),
            )
        )
    return results


def cooccurring_track_pairs(
    tracks: list[FaceTrackResult],
) -> set[tuple[int, int]]:
    """Return only track pairs observed in the same sampled frame."""

    frames = {track.index: track.observed_frame_indices for track in tracks}
    pairs: set[tuple[int, int]] = set()
    for position, left in enumerate(tracks):
        for right in tracks[position + 1 :]:
            if frames[left.index] & frames[right.index]:
                pairs.add(tuple(sorted((left.index, right.index))))
    return pairs
