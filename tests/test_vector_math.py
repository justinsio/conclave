"""Vector maths for corpus similarity. Pure logic, no DB."""
import math

from app.services.embeddings import normalize_vector, vector_cosine, vector_dot


def _magnitude(v):
    return math.sqrt(sum(x * x for x in v))


def test_normalize_returns_a_unit_vector():
    result = normalize_vector([3.0, 4.0])
    assert math.isclose(_magnitude(result), 1.0, rel_tol=1e-9)


def test_normalize_preserves_direction():
    result = normalize_vector([3.0, 4.0])
    assert math.isclose(result[0], 0.6, rel_tol=1e-9)
    assert math.isclose(result[1], 0.8, rel_tol=1e-9)


def test_normalize_is_idempotent():
    once = normalize_vector([3.0, 4.0])
    twice = normalize_vector(once)
    for a, b in zip(once, twice):
        assert math.isclose(a, b, rel_tol=1e-9)


def test_normalize_of_a_zero_vector_does_not_divide_by_zero():
    """A zero vector has no direction. Return it unchanged rather than raising —
    an unembeddable row must not crash a search."""
    assert normalize_vector([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_normalize_of_an_empty_vector_returns_empty():
    assert normalize_vector([]) == []


def test_dot_of_normalized_vectors_equals_cosine():
    """The whole basis of the optimization: once both vectors are unit length,
    cosine similarity IS the dot product."""
    a = normalize_vector([1.0, 2.0, 3.0])
    b = normalize_vector([4.0, 5.0, 6.0])
    assert math.isclose(vector_dot(a, b), vector_cosine(a, b), rel_tol=1e-9)


def test_dot_of_identical_unit_vectors_is_one():
    a = normalize_vector([1.0, 2.0, 3.0])
    assert math.isclose(vector_dot(a, a), 1.0, rel_tol=1e-9)


def test_dot_of_orthogonal_unit_vectors_is_zero():
    assert math.isclose(vector_dot([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-12)


def test_dot_stops_at_the_shorter_vector():
    """zip() truncates. Pinned so a dimension mismatch cannot raise mid-search."""
    assert math.isclose(vector_dot([1.0, 1.0, 99.0], [1.0, 1.0]), 2.0, rel_tol=1e-9)
