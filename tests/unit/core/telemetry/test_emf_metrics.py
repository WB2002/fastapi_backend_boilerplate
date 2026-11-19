from __future__ import annotations

import json

import pytest

from app.core.telemetry import (
    VALID_SERVICE_UNAVAILABLE_REASONS,
    ClientIdentity,
    CountryAccessTelemetry,
    EmfMetricDefinition,
    EmfUnit,
    EmitServiceUnavailableOptions,
    emit_country_access_metric,
    emit_feature_flag_metrics,
    emit_http_metrics,
    emit_service_unavailable_metric,
    flush_embedded_metrics,
)


class TestEmfMetrics:
    async def test_no_ops_when_disabled(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        res = await emit_service_unavailable_metric(
            EmitServiceUnavailableOptions(
                enabled=False,
                reason="inbound_bulkhead",
                path="/api/foo",
            )
        )
        assert res is None
        assert capsys.readouterr().out == ""

    async def test_emits_application_service_unavailable_for_all_reasons(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        expected_reasons = (
            "under_pressure",
            "inbound_bulkhead",
            "api_priority_shed",
            "graphql_concurrency",
            "health_check",
            "dependency_unavailable",
            "inbound_load_level_queue_full",
            "inbound_load_level_wait_timeout",
        )
        assert set(expected_reasons) == VALID_SERVICE_UNAVAILABLE_REASONS

        for reason in expected_reasons:
            await emit_service_unavailable_metric(
                EmitServiceUnavailableOptions(
                    enabled=True,
                    reason=reason,  # type: ignore[arg-type]
                    path="/auth/login",
                    pressure_type="eventLoopDelay"
                    if reason == "under_pressure"
                    else None,
                )
            )

        lines = [
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip()
        ]
        assert len(lines) == len(expected_reasons)

        first = lines[0]
        assert first["Reason"] == "under_pressure"
        assert first["ServiceUnavailable"] == 1
        assert first["Path"] == "/auth/login"
        assert first["PressureType"] == "eventLoopDelay"

        aws = first["_aws"]
        assert isinstance(aws["Timestamp"], int)
        cw = aws["CloudWatchMetrics"][0]
        assert cw["Namespace"] == "Application"
        assert cw["Dimensions"] == [["Reason"]]
        assert cw["Metrics"] == [{"Name": "ServiceUnavailable", "Unit": "Count"}]

    async def test_emits_feature_flags_http_client_and_country_access_namespaces(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        await emit_feature_flag_metrics(
            enabled=True,
            name="feature_flag_evaluation_total",
            value=1,
            dimensions={"Result": "on"},
        )
        await emit_http_metrics(
            enabled=True,
            identity=ClientIdentity(service_name="hcaptcha", kind="external"),
            method="POST",
            status=200,
            latency_ms=42,
            attempts=1,
            success=True,
        )
        telemetry = CountryAccessTelemetry()
        await telemetry.access_denied("RU", "not_in_allowlist")
        await emit_country_access_metric("country_access_invalid_ip", {})

        await flush_embedded_metrics(
            namespace="Custom",
            dimensions={"Env": "test"},
            metrics=[EmfMetricDefinition("CustomCount", 5, EmfUnit.COUNT)],
            properties={"Tag": "v1"},
        )

        lines = [
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip()
        ]
        namespaces = [
            entry["_aws"]["CloudWatchMetrics"][0]["Namespace"] for entry in lines
        ]
        assert namespaces == [
            "FeatureFlags",
            "HttpClient",
            "CountryAccess",
            "CountryAccess",
            "Custom",
        ]
        assert lines[0]["feature_flag_evaluation_total"] == 1
        assert lines[0]["Result"] == "on"
        assert lines[1]["Service"] == "hcaptcha"
        assert lines[1]["Latency"] == 42
        assert lines[1]["Success"] == 1
        assert lines[1]["HttpStatus"] == 200
        assert lines[2]["country_access_denied"] == 1
        assert lines[2]["countryCode"] == "RU"
