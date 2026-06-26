# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


def _main() -> None:
    import click  # noqa: PLC0415
    from ggt._internal import cli  # noqa: PLC0415

    click.command(cli.test)()


if __name__ == "__main__":
    _main()
