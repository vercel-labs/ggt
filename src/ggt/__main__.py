# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


def _main() -> None:
    from ggt._internal import cov  # noqa: PLC0415

    with cov.CoverageConfig.enable_coverage_if_requested():
        from ggt._internal import cli  # noqa: PLC0415

        cli.main()


if __name__ == "__main__":
    _main()
