from __future__ import annotations

import pytest
from graphql import parse

from app.graphql.query_limits import (
    GraphqlLimitError,
    assert_query_depth,
    estimate_query_complexity,
)


def test_rejects_query_deeper_than_ten() -> None:
    inner = "id"
    for _ in range(12):
        inner = f"categories {{ {inner} }}"
    doc = parse(f"query {{ {inner} }}")
    with pytest.raises(GraphqlLimitError) as exc:
        assert_query_depth(doc, max_depth=10)
    assert exc.value.code == "QUERY_DEPTH_LIMIT_EXCEEDED"


def test_rejects_complexity_above_cap() -> None:
    aliases = " ".join(f"a{i}: categories {{ id name }}" for i in range(60))
    doc = parse(f"query {{ {aliases} }}")
    with pytest.raises(GraphqlLimitError) as exc:
        estimate_query_complexity(doc, max_complexity=50)
    assert exc.value.code == "QUERY_TOO_COMPLEX"


def test_accepts_simple_categories_query() -> None:
    doc = parse("query { categories { id name } }")
    assert_query_depth(doc, max_depth=10)
    assert estimate_query_complexity(doc, max_complexity=50) <= 50
