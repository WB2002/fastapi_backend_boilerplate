from __future__ import annotations

import pytest
import strawberry

from app.graphql.categories_service import CategoriesService
from app.graphql.schema import schema
from app.graphql.types import CategoryFilterInput


def test_categories_service_find_all() -> None:
    svc = CategoriesService()
    items = svc.find_all()
    assert len(items) == 3
    assert items[0].name == "Electronics"
    assert items[1].name == "Books"
    assert items[2].name == "Clothing"


def test_categories_service_find_one_by_id() -> None:
    svc = CategoriesService()
    assert svc.find_one_by_id("1") is not None
    assert svc.find_one_by_id("1").name == "Electronics"  # type: ignore[union-attr]
    assert svc.find_one_by_id("non-existent") is None


def test_categories_service_search() -> None:
    svc = CategoriesService()
    res = svc.search(CategoryFilterInput(name="book", website_url=strawberry.UNSET))
    assert len(res) == 1
    assert res[0].name == "Books"


@pytest.mark.asyncio
async def test_graphql_categories_query() -> None:
    query = """
    query {
      categories {
        id
        name
        description
      }
    }
    """
    res = await schema.execute(query)
    assert res.errors is None
    assert res.data is not None
    assert len(res.data["categories"]) == 3
    assert res.data["categories"][0]["name"] == "Electronics"


@pytest.mark.asyncio
async def test_graphql_category_by_id_query() -> None:
    query = """
    query GetCategory($id: ID!) {
      category(id: $id) {
        id
        name
      }
    }
    """
    res = await schema.execute(query, variable_values={"id": "2"})
    assert res.errors is None
    assert res.data is not None
    assert res.data["category"]["name"] == "Books"


@pytest.mark.asyncio
async def test_graphql_search_categories_query() -> None:
    query = """
    query Search($filter: CategoryFilterInput!) {
      searchCategories(filter: $filter) {
        id
        name
      }
    }
    """
    res = await schema.execute(query, variable_values={"filter": {"name": "cloth"}})
    assert res.errors is None
    assert res.data is not None
    assert len(res.data["searchCategories"]) == 1
    assert res.data["searchCategories"][0]["name"] == "Clothing"
