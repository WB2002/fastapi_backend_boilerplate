from __future__ import annotations

from app.core.feature_flag.types import IFeatureFlag


class FeatureFlagCache:
    def __init__(self) -> None:
        self._flags: dict[str, IFeatureFlag] = {}

    def get(self, key: str) -> IFeatureFlag | None:
        return self._flags.get(key)

    def set(self, flag: IFeatureFlag) -> None:
        self._flags[flag.key] = flag

    def delete(self, key: str) -> None:
        self._flags.pop(key, None)

    def load_all(self, flags: list[IFeatureFlag]) -> None:
        self._flags = {f.key: f for f in flags}

    def clear(self) -> None:
        self._flags.clear()

    def size(self) -> int:
        return len(self._flags)

    def list(self) -> list[IFeatureFlag]:
        return list(self._flags.values())
