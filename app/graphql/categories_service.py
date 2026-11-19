from __future__ import annotations

import strawberry

from app.graphql.types import Category, CategoryFilterInput


class CategoriesService:
    def __init__(self) -> None:
        self.categories: list[dict[str, str | None]] = [
            {
                "id": "1",
                "name": "Electronics",
                "description": "Gadgets and devices",
            },
            {"id": "2", "name": "Books", "description": "All kinds of books"},
            {"id": "3", "name": "Clothing", "description": "Fashionable clothes"},
        ]

    def find_all(self) -> list[Category]:
        return [
            Category(
                id=strawberry.ID(str(c["id"])),
                name=str(c["name"]),
                description=c["description"],
            )
            for c in self.categories
        ]

    def find_one_by_id(self, id: str) -> Category | None:
        for c in self.categories:
            if c["id"] == id:
                return Category(
                    id=strawberry.ID(str(c["id"])),
                    name=str(c["name"]),
                    description=c["description"],
                )
        return None

    def search(self, filter_input: CategoryFilterInput) -> list[Category]:
        needle = filter_input.name.strip().lower()
        return [
            Category(
                id=strawberry.ID(str(c["id"])),
                name=str(c["name"]),
                description=c["description"],
            )
            for c in self.categories
            if needle in (c["name"] or "").lower()
        ]
