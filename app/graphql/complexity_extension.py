from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Final

from strawberry.extensions import SchemaExtension
from strawberry.types import ExecutionContext

logger = logging.getLogger("GraphqlComplexityExtension")

GRAPHQL_OVERLOAD_EXTENSION_CODE: Final[str] = "TOO_MANY_CONCURRENT_REQUESTS"
GQL_CONCURRENCY_KEY_PREFIX: Final[str] = "gql:concurrency:"
GQL_BUDGET_KEY_PREFIX: Final[str] = "gql:budget:"
DEFAULT_GQL_CONCURRENCY_KEY_TTL_SEC: Final[int] = 300


class GraphqlConcurrencyExtension(SchemaExtension):
    def __init__(
        self,
        *,
        max_concurrent: int = 20,
        execution_context: ExecutionContext | None = None,
    ) -> None:
        super().__init__(execution_context=execution_context)
        self._max_concurrent = max_concurrent

    async def on_operation(self) -> AsyncIterator[None]:
        yield
