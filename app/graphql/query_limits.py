from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from graphql.language.ast import (
    DocumentNode,
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    OperationDefinitionNode,
)

MAX_QUERY_DEPTH: int = 10


@dataclass(frozen=True, slots=True)
class GraphqlLimitError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _collect_fragments(
    document_ast: DocumentNode,
) -> dict[str, FragmentDefinitionNode]:
    fragments: dict[str, FragmentDefinitionNode] = {}
    for definition in document_ast.definitions:
        if isinstance(definition, FragmentDefinitionNode):
            fragments[definition.name.value] = definition
    return fragments


def _field_depth(
    node: FieldNode,
    fragments: dict[str, FragmentDefinitionNode],
    seen: set[str],
) -> int:
    if not node.selection_set:
        return 1
    child_depths: list[int] = []
    for selection in node.selection_set.selections:
        if isinstance(selection, FieldNode):
            child_depths.append(_field_depth(selection, fragments, seen))
        elif isinstance(selection, InlineFragmentNode):
            if selection.selection_set:
                for inner in selection.selection_set.selections:
                    if isinstance(inner, FieldNode):
                        child_depths.append(_field_depth(inner, fragments, seen))
        elif isinstance(selection, FragmentSpreadNode):
            name = selection.name.value
            if name in seen:
                continue
            frag = fragments.get(name)
            if frag and frag.selection_set:
                for inner in frag.selection_set.selections:
                    if isinstance(inner, FieldNode):
                        child_depths.append(
                            _field_depth(inner, fragments, seen | {name})
                        )
    return 1 + (max(child_depths) if child_depths else 0)


def assert_query_depth(
    document_ast: DocumentNode,
    *,
    max_depth: int = MAX_QUERY_DEPTH,
) -> None:
    fragments = _collect_fragments(document_ast)
    depths: list[int] = []
    for definition in document_ast.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue
        if not definition.selection_set:
            continue
        for selection in definition.selection_set.selections:
            if isinstance(selection, FieldNode):
                depths.append(_field_depth(selection, fragments, set()))
    if depths and max(depths) > max_depth:
        raise GraphqlLimitError(
            code="QUERY_DEPTH_LIMIT_EXCEEDED",
            message=f"Query depth {max(depths)} exceeds maximum {max_depth}",
        )


def estimate_query_complexity(
    document_ast: DocumentNode,
    *,
    max_complexity: int,
) -> int:
    total = 0
    fragments = _collect_fragments(document_ast)
    stack: list[Any] = []

    def push_selections(selections: tuple[Any, ...] | list[Any]) -> None:
        stack.extend(selections)

    for definition in document_ast.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            continue
        if not definition.selection_set:
            continue
        push_selections(list(definition.selection_set.selections))

    while stack:
        node = stack.pop()
        if isinstance(node, FieldNode):
            total += 1
            if node.selection_set:
                push_selections(list(node.selection_set.selections))
        elif isinstance(node, InlineFragmentNode) and node.selection_set:
            push_selections(list(node.selection_set.selections))
        elif isinstance(node, FragmentSpreadNode):
            frag = fragments.get(node.name.value)
            if frag and frag.selection_set:
                push_selections(list(frag.selection_set.selections))

    if total > max_complexity:
        raise GraphqlLimitError(
            code="QUERY_TOO_COMPLEX",
            message=f"Query complexity {total} exceeds maximum {max_complexity}",
        )
    return total


__all__ = [
    "MAX_QUERY_DEPTH",
    "GraphqlLimitError",
    "assert_query_depth",
    "estimate_query_complexity",
]
