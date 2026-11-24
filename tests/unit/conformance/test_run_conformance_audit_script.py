from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth.auth_service import AuthService, ConflictException
from app.auth.dto import RegisterDto
from app.realtime.socketio_server import AuthenticatedSocketIOApp

REPO_ROOT_DIR = Path(__file__).resolve().parents[3]
SOURCE_AUDIT_SCRIPT_PATH = REPO_ROOT_DIR / "scripts" / "ci" / "run-conformance-audit.sh"


def _allocate_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        address: tuple[str, int] = probe.getsockname()
        return address[1]


def _create_mock_sandbox(
    tmp_path: Path,
    *,
    has_pid_flag_support: bool,
) -> tuple[Path, Path, Path, Path]:
    script_dir = tmp_path / "scripts" / "ci"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir(parents=True)

    script_path = script_dir / "run-conformance-audit.sh"
    shutil.copy2(SOURCE_AUDIT_SCRIPT_PATH, script_path)
    script_path.chmod(0o755)

    (tmp_path / "boilerplate.manifest.json").write_text("{}", encoding="utf-8")

    stub_server_path = tmp_path / "stub_server.py"
    stub_server_path.write_text(
        "\n".join(
            [
                "import http.server, os, signal, sys",
                "port = int(os.environ.get('PORT', '3000'))",
                "class Handler(http.server.BaseHTTPRequestHandler):",
                "    def do_GET(self):",
                "        if self.path == '/health/live':",
                '            body = b\'{"status":"ok"}\'',
                "            self.send_response(200)",
                "            self.send_header('Content-Type', 'application/json')",
                "            self.send_header('Content-Length', str(len(body)))",
                "            self.end_headers()",
                "            self.wfile.write(body)",
                "            return",
                "        self.send_response(404)",
                "        self.end_headers()",
                "    def log_message(self, format, *args):",
                "        pass",
                "srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)",
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))",
                "srv.serve_forever()",
            ]
        ),
        encoding="utf-8",
    )

    captured_args_path = tmp_path / "captured-args.txt"
    pid_help_line = (
        "  -pid int\\n    \\tOptional host process PID to sample\\n"
        if has_pid_flag_support
        else ""
    )
    reject_pid_block = (
        ""
        if has_pid_flag_support
        else "\n".join(
            [
                'for arg in "$@"; do',
                '  if [[ "${arg}" == "--pid" || "${arg}" == "-pid"'
                ' || "${arg}" == --pid=* || "${arg}" == -pid=* ]]; then',
                '    echo "flag provided but not defined: -pid" >&2',
                "    exit 2",
                "  fi",
                "done",
            ]
        )
    )
    mock_audit_bin_path = bin_dir / "conformance-audit"
    mock_audit_bin_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then',
                f"  printf 'Usage of conformance-audit:\\n  -manifest string\\n"
                f"  -output string\\n{pid_help_line}  -profile string\\n"
                f"  -stages string\\n  -target string\\n  -url string\\n' >&2",
                "  exit 2",
                "fi",
                reject_pid_block,
                f'printf \'%s\\n\' "$@" > "{captured_args_path}"',
            ]
        ),
        encoding="utf-8",
    )
    mock_audit_bin_path.chmod(0o755)
    return script_path, mock_audit_bin_path, stub_server_path, captured_args_path


def test_run_conformance_audit_script_omits_pid_when_unsupported(
    tmp_path: Path,
) -> None:
    script_path, mock_bin, stub_server, captured_args = _create_mock_sandbox(
        tmp_path,
        has_pid_flag_support=False,
    )
    port = _allocate_free_tcp_port()
    env = {
        **os.environ,
        "CONFORMANCE_AUDIT_BIN": str(mock_bin),
        "CONFORMANCE_PYTHON_BIN": sys.executable,
        "CONFORMANCE_APP_CMD": f"{sys.executable} {stub_server}",
        "SKIP_APP_BUILD": "true",
        "PORT": str(port),
        "GEOIP_DATABASE_PATH": str(tmp_path / "stub.mmdb"),
        "JWT_PRIVATE_KEY": "stub-private-key",
        "JWT_PUBLIC_KEY": "stub-public-key",
    }
    res = subprocess.run(  # noqa: S603
        [
            str(script_path),
            "--stages",
            "sast,graph,pentest,load",
            "--profile",
            "baseline",
            "--output",
            "reports",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "flag provided but not defined: -pid" not in res.stderr
    received_args = captured_args.read_text(encoding="utf-8").strip().splitlines()
    assert "--pid" not in received_args
    assert f"http://127.0.0.1:{port}" in received_args


def test_run_conformance_audit_script_passes_pid_when_supported(
    tmp_path: Path,
) -> None:
    script_path, mock_bin, stub_server, captured_args = _create_mock_sandbox(
        tmp_path,
        has_pid_flag_support=True,
    )
    port = _allocate_free_tcp_port()
    env = {
        **os.environ,
        "CONFORMANCE_AUDIT_BIN": str(mock_bin),
        "CONFORMANCE_PYTHON_BIN": sys.executable,
        "CONFORMANCE_APP_CMD": f"{sys.executable} {stub_server}",
        "SKIP_APP_BUILD": "true",
        "PORT": str(port),
        "GEOIP_DATABASE_PATH": str(tmp_path / "stub.mmdb"),
        "JWT_PRIVATE_KEY": "stub-private-key",
        "JWT_PUBLIC_KEY": "stub-public-key",
    }
    res = subprocess.run(  # noqa: S603
        [
            str(script_path),
            "--stages",
            "sast,graph,pentest,load",
            "--profile",
            "baseline",
            "--output",
            "reports",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    received_args = captured_args.read_text(encoding="utf-8").strip().splitlines()
    assert "--pid" in received_args
    pid_val = received_args[received_args.index("--pid") + 1]
    assert pid_val.isdigit()


@pytest.mark.asyncio
async def test_auth_service_register_maps_duplicate_key_error_to_conflict() -> None:
    class _DuplicateKeyError(Exception):
        code = 11000

    repo = MagicMock()
    repo.find_by_email = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=_DuplicateKeyError("E11000 duplicate key"))

    bulkhead = MagicMock()
    bulkhead.hash = AsyncMock(return_value="hashed-password")

    service = AuthService(
        user_repository=repo,
        password_hash_bulkhead=bulkhead,
        config_service={},
    )
    service.user_repository = repo

    with pytest.raises(ConflictException, match="Email already in use"):
        await service.register(
            RegisterDto(email="race@example.test", password="Password123!")
        )


@pytest.mark.asyncio
async def test_authenticated_socketio_app_rejects_unauthenticated_ws_upgrade() -> None:
    inner = AsyncMock()
    authenticator = MagicMock()
    authenticator.verify_access_token = AsyncMock()

    app = AuthenticatedSocketIOApp(
        inner,
        authenticator=authenticator,
        allow_query_token=False,
    )

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/",
        "headers": [],
        "query_string": b"EIO=4&transport=websocket",
    }
    await app(scope, receive, send)  # type: ignore[arg-type]

    assert sent == [{"type": "websocket.close", "code": 1008}]
    inner.assert_not_awaited()
    authenticator.verify_access_token.assert_not_awaited()
