from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.common.country_access.types import (
    CountryAccessRepository,
    CountryAccessServiceStatus,
    UnknownCountryPolicy,
    assert_country_code,
)
from app.core.telemetry.emf_metrics import CountryAccessTelemetry

logger = logging.getLogger("CountryAccessService")


@dataclass(slots=True)
class CountryAccessModuleOptions:
    unknown_country_policy: UnknownCountryPolicy = "deny"
    bypass_paths: list[str] = field(default_factory=lambda: ["/health", "/metrics"])
    refresh_interval_ms: int = 60_000
    persistence_provider: str = "postgres"


class CountryAccessService:
    def __init__(
        self,
        repository: CountryAccessRepository,
        options: CountryAccessModuleOptions | None = None,
        telemetry: CountryAccessTelemetry | None = None,
    ) -> None:
        self._repository = repository
        self._options = options or CountryAccessModuleOptions()
        self._telemetry = telemetry or CountryAccessTelemetry()

        self._allowed_countries: set[str] = set()
        self._status: CountryAccessServiceStatus = "inactive"
        self._snapshot_version = 0
        self._last_successful_refresh_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._is_running = False

    @property
    def status(self) -> CountryAccessServiceStatus:
        return self._status

    def is_active(self) -> bool:
        return self._status == "active"

    def get_unknown_country_policy(self) -> UnknownCountryPolicy:
        return self._options.unknown_country_policy

    def get_bypass_paths(self) -> list[str]:
        return self._options.bypass_paths

    def get_snapshot_version(self) -> int:
        return self._snapshot_version

    def get_last_successful_refresh_at(self) -> datetime | None:
        return self._last_successful_refresh_at

    def get_allowed_countries(self) -> set[str]:
        return set(self._allowed_countries)

    def is_country_allowed(self, country_code: str) -> bool:
        if self._status != "active":
            return True
        try:
            normalized = assert_country_code(country_code)
        except ValueError:
            return self._options.unknown_country_policy == "allow"
        return normalized in self._allowed_countries

    async def refresh_snapshot(self) -> None:
        async with self._refresh_lock:
            await self._do_refresh()

    async def _do_refresh(self) -> None:
        try:
            configs = await self._repository.get_all()
            enabled = {
                assert_country_code(cfg.country_code) for cfg in configs if cfg.enabled
            }
            self._allowed_countries = enabled
            self._snapshot_version += 1
            now = datetime.now(UTC)
            self._last_successful_refresh_at = now
            self._status = "active"

            await self._telemetry.refresh_success(
                provider=self._options.persistence_provider,
                snapshot_version=self._snapshot_version,
                allowed_count=len(enabled),
                last_successful_refresh_at=now.isoformat(),
            )
        except Exception as exc:
            reason = str(exc)
            logger.error(
                "Country-access refresh failed; retaining snapshot (status=%s): %s",
                self._status,
                reason,
            )
            await self._telemetry.refresh_failure(
                self._options.persistence_provider, reason
            )

    def start_periodic_refresh(self) -> None:
        if self._is_running:
            return
        self._is_running = True

        async def _loop() -> None:
            interval_sec = max(1.0, self._options.refresh_interval_ms / 1000.0)
            while self._is_running:
                try:
                    await asyncio.sleep(interval_sec)
                    if self._is_running:
                        await self.refresh_snapshot()
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Periodic refresh error: %s", exc)

        self._refresh_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        self._is_running = False
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
