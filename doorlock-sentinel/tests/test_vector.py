import numpy as np
import pytest

from doorlock_sentinel.vector import (
    cosine_similarity,
    pack_vector,
    unpack_vector,
    weighted_centroid,
)


def test_vector_roundtrip_is_normalized():
    source = np.array([3, 4, 0, 0], dtype=np.float32)
    restored = unpack_vector(pack_vector(source), 4)
    assert np.linalg.norm(restored) == pytest.approx(1.0)
    assert cosine_similarity(source, restored) == pytest.approx(1.0)


def test_weighted_centroid_prefers_larger_weight():
    left = np.array([1, 0], dtype=np.float32)
    right = np.array([0, 1], dtype=np.float32)
    result = weighted_centroid([left, right], [9, 1])
    assert result[0] > result[1]
