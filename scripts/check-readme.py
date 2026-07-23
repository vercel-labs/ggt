#!/usr/bin/env python3
"""Check that README.rst renders with the renderer PyPI uses.

PyPI rejects uploads whose long_description fails to render, so a
broken README breaks the release workflow.  readme_renderer's own CLI
crashes with an unrelated traceback on render failure, hence this
wrapper.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import readme_renderer.rst

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    source = (ROOT / "README.rst").read_text(encoding="utf-8")
    stream = io.StringIO()
    if readme_renderer.rst.render(source, stream=stream) is None:
        sys.stderr.write(stream.getvalue())
        sys.stderr.write(
            "README.rst does not render as reStructuredText; "
            "PyPI would reject it.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
