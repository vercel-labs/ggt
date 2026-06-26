# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations
from typing import TYPE_CHECKING, Any

import logging
import multiprocessing.pool
import multiprocessing.process
import multiprocessing.reduction
import multiprocessing.util
import types

if TYPE_CHECKING:
    from collections.abc import Callable


_orig_pool_worker_handler: Callable[..., Any] | None = None
_orig_pool_join_exited_workers: Callable[..., Any] | None = None

logger = logging.getLogger(__name__)


class WorkerScope:
    def __init__(
        self, initializer: Callable[..., Any], destructor: Callable[..., Any]
    ):
        self.initializer = initializer
        self.destructor = destructor

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Make multiprocessing.Pool happy
        return self.initializer(*args, **kwargs)


def multiprocessing_pool_worker(
    inqueue: Any,
    outqueue: Any,
    initializer: WorkerScope | Callable[..., Any] | None = None,
    *args: Any,
    **kwargs: Any,
) -> None:
    patch_multiprocessing_reduction()

    destructor: Callable[..., Any] | None = None
    if isinstance(initializer, WorkerScope):
        destructor = initializer.destructor

    # This function is executed in the context of a spawned
    # worker process, so the pool.worker() function is the
    # original unpatched version.
    try:
        multiprocessing.pool.worker(  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
            inqueue, outqueue, initializer, *args, **kwargs
        )
    except KeyboardInterrupt:
        # Try to exit with less noise when ctrl+c is pressed
        return

    if destructor is not None:
        destructor()


def multiprocessing_worker_handler(*args: Any) -> None:
    if _orig_pool_worker_handler is not None:
        _orig_pool_worker_handler(*args)

    if len(args) == 1:
        # In some pythons this is a static method with
        # a single argument...
        workers = args[0]._pool
    else:
        # ... and in others it's a staticmethod or a classmethod taking
        # 12-14 positional arguments.
        for arg in args:
            if (
                isinstance(arg, list)
                and arg
                and isinstance(arg[0], multiprocessing.process.BaseProcess)
            ):
                workers = arg
                break
        else:
            logger.error(
                "unable to patch multiprocessing.Pool._handle_workers"
            )
            return

    for worker_process in workers:
        # Give workers ample time to shutdown, and
        # if they don't, the pool will terminate them.
        worker_process.join(timeout=10)


def join_exited_workers(pool: Any) -> None:
    # Our use case shouldn't have workers exiting really, so we skip
    # doing the joins so that we can detect crashes ourselves in the
    # test runner.x
    pass


def _restore_Traceback() -> None:
    return None


def patch_multiprocessing(*, debug: bool) -> None:
    global _orig_pool_worker_handler, _orig_pool_join_exited_workers  # noqa: PLW0603

    if debug:
        multiprocessing.util.log_to_stderr(logging.DEBUG)

    # A plain "fork" without "exec" is broken on macOS since 10.14:
    # https://www.wefearchange.org/2018/11/forkmacos.rst.html
    # Prefer "forkserver" when available: it keeps the safer clean-server
    # fork model while avoiding most of the per-worker import overhead of
    # "spawn". Fall back to "spawn" on platforms that do not support it.
    methods = multiprocessing.get_all_start_methods()
    method = "forkserver" if "forkserver" in methods else "spawn"
    multiprocessing.set_start_method(method)

    # Add the ability to do clean shutdown of the worker.
    multiprocessing.pool.worker = multiprocessing_pool_worker  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    # Allow workers some time to shut down gracefully.
    _orig_pool_worker_handler = multiprocessing.pool.Pool._handle_workers  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
    multiprocessing.pool.Pool._handle_workers = multiprocessing_worker_handler  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    _orig_pool_join_exited_workers = (
        multiprocessing.pool.Pool._join_exited_workers  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
    )
    multiprocessing.pool.Pool._join_exited_workers = join_exited_workers  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    patch_multiprocessing_reduction()


def patch_multiprocessing_reduction() -> None:
    # Disable pickling of traceback objects in multiprocessing.
    # Test errors' tracebacks are serialized manually by
    # `TestReesult._exc_info_to_string()`.  Therefore we need
    # to make sure that some random __traceback__ attribute
    # doesn't crash the test results queue.
    multiprocessing.reduction.register(
        types.TracebackType,
        lambda _: (_restore_Traceback, ()),
    )
