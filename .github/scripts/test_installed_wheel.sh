#!/bin/sh
set -eu

wheel_count=0
wheel_path=
for path in dist/*.whl; do
    if [ ! -e "$path" ]; then
        continue
    fi
    wheel_count=$((wheel_count + 1))
    wheel_path=$path
done

if [ "$wheel_count" -ne 1 ]; then
    printf 'expected exactly one wheel in dist/, found %s\n' "$wheel_count" >&2
    exit 1
fi

temp_dir=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
test_root=$(mktemp -d "$temp_dir/ggt-installed-wheel.XXXXXX")
wheel_path=$(pwd)/$wheel_path
cp -R tests "$test_root/tests"
cp -R fixtures "$test_root/fixtures"

uv run \
    --no-cache \
    --only-group=test \
    --locked \
    --isolated \
    --with "$wheel_path" \
    python -m unittest discover -s "$test_root/tests"
