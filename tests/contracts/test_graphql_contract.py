from __future__ import annotations

from pathlib import Path

from graphql import build_schema, parse, validate

from app.graphql.schema import schema


def test_graphql_sdl_contract() -> None:
    sdl = schema.as_str()
    gql_schema = build_schema(sdl)

    # 1. Validate categories query document
    doc1 = parse("{ categories { id name description } }")
    assert validate(gql_schema, doc1) == []

    # 2. Validate category by id query document
    doc2 = parse('{ category(id: "1") { id name } }')
    assert validate(gql_schema, doc2) == []

    # 3. Validate searchCategories with CategoryFilterInput
    query_str = (
        "query Search($filter: CategoryFilterInput!) {\n"
        "  searchCategories(filter: $filter) { id name }\n"
        "}"
    )
    doc3 = parse(query_str)
    assert validate(gql_schema, doc3) == []


def test_graphql_sdl_matches_reference_contract() -> None:
    ref_snapshot_path = Path("schema.graphql")
    if ref_snapshot_path.exists():
        ref_sdl = ref_snapshot_path.read_text()
        ref_schema = build_schema(ref_sdl)

        # Ensure our generated schema has all reference types
        for type_name in ["Category", "CategoryFilterInput", "Query"]:
            assert type_name in ref_schema.type_map
            assert type_name in build_schema(schema.as_str()).type_map
