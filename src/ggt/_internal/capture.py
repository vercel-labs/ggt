# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.

"""Per-test capture of stdout and stderr.

Captures at the file-descriptor level (like pytest's default
``--capture=fd``), so output from ``print()``, the :mod:`logging`
module (whose handlers may hold a reference to the original
``sys.stderr`` object), subprocesses inheriting the standard streams,
and C extensions is all caught.  This keeps test noise from corrupting
the runner's progress rendering and attributes the output to the test
that produced it; captured output of failing tests is included in
their failure report.

The capture window is strictly per test: file descriptors 1 and 2 are
redirected when a test starts and restored as soon as it finishes, so
runner UI output between tests is unaffected.

Capture is controlled by the ``GGT_OUTPUT_CAPTURE`` environment
variable, set by the runner (``--capture``/``--no-capture``) and
propagated to worker processes.  When the variable is unset capture is
off, so library consumers of the suite classes are unaffected.
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

ENV_VAR = "GGT_OUTPUT_CAPTURE"

_STD_FDS = (1, 2)


def is_requested() -> bool:
    return os.environ.get(ENV_VAR) == "1"


class OutputCapture:
    """Redirects fds 1 and 2 to temp files for the duration of a test."""

    def __init__(self) -> None:
        self._saved_fds = [os.dup(fd) for fd in _STD_FDS]
        for fd in self._saved_fds:
            os.set_inheritable(fd, False)  # noqa: FBT003
        self._files = [
            tempfile.TemporaryFile(buffering=0)  # noqa: SIM115
            for _ in _STD_FDS
        ]
        self._active = False
        self._saved_err: Any | None = None
        # faulthandler was enabled on fd 2 directly; while a test is
        # captured, crash dumps must still reach the real stderr.
        if faulthandler.is_enabled():
            self._saved_err = os.fdopen(
                os.dup(self._saved_fds[1]), "w", encoding="utf-8"
            )
            faulthandler.enable(file=self._saved_err, all_threads=True)

    def close(self) -> None:
        """Release all descriptors held by the capture.

        Leaked descriptors would be reported as unclosed files by the
        interpreter's shutdown garbage collection.
        """
        if self._active:
            self.stop()
        if self._saved_err is not None:
            if sys.__stderr__ is not None:
                faulthandler.enable(file=sys.__stderr__, all_threads=True)
            with contextlib.suppress(Exception):
                self._saved_err.close()
            self._saved_err = None
        for file in self._files:
            with contextlib.suppress(Exception):
                file.close()
        self._files = []
        for fd in self._saved_fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        self._saved_fds = []

    def start(self) -> None:
        assert not self._active
        self._flush_std_streams()
        for file, fd in zip(self._files, _STD_FDS, strict=True):
            file.seek(0)
            file.truncate()
            os.dup2(file.fileno(), fd)
        self._active = True

    def stop(self) -> tuple[str, str]:
        assert self._active
        self._flush_std_streams()
        for saved, fd in zip(self._saved_fds, _STD_FDS, strict=True):
            os.dup2(saved, fd)
        self._active = False
        texts = []
        for file in self._files:
            file.seek(0)
            texts.append(file.read().decode("utf-8", errors="replace"))
        return texts[0], texts[1]

    @contextlib.contextmanager
    def suspended(self) -> Iterator[None]:
        """Temporarily restore the real stdout/stderr mid-test."""
        if not self._active:
            yield
            return
        self._flush_std_streams()
        for saved, fd in zip(self._saved_fds, _STD_FDS, strict=True):
            os.dup2(saved, fd)
        try:
            yield
        finally:
            self._flush_std_streams()
            for file, fd in zip(self._files, _STD_FDS, strict=True):
                os.dup2(file.fileno(), fd)

    @staticmethod
    def _flush_std_streams() -> None:
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()


_instances: list[OutputCapture] = []


def instance() -> OutputCapture | None:
    """The process-wide capture, created on first use if requested."""
    if not _instances and is_requested():
        _instances.append(OutputCapture())
    return _instances[0] if _instances else None


def active_instance() -> OutputCapture | None:
    """The process-wide capture if it is currently capturing."""
    if _instances and _instances[0]._active:
        return _instances[0]
    return None


def close() -> None:
    """Close the process-wide capture, if one was created."""
    if _instances:
        _instances.pop().close()
