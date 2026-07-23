# SPDX-PackageName: ggt
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright Vercel, Inc. and the contributors.


from __future__ import annotations
from typing import TYPE_CHECKING, Any

import logging
import time
import multiprocessing.pool
import multiprocessing.process
import multiprocessing.reduction
import multiprocessing.util
import os
import socket
import sys
import types

if TYPE_CHECKING:
    from collections.abc import Callable


_orig_pool_worker_handler: Callable[..., Any] | None = None
_orig_pool_join_exited_workers: Callable[..., Any] | None = None
_orig_popen_forkserver_launch: Callable[..., Any] | None = None

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

    # Skip interpreter finalization: deallocating the worker's object
    # graph (much of it inherited from the preloaded fork server) takes
    # ~15ms per worker and buys nothing — the OS reclaims the memory
    # anyway.  Test teardowns already ran in the destructor above and
    # coverage data is saved there too, so a hard exit is safe; it is
    # also the exit CPython documents for forked children.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


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


def multiprocessing_help_stuff_finish(
    inqueue: Any, task_handler: Any, size: int
) -> None:
    """Drain the inqueue at shutdown without wedging idle workers.

    The upstream implementation acquires the inqueue read lock (to keep
    draining pending tasks until the task handler is no longer blocked
    writing to a full pipe) and never releases it.  Idle workers race
    the pool for that same lock to read their shutdown sentinels; a
    worker that loses the race — the drain may also eat its sentinel —
    can never read again and sits blocked in ``get()`` for the whole
    shutdown grace period in :func:`multiprocessing_worker_handler`
    until the pool terminates it.  Upstream tolerates this because
    stock pools terminate workers immediately; our graceful-shutdown
    handler must not.  So: drain, then release the lock and re-send the
    worker sentinels so every worker can exit cleanly.  Extra sentinels
    are harmless — each worker consumes exactly one, and unread ones
    disappear with the queue.
    """
    inqueue._rlock.acquire()
    try:
        while task_handler.is_alive() and inqueue._reader.poll():
            inqueue._reader.recv()
            time.sleep(0)
    finally:
        inqueue._rlock.release()
    for _ in range(size):
        inqueue.put(None)


def _restore_Traceback() -> None:
    return None


def forkserver_popen_launch_with_retry(self: Any, process_obj: Any) -> None:
    """Retry a worker launch that raced a dying fork server.

    ``connect_to_new_process`` checks the server's liveness with a
    non-blocking ``waitpid`` and restarts a reaped corpse, but a server
    that dies *between* that check and the handshake (or mid-handshake)
    surfaces as ``EOFError``/``ConnectionError`` — on Python <= 3.13 as
    ``RuntimeError`` from ``reduction.sendfds`` — and, once its
    listening socket is unlinked, ``FileNotFoundError``.  The retry
    re-enters ``ensure_running``, which by then observes the death and
    starts a fresh server, so a transient server loss costs one worker
    respawn instead of the entire run.  (An unrelated ``RuntimeError``
    still propagates, merely after the retries fail too.)
    """
    assert _orig_popen_forkserver_launch is not None
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            _orig_popen_forkserver_launch(self, process_obj)
            return
        except (
            EOFError,
            ConnectionError,
            FileNotFoundError,
            RuntimeError,
        ):
            if attempt == attempts:
                raise
            logger.debug(
                "fork server connection failed, retrying worker launch "
                "(attempt %d/%d)",
                attempt,
                attempts,
            )
            time.sleep(0.05 * attempt)


def forkserver_usable() -> bool:
    """Whether the fork server's listener socket can be created.

    The fork server listens on a filesystem-backed ``AF_UNIX`` socket.
    Sandboxes used by agent harnesses and CI (e.g. the Seatbelt profile
    of Codex) commonly deny binding such sockets while permitting
    fork/exec, pipes, and POSIX semaphores — everything the "spawn"
    start method needs.  Probe with a throwaway socket in the same
    temporary directory the fork server would use (including its
    too-long-for-``sun_path`` fallback logic), so parallel runs can
    transparently fall back to "spawn" instead of dying with
    ``PermissionError`` at pool startup.
    """
    try:
        probe_path = os.path.join(
            multiprocessing.util.get_temp_dir(), f"fs-probe-{os.getpid()}"
        )
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(probe_path)
        # Binding leaves a filesystem entry behind.
        os.unlink(probe_path)
    except OSError:
        return False
    return True


def patch_multiprocessing(*, debug: bool) -> None:
    global _orig_pool_worker_handler, _orig_pool_join_exited_workers, _orig_popen_forkserver_launch  # noqa: PLW0603, E501

    if debug:
        multiprocessing.util.log_to_stderr(logging.DEBUG)

    # A plain "fork" without "exec" is broken on macOS since 10.14:
    # https://www.wefearchange.org/2018/11/forkmacos.rst.html
    # Prefer "forkserver" when available: it keeps the safer clean-server
    # fork model while avoiding most of the per-worker import overhead of
    # "spawn". Fall back to "spawn" on platforms that do not support it
    # or in sandboxes that block the fork server's listener socket.
    methods = multiprocessing.get_all_start_methods()
    if "forkserver" in methods and forkserver_usable():
        method = "forkserver"
    else:
        method = "spawn"
    multiprocessing.set_start_method(method)

    if method == "forkserver":
        # Imported lazily (and aliased so the plain "multiprocessing"
        # name above stays module-global): the module refuses to import
        # on platforms without fd passing, which is also why
        # "forkserver" is absent from get_all_start_methods() there.
        from multiprocessing import popen_forkserver  # noqa: PLC0415

        _orig_popen_forkserver_launch = popen_forkserver.Popen._launch  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        popen_forkserver.Popen._launch = (  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
            forkserver_popen_launch_with_retry
        )

    # Add the ability to do clean shutdown of the worker.
    multiprocessing.pool.worker = multiprocessing_pool_worker  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    # Allow workers some time to shut down gracefully.
    _orig_pool_worker_handler = multiprocessing.pool.Pool._handle_workers  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
    multiprocessing.pool.Pool._handle_workers = multiprocessing_worker_handler  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    _orig_pool_join_exited_workers = (
        multiprocessing.pool.Pool._join_exited_workers  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
    )
    multiprocessing.pool.Pool._join_exited_workers = join_exited_workers  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]

    # Keep shutdown sentinel delivery reliable so the graceful worker
    # shutdown above does not stall (see multiprocessing_help_stuff_finish).
    multiprocessing.pool.Pool._help_stuff_finish = staticmethod(  # type: ignore [attr-defined]  # ty: ignore[unresolved-attribute]
        multiprocessing_help_stuff_finish
    )

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
