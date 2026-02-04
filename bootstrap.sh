#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBULL_VERSION="${WEBULL_VERSION:-1.1.0}"
WEBULL_DEPS=(
  cachetools
  cryptography
  grpcio
  grpcio-tools
  jmespath
  paho-mqtt
)

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: https://astral.sh/uv" >&2
  exit 1
fi

cd "${ROOT_DIR}"

if [[ ! -d ".venv" ]]; then
  uv venv
fi

uv pip install -e . --python .venv
uv pip install --python .venv "ib_async"
uv pip install --python .venv --no-deps "webull-openapi-python-sdk==${WEBULL_VERSION}"
uv pip install --python .venv "${WEBULL_DEPS[@]}"

cat <<EOF
Bootstrap complete.

Run:
  uv run --no-sync python -m janus.server
  uv run --no-sync python -m janus.client
EOF
