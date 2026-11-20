from __future__ import annotations

from collections.abc import Mapping

import strawberry
from strawberry.types import ExecutionContext

from app.common.config.settings import Settings
from app.graphql.categories_service import CategoriesService
from app.graphql.complexity_extension import GraphqlProtectionExtension
from app.graphql.redis_budget import MemoryRedisBudgetClient, RedisBudgetClient
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


def build_graphql_schema(
    *,
    redis_client: RedisBudgetClient,
    env: Mapping[str, str | None] | None = None,
    max_complexity: int | None = None,
    user_budget: int | None = None,
    max_concurrency: int | None = None,
    concurrency_ttl_sec: int | None = None,
) -> strawberry.Schema:
    if env is not None:
        settings = Settings.from_env(env)
        max_complexity = (
            settings.gql_max_complexity if max_complexity is None else max_complexity
        )
        user_budget = settings.gql_user_budget if user_budget is None else user_budget
        max_concurrency = (
            settings.gql_max_concurrency if max_concurrency is None else max_concurrency
        )
        concurrency_ttl_sec = (
            settings.gql_concurrency_key_ttl_sec
            if concurrency_ttl_sec is None
            else concurrency_ttl_sec
        )
    else:
        max_complexity = 50 if max_complexity is None else max_complexity
        user_budget = 1000 if user_budget is None else user_budget
        max_concurrency = 3 if max_concurrency is None else max_concurrency
        concurrency_ttl_sec = (
            300 if concurrency_ttl_sec is None else concurrency_ttl_sec
        )
    if (
        max_complexity is None
        or user_budget is None
        or max_concurrency is None
        or concurrency_ttl_sec is None
    ):
        msg = "GraphQL limit configuration incomplete"
        raise ValueError(msg)
    bound_redis = redis_client
    bound_max_complexity = max_complexity
    bound_user_budget = user_budget
    bound_max_concurrency = max_concurrency
    bound_concurrency_ttl_sec = concurrency_ttl_sec

    class _ConfiguredGraphqlProtectionExtension(GraphqlProtectionExtension):
        def __init__(self, execution_context: ExecutionContext | None = None) -> None:
            super().__init__(
                redis_client=bound_redis,
                max_complexity=bound_max_complexity,
                user_budget=bound_user_budget,
                max_concurrency=bound_max_concurrency,
                concurrency_ttl_sec=bound_concurrency_ttl_sec,
                execution_context=execution_context,
            )

    return strawberry.Schema(
        query=Query,
        extensions=[_ConfiguredGraphqlProtectionExtension],
    )


schema = build_graphql_schema(redis_client=MemoryRedisBudgetClient())
