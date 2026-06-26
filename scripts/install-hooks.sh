#!/bin/sh

set -e

ROOT=$(git rev-parse --show-toplevel)

git -C "$ROOT" config core.hooksPath .githooks
chmod +x "$ROOT/.githooks/pre-commit" "$ROOT/.githooks/pre-push"
chmod +x "$ROOT/scripts/run-index-checks.sh"

echo "Git hooks installed from .githooks"
