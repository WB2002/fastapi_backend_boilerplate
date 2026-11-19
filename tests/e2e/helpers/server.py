"""Live ASGI/Uvicorn server, subprocess runner, and JSON/CloudWatch EMF log parser."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.e2e.helpers.containers import resolve_project_root

_LIVE_RUNNER_SCRIPT = """
import importlib
import inspect
import os
import sys

def _bootstrap() -> None:
    try:
        inst = importlib.import_module("app.instrumentation")
        for fn_name in (
            "register_instrumentation",
            "setup_instrumentation",
            "init_instrumentation",
            "configure_instrumentation",
        ):
            fn = getattr(inst, fn_name, None)
            if callable(fn):
                fn()
                break
    except ModuleNotFoundError:
        pass

    main_mod = importlib.import_module("app.main")
    factory = getattr(main_mod, "create_app", None)
    if callable(factory):
        app_obj = factory()
    else:
        app_obj = getattr(main_mod, "app")
    if inspect.isawaitable(app_obj):
        import asyncio
        app_obj = asyncio.run(app_obj)

    import uvicorn
    host = sys.argv[1]
    port = int(sys.argv[2])
    keep_alive_ms = int(os.environ.get("HTTP_KEEP_ALIVE_TIMEOUT_MS", "65000"))
    keep_alive_sec = max(1, keep_alive_ms // 1000)
    node_env = os.environ.get("NODE_ENV", "test")
    log_level = "warning" if node_env == "production" else "info"
    config = uvicorn.Config(
        app_obj,
        host=host,
        port=port,
        log_level=log_level,
        timeout_keep_alive=keep_alive_sec,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()

if __name__ == "__main__":
    _bootstrap()
"""


def find_free_port(host: str = "127.0.0.1") -> int:
    """Bind an ephemeral loopback TCP port and return its port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        addr: tuple[str, int] = sock.getsockname()
        return int(addr[1])


def resolve_python_executable(project_root: Path | None = None) -> str:
    """Resolve the best Python interpreter for launching app/worker subprocesses."""
    root = project_root or resolve_project_root()
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    for candidate in ("python3.12", "python3.13"):
        found = shutil.which(candidate)
        if found is not None:
            return found
    return sys.executable


def _build_subprocess_env(
    env: Mapping[str, str | None] | None,
    *,
    project_root: Path,
    inherit_env: bool,
) -> dict[str, str]:
    if inherit_env:
        out = dict(os.environ)
    else:
        out = {}
        for base_key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER"):
            val = os.environ.get(base_key)
            if val is not None:
                out[base_key] = val
    existing_pp = out.get("PYTHONPATH", "")
    root_str = str(project_root)
    if existing_pp:
        parts = existing_pp.split(os.pathsep)
        if root_str not in parts:
            out["PYTHONPATH"] = f"{root_str}{os.pathsep}{existing_pp}"
    else:
        out["PYTHONPATH"] = root_str
    out.setdefault("PYTHONUNBUFFERED", "1")
    if env is not None:
        for key, val in env.items():
            if val is None:
                out.pop(key, None)
            else:
                out[key] = str(val)
    return out


def run_python_snippet(
    code: str,
    *,
    env: Mapping[str, str | None] | None = None,
    cwd: Path | None = None,
    timeout: float = 15.0,
    inherit_env: bool = False,
    python_exe: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an isolated Python snippet with explicit environment control."""
    root = cwd or resolve_project_root()
    exe = python_exe or resolve_python_executable(root)
    proc_env = _build_subprocess_env(env, project_root=root, inherit_env=inherit_env)
    return subprocess.run(  # noqa: S603
        [exe, "-c", code],
        cwd=str(root),
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_app_subprocess(
    env: Mapping[str, str | None],
    *,
    code: str | None = None,
    entrypoint: str | None = None,
    cwd: Path | None = None,
    timeout: float = 15.0,
    inherit_env: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an isolated app boot/validation subprocess (e.g. startup failure tests)."""
    root = cwd or resolve_project_root()
    exe = resolve_python_executable(root)
    proc_env = _build_subprocess_env(env, project_root=root, inherit_env=inherit_env)
    if code is not None:
        cmd = [exe, "-c", code]
    elif entrypoint is not None:
        cmd = [exe, "-m", entrypoint]
    else:
        cmd = [
            exe,
            "-c",
            (
                "import importlib; "
                "m = importlib.import_module('app.main'); "
                "f = getattr(m, 'create_app', None); "
                "f() if callable(f) else getattr(m, 'app')"
            ),
        ]
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(root),
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def create_asgi_app(env: Mapping[str, str | None] | None = None) -> Any:
    """Dynamically construct the FastAPI ASGI app in-process without static imports."""
    if env is not None:
        for key, val in env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(val)
    main_mod = importlib.import_module("app.main")
    factory = getattr(main_mod, "create_app", None)
    if callable(factory):
        return factory()
    return main_mod.app


@dataclass(frozen=True, slots=True)
class EmfRecord:
    """Parsed CloudWatch Embedded Metric Format (``_aws``) log record."""

    raw: dict[str, Any]
    timestamp_ms: int
    namespace: str
    dimensions: dict[str, str]
    metrics: dict[str, float | int]
    metric_units: dict[str, str]
    properties: dict[str, Any]


def _iter_lines(lines: Sequence[str] | str) -> list[str]:
    if isinstance(lines, str):
        return lines.splitlines()
    out: list[str] = []
    for item in lines:
        out.extend(item.splitlines())
    return out


def parse_json_logs(lines: Sequence[str] | str) -> list[dict[str, Any]]:
    """Parse single-line JSON log entries, ignoring plain-text/ANSI lines."""
    records: list[dict[str, Any]] = []
    for line in _iter_lines(lines):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _parse_single_emf(obj: dict[str, Any]) -> EmfRecord | None:
    aws_meta = obj.get("_aws")
    if not isinstance(aws_meta, dict):
        return None
    cw_metrics = aws_meta.get("CloudWatchMetrics")
    if not isinstance(cw_metrics, list) or not cw_metrics:
        return None
    first_group = cw_metrics[0]
    if not isinstance(first_group, dict):
        return None

    namespace = str(first_group.get("Namespace", ""))
    raw_ts = aws_meta.get("Timestamp")
    try:
        timestamp_ms = int(raw_ts or 0)
    except (TypeError, ValueError):
        timestamp_ms = 0

    dimensions: dict[str, str] = {}
    dim_sets = first_group.get("Dimensions")
    if isinstance(dim_sets, list) and dim_sets and isinstance(dim_sets[0], list):
        for dim_name in dim_sets[0]:
            if isinstance(dim_name, str) and dim_name in obj:
                dimensions[dim_name] = str(obj[dim_name])

    metrics: dict[str, float | int] = {}
    metric_units: dict[str, str] = {}
    metric_defs = first_group.get("Metrics")
    if isinstance(metric_defs, list):
        for mdef in metric_defs:
            if not isinstance(mdef, dict):
                continue
            name = mdef.get("Name")
            if not isinstance(name, str):
                continue
            unit = mdef.get("Unit")
            if isinstance(unit, str):
                metric_units[name] = unit
            val = obj.get(name)
            if isinstance(val, int | float) and not isinstance(val, bool):
                metrics[name] = val

    excluded = {"_aws", *dimensions.keys(), *metrics.keys()}
    properties = {k: v for k, v in obj.items() if k not in excluded}

    return EmfRecord(
        raw=obj,
        timestamp_ms=timestamp_ms,
        namespace=namespace,
        dimensions=dimensions,
        metrics=metrics,
        metric_units=metric_units,
        properties=properties,
    )


def parse_emf_records(lines: Sequence[str] | str) -> list[EmfRecord]:
    """Extract CloudWatch EMF records from stdout/stderr lines."""
    records: list[EmfRecord] = []
    for obj in parse_json_logs(lines):
        parsed = _parse_single_emf(obj)
        if parsed is not None:
            records.append(parsed)
    return records


class LiveAppServer:
    """Manages an isolated FastAPI Uvicorn subprocess on an ephemeral loopback port."""

    def __init__(
        self,
        env: Mapping[str, str | None],
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        cwd: Path | None = None,
        entrypoint: str | None = None,
        inherit_env: bool = False,
        ready_path: str = "/health/live",
        ready_timeout: float = 20.0,
        accepted_ready_statuses: tuple[int, ...] = (200, 503),
    ) -> None:
        self._host = host
        self._port = port if port and port > 0 else find_free_port(host)
        self._cwd = cwd or resolve_project_root()
        self._entrypoint = entrypoint
        self._inherit_env = inherit_env
        self._ready_path = ready_path
        self._ready_timeout = ready_timeout
        self._accepted_ready_statuses = accepted_ready_statuses

        merged_env = dict(env)
        merged_env["PORT"] = str(self._port)
        self._env = merged_env

        self._proc: subprocess.Popen[str] | None = None
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    @property
    def host(self) -> str:
        """Bound host address."""
        return self._host

    @property
    def port(self) -> int:
        """Bound TCP port."""
        return self._port

    @property
    def base_url(self) -> str:
        """Base HTTP URL for this server."""
        return f"http://{self._host}:{self._port}"

    @property
    def pid(self) -> int | None:
        """Subprocess PID if started."""
        return self._proc.pid if self._proc is not None else None

    @property
    def returncode(self) -> int | None:
        """Subprocess exit code if terminated."""
        if self._proc is None:
            return None
        return self._proc.poll()

    def url(self, path: str) -> str:
        """Build an absolute URL for ``path``."""
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _drain_stream(self, stream: Any, target: list[str]) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                cleaned = line.rstrip("\r\n")
                with self._lock:
                    target.append(cleaned)
        finally:
            stream.close()

    def start_sync(self) -> None:
        """Launch the server subprocess and wait until ``ready_path`` responds."""
        if self._proc is not None:
            return
        exe = resolve_python_executable(self._cwd)
        proc_env = _build_subprocess_env(
            self._env,
            project_root=self._cwd,
            inherit_env=self._inherit_env,
        )
        if self._entrypoint is not None:
            cmd = [exe, "-m", self._entrypoint]
        else:
            cmd = [exe, "-c", _LIVE_RUNNER_SCRIPT, self._host, str(self._port)]

        self._proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(self._cwd),
            env=proc_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        t_out = threading.Thread(
            target=self._drain_stream,
            args=(self._proc.stdout, self._stdout_lines),
            daemon=True,
        )
        t_err = threading.Thread(
            target=self._drain_stream,
            args=(self._proc.stderr, self._stderr_lines),
            daemon=True,
        )
        t_out.start()
        t_err.start()
        self._threads = [t_out, t_err]

        try:
            self.wait_until_ready_sync()
        except Exception:
            self.stop_sync(timeout=3.0)
            raise

    async def start(self) -> None:
        """Async wrapper around :meth:`start_sync`."""
        await asyncio.to_thread(self.start_sync)

    def _probe_once(self, target_url: str) -> int | None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(target_url, method="GET")  # noqa: S310
        try:
            with opener.open(req, timeout=1.0) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as err:
            return int(err.code)
        except (urllib.error.URLError, OSError):
            return None

    def wait_until_ready_sync(
        self,
        path: str | None = None,
        timeout: float | None = None,
        accepted_statuses: tuple[int, ...] | None = None,
    ) -> int:
        """Poll ``path`` until it returns one of ``accepted_statuses``."""
        target_path = path or self._ready_path
        limit = self._ready_timeout if timeout is None else timeout
        accepted = accepted_statuses or self._accepted_ready_statuses
        target_url = self.url(target_path)
        deadline = time.monotonic() + limit

        while time.monotonic() < deadline:
            if self._proc is not None:
                rc = self._proc.poll()
                if rc is not None:
                    logs = "\n".join(self.get_output_lines())
                    msg = (
                        f"Server exited prematurely with code {rc} "
                        f"before {target_url} became ready.\nLogs:\n{logs}"
                    )
                    raise RuntimeError(msg)
            status = self._probe_once(target_url)
            if status is not None and status in accepted:
                return status
            time.sleep(0.05)

        logs = "\n".join(self.get_output_lines())
        msg = (
            f"Timed out after {limit}s waiting for {target_url} "
            f"(accepted={accepted}).\nLogs:\n{logs}"
        )
        raise TimeoutError(msg)

    async def wait_until_ready(
        self,
        path: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        accepted_statuses: tuple[int, ...] | None = None,
    ) -> int:
        """Async wrapper around :meth:`wait_until_ready_sync`."""
        return await asyncio.to_thread(
            self.wait_until_ready_sync,
            path,
            timeout,
            accepted_statuses,
        )

    def send_signal(self, sig: signal.Signals = signal.SIGTERM) -> None:
        """Send an OS signal (e.g., ``SIGTERM``) to the live server process."""
        if self._proc is None:
            msg = "Server process has not been started"
            raise RuntimeError(msg)
        if self._proc.poll() is None:
            self._proc.send_signal(sig)

    def wait_for_exit_sync(self, timeout: float = 15.0) -> int:
        """Wait for the subprocess to exit and join stdout/stderr reader threads."""
        if self._proc is None:
            msg = "Server process has not been started"
            raise RuntimeError(msg)
        rc = self._proc.wait(timeout=timeout)
        for thread in self._threads:
            thread.join(timeout=2.0)
        return int(rc)

    async def wait_for_exit(self, timeout: float = 15.0) -> int:  # noqa: ASYNC109
        """Async wrapper around :meth:`wait_for_exit_sync`."""
        return await asyncio.to_thread(self.wait_for_exit_sync, timeout)

    def stop_sync(self, timeout: float = 10.0) -> int:
        """Gracefully terminate the server (SIGTERM -> SIGKILL fallback)."""
        if self._proc is None:
            return 0
        rc = self._proc.poll()
        if rc is None:
            self._proc.terminate()
            try:
                rc = self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                rc = self._proc.wait(timeout=5.0)
        for thread in self._threads:
            thread.join(timeout=2.0)
        return int(rc)

    async def stop(self, timeout: float = 10.0) -> int:  # noqa: ASYNC109
        """Async wrapper around :meth:`stop_sync`."""
        return await asyncio.to_thread(self.stop_sync, timeout)

    def get_stdout_lines(self) -> list[str]:
        """Return a snapshot of captured stdout lines."""
        with self._lock:
            return list(self._stdout_lines)

    def get_stderr_lines(self) -> list[str]:
        """Return a snapshot of captured stderr lines."""
        with self._lock:
            return list(self._stderr_lines)

    def get_output_lines(self) -> list[str]:
        """Return combined stdout and stderr lines."""
        with self._lock:
            return [*self._stdout_lines, *self._stderr_lines]

    def get_json_logs(self) -> list[dict[str, Any]]:
        """Parse JSON log objects from stdout and stderr."""
        return parse_json_logs(self.get_output_lines())

    def get_emf_records(self) -> list[EmfRecord]:
        """Parse CloudWatch EMF ``_aws`` records from stdout and stderr."""
        return parse_emf_records(self.get_output_lines())

    async def wait_for_log(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 5.0,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Wait until a JSON log entry satisfies ``predicate``."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for entry in self.get_json_logs():
                if predicate(entry):
                    return entry
            await asyncio.sleep(0.05)
        logs = "\n".join(self.get_output_lines())
        msg = (
            f"Timed out after {timeout}s waiting for matching JSON log.\nLogs:\n{logs}"
        )
        raise TimeoutError(msg)

    async def wait_for_emf(
        self,
        predicate: Callable[[EmfRecord], bool],
        *,
        timeout: float = 5.0,  # noqa: ASYNC109
    ) -> EmfRecord:
        """Wait until a CloudWatch EMF record satisfies ``predicate``."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for rec in self.get_emf_records():
                if predicate(rec):
                    return rec
            await asyncio.sleep(0.05)
        logs = "\n".join(self.get_output_lines())
        msg = (
            f"Timed out after {timeout}s waiting for matching EMF record.\n"
            f"Logs:\n{logs}"
        )
        raise TimeoutError(msg)

    def __enter__(self) -> LiveAppServer:
        self.start_sync()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop_sync()

    async def __aenter__(self) -> LiveAppServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


__all__ = [
    "EmfRecord",
    "LiveAppServer",
    "create_asgi_app",
    "find_free_port",
    "parse_emf_records",
    "parse_json_logs",
    "resolve_python_executable",
    "run_app_subprocess",
    "run_python_snippet",
]
