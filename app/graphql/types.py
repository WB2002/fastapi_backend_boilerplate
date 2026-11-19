from __future__ import annotations

import strawberry


@strawberry.type
class Category:
    id: strawberry.ID
    name: str
    description: str | None = None


@strawberry.input
class CategoryFilterInput:
    name: str
    website_url: str | None = strawberry.field(
        default=strawberry.UNSET, name="websiteUrl"
    )
