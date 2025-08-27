def _main() -> None:
    import click  # noqa: PLC0415
    from geltest._internal import cli  # noqa: PLC0415

    click.command(cli.test)()


if __name__ == "__main__":
    _main()
