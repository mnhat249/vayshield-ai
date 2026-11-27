"""EC-AI20: B2B Vector Poisoning — tóm tắt ≤ 200 ký tự + Huber spherical medoid."""

from __future__ import annotations

import math

import pytest

from app.mule_graph.robust import (
    MAX_INTENT_CHARS,
    canonicalize_intent,
    huber_spherical_medoid,
    robust_centroid,
)

CORE = "Yêu cầu chuyển khoản phí bảo hiểm khoản vay để giải ngân."
JUNK = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 200


def test_ec_ai20_junk_padding_does_not_change_canonical_summary() -> None:
    clean = canonicalize_intent(CORE)
    poisoned = canonicalize_intent(JUNK + CORE + " " + JUNK)
    assert poisoned == clean
    assert len(poisoned) <= MAX_INTENT_CHARS


def test_ec_ai20_summary_never_exceeds_200_chars() -> None:
    long = " ".join(f"Cần chuyển khoản phí bảo hiểm số {i} để xác minh hồ sơ." for i in range(100))
    assert len(canonicalize_intent(long)) <= MAX_INTENT_CHARS
    assert len(canonicalize_intent("x" * 5000)) <= MAX_INTENT_CHARS
    assert canonicalize_intent("") == ""


def test_ec_ai20_repeated_sentences_deduplicated() -> None:
    assert canonicalize_intent((CORE + " ") * 50) == CORE


def test_medoid_ignores_outliers() -> None:
    base = [[1.0, 0.0, 0.0], [0.99, 0.05, 0.0], [0.97, 0.1, 0.0], [0.98, 0.0, 0.1]]
    outliers = [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]]
    idx = huber_spherical_medoid(base + outliers)
    assert idx < len(base)


def test_robust_centroid_is_unit_vector_and_deterministic() -> None:
    vecs = [[3.0, 4.0, 0.0], [3.0, 4.1, 0.0], [0.0, 0.0, 5.0]]
    c = robust_centroid(vecs)
    assert math.isclose(sum(x * x for x in c), 1.0)
    assert c == robust_centroid(vecs)


def test_medoid_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        huber_spherical_medoid([])
    with pytest.raises(ValueError):
        huber_spherical_medoid([[1.0, 0.0], [1.0]])
    with pytest.raises(ValueError):
        huber_spherical_medoid([[0.0, 0.0]])
