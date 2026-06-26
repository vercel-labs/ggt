#!/bin/sh

set -e

ROOT=$(git rev-parse --show-toplevel)
SNAPSHOT=$(mktemp -d "${TMPDIR:-/tmp}/ggt-index.XXXXXX")
VENV_BIN="$ROOT/.venv.uv/bin"

cleanup() {
  rm -rf "$SNAPSHOT"
}
trap cleanup EXIT INT TERM

git checkout-index --all --force --prefix="$SNAPSHOT/"
ln -s "$ROOT/.git" "$SNAPSHOT/.git"

if [ ! -x "$VENV_BIN/python" ] || [ ! -x "$VENV_BIN/ruff" ] || [ ! -x "$VENV_BIN/ggt" ]; then
  echo "Project environment is missing. Run: uv sync --dev" >&2
  exit 1
fi

cd "$SNAPSHOT"

PATH="$VENV_BIN:$PATH" "$VENV_BIN/ruff" format --check src tests
PATH="$VENV_BIN:$PATH" "$VENV_BIN/ruff" check src/ggt pyproject.toml
PYTHONPATH="$SNAPSHOT/src" PATH="$VENV_BIN:$PATH" \
  "$VENV_BIN/ggt" tests --output-format simple
