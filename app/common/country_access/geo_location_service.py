from __future__ import annotations

import ipaddress
import logging
from typing import Any

import geoip2.database

from app.common.country_access.types import (
    GeoLocationService,
    normalize_country_code,
)
from app.core.telemetry.emf_metrics import CountryAccessTelemetry

logger = logging.getLogger("MaxMindGeoLocationService")


class MaxMindGeoLocationService(GeoLocationService):
    def __init__(
        self,
        database_path: str | None = None,
        telemetry: CountryAccessTelemetry | None = None,
        reader: Any = None,
    ) -> None:
        self._database_path = database_path
        self._telemetry = telemetry or CountryAccessTelemetry()
        self._reader = reader

    def init_reader(self) -> None:
        if self._reader is not None or not self._database_path:
            return
        try:
            self._reader = geoip2.database.Reader(self._database_path)
            logger.info("Loaded GeoIP database from %s", self._database_path)
        except Exception as exc:
            logger.error("Failed to open GeoIP database: %s", exc)
            raise

    def close(self) -> None:
        if self._reader is not None and hasattr(self._reader, "close"):
            self._reader.close()
        self._reader = None

    def get_country_code(self, ip: str) -> str | None:
        if not self._reader:
            self._telemetry.geo_ip_failure_sync("reader_not_initialized")
            return None

        if not ip:
            self._telemetry.invalid_ip_sync()
            return None

        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError:
            self._telemetry.invalid_ip_sync()
            return None

        if addr.is_private or addr.is_reserved or addr.is_loopback:
            self._telemetry.unknown_country_sync("private_or_reserved_ip")
            return None

        try:
            response = self._reader.country(str(addr))
            iso = None
            if response.country and response.country.iso_code:
                iso = response.country.iso_code
            elif response.registered_country and response.registered_country.iso_code:
                iso = response.registered_country.iso_code

            if not iso:
                self._telemetry.unknown_country_sync("missing_geoip_record")
                return None

            normalized = normalize_country_code(iso)
            if not normalized:
                self._telemetry.unknown_country_sync("invalid_geoip_country_code")
                return None
            return normalized

        except Exception as exc:
            self._telemetry.geo_ip_failure_sync(str(exc))
            return None
