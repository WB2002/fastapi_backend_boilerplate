#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

resolve_audit_bin() {
  if [[ -n "${CONFORMANCE_AUDIT_BIN:-}" && -x "${CONFORMANCE_AUDIT_BIN}" ]]; then
    echo "${CONFORMANCE_AUDIT_BIN}"
    return 0
  fi

  local candidates=(
    "${ROOT_DIR}/.conformance-runner/bin/conformance-audit"
    "${ROOT_DIR}/../backend_security_and_conformance_tests/bin/conformance-audit"
    "${ROOT_DIR}/../backend_boilerplate_conformance_tests/bin/conformance-audit"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done

  if command -v conformance-audit >/dev/null 2>&1; then
    command -v conformance-audit
    return 0
  fi

  local src_dirs=(
    "${ROOT_DIR}/.conformance-runner"
    "${ROOT_DIR}/../backend_security_and_conformance_tests"
    "${ROOT_DIR}/../backend_boilerplate_conformance_tests"
  )
  if command -v go >/dev/null 2>&1; then
    for src_dir in "${src_dirs[@]}"; do
      if [[ -f "${src_dir}/cmd/conformance-audit/main.go" ]]; then
        mkdir -p "${src_dir}/bin"
        (cd "${src_dir}" && go build -o bin/conformance-audit ./cmd/conformance-audit) >&2
        echo "${src_dir}/bin/conformance-audit"
        return 0
      fi
    done
  fi

  echo "Error: conformance-audit binary not found. Set CONFORMANCE_AUDIT_BIN or build ../backend_boilerplate_conformance_tests." >&2
  return 1
}

AUDIT_BIN="$(resolve_audit_bin)"

STAGES="sast,graph,pentest,load"
HAS_URL=false

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  arg="${args[$i]}"
  case "${arg}" in
    --stages=*)
      STAGES="${arg#--stages=}"
      ;;
    --stages)
      if ((i + 1 < ${#args[@]})); then
        STAGES="${args[$((i + 1))]}"
      fi
      ;;
    --url=*|--url)
      HAS_URL=true
      ;;
  esac
done

is_static_only() {
  local remaining="$1"
  local stage=""
  while [[ -n "${remaining}" ]]; do
    if [[ "${remaining}" == *,* ]]; then
      stage="${remaining%%,*}"
      remaining="${remaining#*,}"
    else
      stage="${remaining}"
      remaining=""
    fi
    case "${stage}" in
      sast|graph) ;;
      *) return 1 ;;
    esac
  done
  return 0
}

if is_static_only "${STAGES}" && [[ "${HAS_URL}" == "false" ]]; then
  exec "${AUDIT_BIN}" \
    --target "${ROOT_DIR}" \
    --manifest "${ROOT_DIR}/boilerplate.manifest.json" \
    "$@"
fi

if [[ "${SKIP_APP_BOOT:-false}" == "true" ]]; then
  exec "${AUDIT_BIN}" \
    --target "${ROOT_DIR}" \
    --manifest "${ROOT_DIR}/boilerplate.manifest.json" \
    "$@"
fi

USER_INBOUND_MAX_IN_FLIGHT="${INBOUND_MAX_IN_FLIGHT:-}"
USER_INBOUND_LOAD_LEVEL_MAX_QUEUE="${INBOUND_LOAD_LEVEL_MAX_QUEUE:-}"
USER_INBOUND_LOAD_LEVEL_MAX_WAIT_MS="${INBOUND_LOAD_LEVEL_MAX_WAIT_MS:-}"
USER_AUTH_PASSWORD_HASH_MAX_CONCURRENT="${AUTH_PASSWORD_HASH_MAX_CONCURRENT:-}"
USER_AUTH_PASSWORD_HASH_MAX_WAIT_MS="${AUTH_PASSWORD_HASH_MAX_WAIT_MS:-}"
USER_REQUEST_TIMEOUT_MS="${REQUEST_TIMEOUT_MS:-}"
USER_COUNTRY_ACCESS_REFRESH_INTERVAL_MS="${COUNTRY_ACCESS_REFRESH_INTERVAL_MS:-}"

if [[ -f "${ROOT_DIR}/.env.conformance.example" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env.conformance.example"
  set +a
fi

export CONFORMANCE_HARNESS_TOKEN="${CONFORMANCE_HARNESS_TOKEN:-conformance-harness-secret-token}"
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-http://127.0.0.1:3000,http://localhost:3000}"
export RATE_LIMIT_GLOBAL_IP_LIMIT="${RATE_LIMIT_GLOBAL_IP_LIMIT:-100000}"
export PORT="${PORT:-3000}"
export MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:27017/fastapi-conformance}"
export REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
export REDIS_PORT="${REDIS_PORT:-6379}"
export COOKIE_SECRET="${COOKIE_SECRET:-conformance-cookie-secret-32-chars-min}"
export ACCESS_TOKEN_TTL=900
export SESSION_TTL=604800
export HCAPTCHA_SECRET="${HCAPTCHA_SECRET:-conformance-secret}"
export OTEL_ENABLED=false
export APP_METRICS_ENABLED="${APP_METRICS_ENABLED:-false}"
export FEATURE_FLAG_METRICS="${FEATURE_FLAG_METRICS:-false}"
export COUNTRY_ACCESS_REFRESH_INTERVAL_MS="${USER_COUNTRY_ACCESS_REFRESH_INTERVAL_MS:-60000}"
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
export BACKGROUND_JOBS_ENABLED=false
export BACKGROUND_WORKER_ENABLED=false
export INBOUND_BULKHEAD_ENABLED="${INBOUND_BULKHEAD_ENABLED:-false}"
export INBOUND_PRIORITY_SHEDDING_ENABLED="${INBOUND_PRIORITY_SHEDDING_ENABLED:-false}"
export INBOUND_LOAD_LEVELING_ENABLED="${INBOUND_LOAD_LEVELING_ENABLED:-false}"
export INBOUND_MAX_IN_FLIGHT="${USER_INBOUND_MAX_IN_FLIGHT:-256}"
export INBOUND_LOAD_LEVEL_MAX_QUEUE="${USER_INBOUND_LOAD_LEVEL_MAX_QUEUE:-256}"
export INBOUND_LOAD_LEVEL_MAX_WAIT_MS="${USER_INBOUND_LOAD_LEVEL_MAX_WAIT_MS:-5000}"
export AUTH_PASSWORD_HASH_MAX_CONCURRENT="${USER_AUTH_PASSWORD_HASH_MAX_CONCURRENT:-2}"
export AUTH_PASSWORD_HASH_MAX_WAIT_MS="${USER_AUTH_PASSWORD_HASH_MAX_WAIT_MS:-8000}"
export REQUEST_TIMEOUT_MS="${USER_REQUEST_TIMEOUT_MS:-10000}"
export PYTHONUNBUFFERED=1

TMP_RUNTIME_DIR=""
APP_PID=""

cleanup() {
  if [[ -n "${APP_PID}" ]] && kill -0 "${APP_PID}" 2>/dev/null; then
    kill "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
  fi
  if [[ -n "${TMP_RUNTIME_DIR}" && -d "${TMP_RUNTIME_DIR}" ]]; then
    rm -rf "${TMP_RUNTIME_DIR}"
  fi
}
trap cleanup EXIT

ensure_tmp_runtime_dir() {
  if [[ -z "${TMP_RUNTIME_DIR}" ]]; then
    TMP_RUNTIME_DIR="$(mktemp -d)"
  fi
}

resolve_python_bin() {
  if [[ -n "${CONFORMANCE_PYTHON_BIN:-}" && -x "${CONFORMANCE_PYTHON_BIN}" ]]; then
    echo "${CONFORMANCE_PYTHON_BIN}"
    return 0
  fi
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    echo "${ROOT_DIR}/.venv/bin/python"
    return 0
  fi
  command -v python3
}

if [[ "${SKIP_APP_BUILD:-false}" != "true" ]]; then
  if command -v uv >/dev/null 2>&1; then
    uv sync --frozen
  fi
fi

PYTHON_BIN="$(resolve_python_bin)"

if [[ -z "${GEOIP_DATABASE_PATH:-}" ]]; then
  ensure_tmp_runtime_dir
  export GEOIP_DATABASE_PATH="${TMP_RUNTIME_DIR}/conformance.mmdb"
  "${PYTHON_BIN}" -c '
import sys
def enc_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return bytes([(2 << 5) | len(b)]) + b
def enc_uint16(v: int) -> bytes:
    if v == 0:
        return bytes([5 << 5])
    if v <= 255:
        return bytes([(5 << 5) | 1, v])
    return bytes([(5 << 5) | 2, (v >> 8) & 0xFF, v & 0xFF])
tree = bytes([0, 0, 1, 0, 0, 1])
sep = bytes(16)
marker = b"\xab\xcd\xefMaxMind.com"
meta = b"".join([
    bytes([(7 << 5) | 7]),
    enc_str("binary_format_major_version"), enc_uint16(2),
    enc_str("binary_format_minor_version"), enc_uint16(0),
    enc_str("build_epoch"), bytes([0x00, 0x02]),
    enc_str("database_type"), enc_str("GeoLite2-Country"),
    enc_str("ip_version"), enc_uint16(4),
    enc_str("node_count"), bytes([(6 << 5) | 1, 1]),
    enc_str("record_size"), enc_uint16(24),
])
with open(sys.argv[1], "wb") as f:
    f.write(tree + sep + marker + meta)
' "${GEOIP_DATABASE_PATH}"
fi

if [[ -z "${JWT_PRIVATE_KEY:-}" || -z "${JWT_PUBLIC_KEY:-}" ]]; then
  ensure_tmp_runtime_dir
  openssl genrsa -out "${TMP_RUNTIME_DIR}/private.pem" 2048 >/dev/null 2>&1
  openssl rsa -in "${TMP_RUNTIME_DIR}/private.pem" -pubout -out "${TMP_RUNTIME_DIR}/public.pem" >/dev/null 2>&1
  export JWT_PRIVATE_KEY
  export JWT_PUBLIC_KEY
  JWT_PRIVATE_KEY="$(cat "${TMP_RUNTIME_DIR}/private.pem")"
  JWT_PUBLIC_KEY="$(cat "${TMP_RUNTIME_DIR}/public.pem")"
fi

CONFORMANCE_APP_CMD="${CONFORMANCE_APP_CMD:-}"
if [[ -n "${CONFORMANCE_APP_CMD}" ]]; then
  # Allow tests to inject a stub server command
  # shellcheck disable=SC2086
  ${CONFORMANCE_APP_CMD} > /tmp/fastapi-conformance.log 2>&1 &
else
  "${PYTHON_BIN}" -m uvicorn app.main:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT}" \
    --proxy-headers \
    --forwarded-allow-ips="*" \
    > /tmp/fastapi-conformance.log 2>&1 &
fi
APP_PID=$!

HEALTH_URL="http://127.0.0.1:${PORT}/health/live"
READY=false
for _ in $(seq 1 30); do
  if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "Error: FastAPI app exited prematurely. Logs:" >&2
    cat /tmp/fastapi-conformance.log >&2 || true
    exit 1
  fi
  HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' "${HEALTH_URL}" || true)"
  if [[ "${HTTP_CODE}" == "200" ]]; then
    READY=true
    break
  fi
  sleep 1
done

if [[ "${READY}" != "true" ]]; then
  echo "Error: Timed out waiting for ${HEALTH_URL}. Logs:" >&2
  cat /tmp/fastapi-conformance.log >&2 || true
  exit 1
fi

# Warm up Python routes, middleware, and memory arenas before RSS baseline sampling.
"${PYTHON_BIN}" -c '
import concurrent.futures
import os
import urllib.request

port = os.environ.get("PORT", "3000")
paths = ["/health/live", "/"]

def hit(idx: int) -> None:
    url = f"http://127.0.0.1:{port}{paths[idx % len(paths)]}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            resp.read()
    except Exception:
        pass

with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
    list(pool.map(hit, range(400)))
' || true
sleep 1

export CONFORMANCE_TARGET_PID="${APP_PID}"

# Static scanners should not treat the local virtualenv as application source.
if [[ -d "${ROOT_DIR}/.venv" ]]; then
  rm -rf "${ROOT_DIR}/.venv"
fi

AUDIT_HELP="$("${AUDIT_BIN}" --help 2>&1 || true)"
EXTRA_AUDIT_ARGS=()
if grep -qE '(^|[[:space:]])--?pid([[:space:]=]|$)' <<< "${AUDIT_HELP}"; then
  EXTRA_AUDIT_ARGS+=(--pid "${APP_PID}")
fi

"${AUDIT_BIN}" \
  --target "${ROOT_DIR}" \
  --manifest "${ROOT_DIR}/boilerplate.manifest.json" \
  --url "http://127.0.0.1:${PORT}" \
  ${EXTRA_AUDIT_ARGS[@]+"${EXTRA_AUDIT_ARGS[@]}"} \
  "$@"
