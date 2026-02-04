#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IBAPI_PYTHONCLIENT="${1:-${IBAPI_PYTHONCLIENT:-}}"
WEBULL_VERSION="${WEBULL_VERSION:-1.1.0}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: https://astral.sh/uv" >&2
  exit 1
fi

if [[ -z "${IBAPI_PYTHONCLIENT}" ]]; then
  cat >&2 <<'EOF'
Usage:
  ./bootstrap.sh /path/to/ibapi/pythonclient

Or set:
  IBAPI_PYTHONCLIENT=/path/to/ibapi/pythonclient ./bootstrap.sh
EOF
  exit 1
fi

if [[ ! -d "${IBAPI_PYTHONCLIENT}" ]]; then
  echo "IB API pythonclient not found: ${IBAPI_PYTHONCLIENT}" >&2
  exit 1
fi

cd "${ROOT_DIR}"

if [[ ! -d ".venv" ]]; then
  uv venv
fi

uv pip install -e . --python .venv
uv pip install --python .venv "${IBAPI_PYTHONCLIENT}"
uv pip install --python .venv --no-deps "webull-openapi-python-sdk==${WEBULL_VERSION}"

cat <<EOF
Bootstrap complete.

Run:
  uv run --no-sync python -m janus.server
  uv run --no-sync python -m janus.client
EOF
