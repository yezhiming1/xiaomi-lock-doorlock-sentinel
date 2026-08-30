from __future__ import annotations

import numpy as np


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-12:
        raise ValueError("zero-length embedding")
    return value / norm


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(normalize(left), normalize(right)))


def pack_vector(vector: np.ndarray) -> bytes:
    return normalize(vector).astype("<f4", copy=False).tobytes()


def unpack_vector(blob: bytes, expected_dimension: int | None = None) -> np.ndarray:
    vector = np.frombuffer(blob, dtype="<f4").astype(np.float32, copy=True)
    if expected_dimension is not None and vector.size != expected_dimension:
        raise ValueError(f"embedding dimension {vector.size}, expected {expected_dimension}")
    return normalize(vector)


def weighted_centroid(vectors: list[np.ndarray], weights: list[float] | None = None) -> np.ndarray:
    if not vectors:
        raise ValueError("at least one vector is required")
    matrix = np.stack([normalize(v) for v in vectors])
    if weights is None:
        aggregate = matrix.mean(axis=0)
    else:
        w = np.asarray(weights, dtype=np.float32)
        if w.shape != (len(vectors),):
            raise ValueError("weights length mismatch")
        aggregate = np.average(matrix, axis=0, weights=w)
    return normalize(aggregate)
