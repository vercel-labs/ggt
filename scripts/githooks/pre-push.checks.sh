#!/usr/bin/env sh
set -eu

printf '\n'
# Hook streams are often pipes (e.g. under gh or GUI clients), which
# would make lograil fall back to plain line output; force the fancy
# renderer, honoring an explicit override from the environment.
if FORCE_COLOR=1 CLICOLOR_FORCE=1 PY_COLORS=1 \
    LOGRAIL_OUTPUT="${LOGRAIL_OUTPUT:-fancy}" uv run poe pre-push; then
    status=0
else
    status=$?
fi
printf '\n'
exit "$status"
