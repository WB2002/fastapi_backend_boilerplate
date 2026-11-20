from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Final

from graphql import GraphQLError, parse
from graphql.language.ast import DocumentNode
from strawberry.extensions import SchemaExtension
from strawberry.types import ExecutionContext

from app.core.telemetry.emf_metrics import record_graphql_concurrency_503
from app.graphql.gql_subject import resolve_gql_subject_from_context
from app.graphql.query_limits import (
    GraphqlLimitError,
    assert_query_depth,
    estimate_query_complexity,
)
from app.graphql.redis_budget import (
    MemoryRedisBudgetClient,
    RedisBudgetClient,
    assert_budget_available,
    consume_budget,
    release_concurrency,
    try_acquire_concurrency,
)

logger = logging.getLogger("GraphqlComplexityExtension")

GRAPHQL_OVERLOAD_EXTENSION_CODE: Final[str] = "TOO_MANY_CONCURRENT_REQUESTS"
GQL_CONCURRENCY_KEY_PREFIX: Final[str] = "gql:concurrency:"
GQL_BUDGET_KEY_PREFIX: Final[str] = "gql:budget:"
DEFAULT_GQL_CONCURRENCY_KEY_TTL_SEC: Final[int] = 300


def _graphql_error(err: GraphqlLimitError) -> GraphQLError:
    return GraphQLError(err.message, extensions={"code": err.code})


class GraphqlProtectionExtension(SchemaExtension):
    def __init__(
        self,
        *,
        redis_client: RedisBudgetClient | None = None,
        max_complexity: int = 50,
        user_budget: int = 1000,
        max_concurrency: int = 3,
        concurrency_ttl_sec: int = DEFAULT_GQL_CONCURRENCY_KEY_TTL_SEC,
        execution_context: ExecutionContext | None = None,
    ) -> None:
        super().__init__(execution_context=execution_context)
        self._redis = redis_client or MemoryRedisBudgetClient()
        self._max_complexity = max_complexity
        self._user_budget = user_budget
        self._max_concurrency = max_concurrency
        self._concurrency_ttl_sec = concurrency_ttl_sec
        self._subject: str | None = None
        self._concurrency_acquired = False

    def _resolve_subject(self) -> str:
        if self._subject is not None:
            return self._subject
        ctx = self.execution_context.context if self.execution_context else None
        self._subject = resolve_gql_subject_from_context(ctx)
        return self._subject

    async def validate_before_execute(self, document: DocumentNode) -> int:
        assert_query_depth(document)
        complexity = estimate_query_complexity(
            document,
            max_complexity=self._max_complexity,
        )
        subject = self._resolve_subject()
        await assert_budget_available(
            self._redis,
            subject=subject,
            user_budget=self._user_budget,
            complexity=complexity,
        )
        return complexity

    async def on_operation(self) -> AsyncIterator[None]:
        query = self.execution_context.query if self.execution_context else None
        if not query:
            yield
            return
        document = parse(query)
        try:
            complexity = await self.validate_before_execute(document)
        except GraphqlLimitError as err:
            raise _graphql_error(err) from err

        subject = self._resolve_subject()
        acquired = await try_acquire_concurrency(
            self._redis,
            subject=subject,
            max_concurrency=self._max_concurrency,
            ttl_sec=self._concurrency_ttl_sec,
        )
        if not acquired:
            record_graphql_concurrency_503()
            ctx = self.execution_context.context if self.execution_context else None
            if isinstance(ctx, dict):
                response = ctx.get("response")
                headers = getattr(response, "headers", None)
                if headers is not None:
                    headers["Retry-After"] = "1"
            raise GraphQLError(
                "Too many concurrent GraphQL requests",
                extensions={"code": GRAPHQL_OVERLOAD_EXTENSION_CODE},
            )

        self._concurrency_acquired = True
        try:
            await consume_budget(
                self._redis,
                subject=subject,
                user_budget=self._user_budget,
                complexity=complexity,
            )
            yield
        finally:
            if self._concurrency_acquired:
                await release_concurrency(self._redis, subject=subject)
                self._concurrency_acquired = False


GraphqlConcurrencyExtension = GraphqlProtectionExtension

__all__ = [
    "DEFAULT_GQL_CONCURRENCY_KEY_TTL_SEC",
    "GQL_BUDGET_KEY_PREFIX",
    "GQL_CONCURRENCY_KEY_PREFIX",
    "GRAPHQL_OVERLOAD_EXTENSION_CODE",
    "GraphqlConcurrencyExtension",
    "GraphqlProtectionExtension",
]
