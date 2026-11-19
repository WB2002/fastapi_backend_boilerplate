from __future__ import annotations

import strawberry

from app.graphql.categories_service import CategoriesService
from app.graphql.complexity_extension import GraphqlConcurrencyExtension
from app.graphql.types import Category, CategoryFilterInput

_categories_service = CategoriesService()


def get_categories_service() -> CategoriesService:
    return _categories_service


@strawberry.type
class Query:
    @strawberry.field
    def categories(self) -> list[Category]:
        return get_categories_service().find_all()

    @strawberry.field
    def category(self, id: strawberry.ID) -> Category | None:
        return get_categories_service().find_one_by_id(str(id))

    @strawberry.field
    def search_categories(self, filter: CategoryFilterInput) -> list[Category]:
        return get_categories_service().search(filter)


schema = strawberry.Schema(
    query=Query,
    extensions=[GraphqlConcurrencyExtension],
)
