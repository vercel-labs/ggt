================================================
ggt - go-go test -- make Python unittest go fast
================================================

`ggt` is a `unittest` runner for Python that runs tests in parallel with the
ability to run and share test setup.  `ggt` also provides a `pytest`-like CLI
to run tests with some useful test selection and running features.

There is no runtime bloat or magic, it's standard `unittest` underneath,
which makes running tests with `ggt` *fast*.

Quick Start
===========

Basic Usage
-----------

Run tests from the default ``tests/`` directory:

.. code-block:: bash

   ggt

Run tests in specific files or directories:

.. code-block:: bash

   ggt tests/test_models.py tests/integration/

Run tests sequentially:

.. code-block:: bash

   ggt -j1

Verbose output with detailed test descriptions:

.. code-block:: bash

   ggt -v

Filter Tests
------------

Run only tests matching a regular expression:

.. code-block:: bash

   ggt -k "test_user.*create"

Exclude tests matching a pattern:

.. code-block:: bash

   ggt -e "test_slow.*"

Run tests in shards (useful for CI):

.. code-block:: bash

   # Run shard 2 out of 4 total shards
   ggt -s 2/4

Command Line Option Reference
=============================

Core Options
------------

``-v, --verbose``
   Increase verbosity level. Shows detailed test descriptions and results.

``-q, --quiet``
   Decrease verbosity level. Minimal output.

``-j, --jobs INTEGER``
   Number of parallel worker processes. Default is 0 (auto-detect based on
   the number of CPU cores).

``-s, --shard TEXT``
   Run tests in shards using format ``current/total`` (e.g., ``2/4``).

Test Selection
--------------

``-k, --include REGEXP``
   Only run tests matching the regular expression. Can be specified multiple
   times.

``-e, --exclude REGEXP``
   Skip tests matching the regular expression. Can be specified multiple
   times.

``-m, --mark MARKEXPR``
   Only run tests matching the given mark expression, e.g.
   ``-m 'slow and not integration'``.  Marks are attached with the
   ``@ggt.mark`` decorator (with pytest compatibility enabled,
   ``pytest.mark`` marks are matched as well).  Expression terms
   that are not plain identifiers are treated as regular expressions
   and matched in full against each mark name, e.g.
   ``-m 'integration_.*'``.

``-x, --failfast``
   Stop execution after the first test failure or error.

``--shuffle``
   Randomize the order in which tests are executed.

``--distribute [module|test]``
   Parallel work distribution granularity. ``module`` (the default)
   keeps each test module's tests in a single worker, avoiding
   repeated module imports and module-fixture setups across workers;
   ``test`` distributes each test individually. When there are too
   few modules to balance the workers, ``module`` automatically falls
   back to per-test distribution.

``--preload/--no-preload``
   Warm up the worker fork server (enabled by default). ggt records
   the test suite's dependency graph in a per-environment
   ``preload-*.json`` in the cache
   directory (a format-versioned subdirectory of ``.ggt_cache``)
   after discovery; on the next run the fork server imports that
   module list concurrently with test discovery, freezes the
   resulting object graph (``gc.freeze()``) to maximize
   copy-on-write sharing, and every worker then forks with a warm
   interpreter. Use ``--no-preload`` if a dependency is not
   fork-safe at import time (e.g. it starts background threads when
   imported).

``--repeat INTEGER``
   Repeat the test suite N times or until the first failure.

Output and Reporting
--------------------

``--output-format [auto|simple|stacked|verbose|silent|json]``
   Control test progress output style:

   - ``auto``: Automatically choose based on terminal capabilities
   - ``simple``: Simple dot notation (like standard unittest)
   - ``stacked``: Rich progress display with module grouping
   - ``verbose``: Detailed output for each test
   - ``silent``: Suppress progress output while still printing the final
     result summary
   - ``json``: Machine-readable NDJSON events on stdout (never chosen
     by ``auto``; see `JSON Format`_)

``--warnings/--no-warnings``
   Enable or disable warning capture and reporting (enabled by default).

``--capture/--no-capture``
   Capture test stdout and stderr (enabled by default).  Like pytest,
   ggt captures at the file-descriptor level, so output from
   ``print()``, the ``logging`` module and subprocesses is all caught
   and attributed to the test that produced it; the captured output of
   failing tests is shown in their failure report.  Use
   ``--no-capture`` to let test output pass through to the terminal
   (this can garble the progress display).

``--result-log FILEPATH``
   Write test results to a JSON log file. Use ``%TIMESTAMP%`` for automatic
   timestamping. Result logs include outcome details and timing fields such
   as ``setup_time_taken`` and ``tests_time_taken``.

``--running-times-log FILEPATH``
   Maintain a CSV file tracking test execution times for performance
   analysis and shard balancing.

``-X, --option KEY=VALUE``
   Test suite specific options in key-value format. Can be specified multiple
   times to pass configuration options to test fixtures and test cases.

   Examples:

   .. code-block:: bash

      # Enable database caching
      ggt -X test-db-cache=on

      # Specify custom data directory
      ggt -X data-dir=/custom/path

      # Multiple options
      ggt -X backend-dsn=postgresql://... -X use-ssl=true

Advanced Options
----------------

``--pytest/--no-pytest``
   Enable or disable pytest-compatible test collection (see *pytest
   Compatibility* below). Enabled by default when pytest is installed,
   e.g. via the ``ggt[pytest]`` extra.

``--debug``
   Output internal debug logs for troubleshooting.

``--list``
   List all discovered tests and exit without running them.

``--include-unsuccessful``
   Include tests that failed in the previous run (requires
   ``--result-log``).

``--cov PACKAGE``
   Enable code coverage reporting for the specified package. Can be used
   multiple times. ``PACKAGE`` must be an importable package name, not a file
   path. Enable coverage support with
   ``uv add --dev ggt[coverage]`` or
   ``python -m pip install 'ggt[coverage]'``.


pytest Compatibility
====================

ggt can discover and run pytest-style test suites.  Install the extra:

.. code-block:: bash

   uv add --dev ggt[pytest]
   # or: python -m pip install 'ggt[pytest]'

Compatibility mode is **enabled automatically whenever pytest is
importable** and can be turned off with ``--no-pytest``.  Pure-unittest
modules are never touched — synthesis only applies to modules that
contain pytest-style tests.

What works
----------

- **Collection**: bare ``test_*`` functions and non-unittest ``Test*``
  classes; pytest-style discovery (``test*.py`` and ``*_test.py`` files,
  test directories without ``__init__.py``, pytest's default
  ``norecursedirs``); ``conftest.py`` loading.
- **Fixtures**: ``@pytest.fixture`` with function/class/module/session
  scopes, ``yield`` teardown, ``autouse``, dependency injection,
  fixture overriding by conftest proximity, and **parametrized
  fixtures** (``params=`` with ``ids``, ``pytest.param`` values and
  per-parameter marks) — every parameter combination reachable from a
  test's fixture closure becomes a separate test that is parallelized
  and sharded individually.
- **The request object**: ``request.param``, ``request.node`` (with
  ``get_closest_marker()``), ``getfixturevalue()``, ``addfinalizer()``
  and a minimal ``request.config`` whose ``getoption()`` is backed by
  ggt's ``-X key=value`` options (unknown options resolve to the
  provided default).
- **Built-in fixtures**: ``tmp_path``, ``tmp_path_factory``,
  ``monkeypatch`` (including ``context()``), ``capsys`` (including
  ``disabled()``), ``caplog``, ``recwarn``.
- **Marks**: ``skip``, ``skipif`` (including string conditions),
  ``xfail`` (``reason``, ``condition``, ``raises``, ``strict``),
  ``parametrize`` (including stacking, ``pytest.param`` with custom ids
  and per-parameter marks) and ``usefixtures``.  Each parameter set
  becomes a separate test that is parallelized and sharded
  individually.  Custom marks are translated to ggt marks, so
  ``-m 'slow and not integration'`` style selection works uniformly.
- **Ini options**: the collection-affecting subset of
  ``[tool.pytest.ini_options]`` (from ``pyproject.toml``) or
  ``pytest.ini`` — ``python_files``, ``python_classes``,
  ``python_functions``, ``testpaths`` and ``usefixtures``.  Other ini
  options (notably ``addopts``) are ignored.
- **Assertion introspection**: plain ``assert`` statements are rewritten
  with pytest's own assertion rewriter, producing rich failure messages
  (``assert [1, 2] == [1, 3] ... At index 1 diff: 2 != 3``).  Rewritten
  bytecode is cached in ``__pycache__`` under a ggt-specific tag (never
  clashing with pytest's own cache), so repeat runs and worker
  processes skip recompilation; set ``GGT_PYTEST_REWRITE_CACHE=0`` to
  disable the cache.
- **xunit-style hooks**: ``setup_module``/``teardown_module``,
  ``setup_class``/``teardown_class``,
  ``setup_method``/``teardown_method``.
- **Imperative outcomes**: ``pytest.skip()``, ``pytest.fail()``,
  ``pytest.raises()``, ``pytest.approx()``.
- **Async tests and async fixtures**: ``async def test_*`` functions
  and methods run on synthesized ``unittest.IsolatedAsyncioTestCase``
  classes — each test gets a fresh event loop, matching
  pytest-asyncio's default loop scope (no ``asyncio_mode``
  configuration needed; ``@pytest.mark.asyncio`` markers are accepted
  and ignored).  ``async def`` fixtures (including ``yield``
  teardown) resolve inside the requesting test's event loop and are
  restricted to function scope — wider scopes would bind a value to a
  single test's loop.  A sync fixture may depend on an async fixture
  when the requesting test is async.
- **anyio**: tests marked ``@pytest.mark.anyio`` follow anyio's
  plugin contract natively — the ``anyio_backend`` fixture joins the
  test's fixture closure (the built-in default is parametrized over
  every installed backend, and user conftest overrides apply), each
  backend becomes a separate test (``[asyncio]``, ``[trio]``), and
  the test runs via ``anyio.run()`` on the selected backend.

Execution model
---------------

Under the hood each pytest-style test becomes a synthesized
``unittest.TestCase`` method, so the full ggt feature set (parallel
workers, sharding, timing logs, result logs, ``-k``/``-e`` selection)
applies uniformly.

Unlike stock pytest — and like ``pytest-xdist`` — tests run in worker
processes.  ggt goes one step further with **shared fixtures**:
session- and module-scoped fixture values are computed *once* in the
runner process, pickled, and shipped to all workers.  Fixture teardown
(the code after ``yield``) also runs once, in the runner.  Values that
cannot be pickled automatically fall back to lazy per-worker execution
(pytest-xdist semantics) with a warning naming the fixture.  Set
``GGT_PYTEST_SHARED_FIXTURES=0`` to disable parent-process execution
entirely.  To opt out a single fixture, decorate it with
``@ggt.local_fixture``; fixtures that depend on it also remain local:

.. code-block:: python

   import ggt
   import pytest

   @ggt.local_fixture
   @pytest.fixture(scope="session")
   def api_client():
       return make_api_client()

Known deviations:

- module-scoped fixture teardown runs at session teardown (in the
  runner) rather than immediately after the module's last test;
- class-scoped fixtures run once per worker per class (the same
  semantics as ``setUpClass``);
- imported test functions are not re-collected in the importing module.

Not supported (yet)
-------------------

The pytest plugin/hook system and third-party plugins (pytest-asyncio,
pytest-mock, ...) — hooks defined in conftest.py files or plugin
modules are ignored with a warning.  Fixture-only plugin modules
declared via a conftest's ``pytest_plugins`` *are* supported
(transitively): they contribute their fixtures at lower lookup
priority than any conftest.  ``pytest_generate_tests``; indirect
parametrization;
dynamically requested parametrized fixtures
(``request.getfixturevalue()`` of a parametrized fixture); package-
and dynamically-scoped fixtures; async fixtures with
class/module/session scope; ``capfd``; ini options beyond the subset
above; and pytest's ``-k`` expression syntax (ggt's regex-based
``-k`` applies instead; use ``-m`` for mark expressions).

Test Decorators and Fixtures
============================

Test Decorators
---------------

``@async_timeout(seconds)``
   Set a timeout for async test methods. The test will fail if it takes
   longer than the specified time.

``@xfail(reason, *, unless=False)``
   Mark a test as expected to fail. The test will be reported as "expected
   failure" if it fails, or "unexpected success" if it passes.

``@xerror(reason, *, unless=False)``
   Like ``@xfail`` but expects an error (exception) rather than just a
   failure (assertion).

``@not_implemented(reason)``
   Mark a test as not implemented. Similar to ``@xfail`` but semantically
   indicates missing functionality.

``@skip(reason)``
   Skip a test entirely (from standard unittest).

``@mark.<name>`` / ``@mark(*names)``
   Attach free-form labels to a test method, function, or class
   (class marks apply to every test in the class, subclasses
   included).  Marks are inert; their only effect is test selection
   with ``-m/--mark``.

Fixture System
--------------

`ggt` provides a fixture system for managing test prerequisites at the session
level. Fixtures are declared as class attributes and automatically handle setup
and teardown across the entire test session.

Basic Fixture Example:

.. code-block:: python

   class DatabaseFixture:
       def __init__(self):
           self._instance = None

       async def set_up(self, ui):
           """Called once during session setup"""
           self._instance = await create_database()

       async def tear_down(self, ui):
           """Called once during session teardown"""
           if self._instance:
               await self._instance.close()

       def __get__(self, obj, cls):
           """Descriptor protocol - returns the fixture value"""
           return self._instance

       def set_options(self, options):
           """Configure fixture from command-line options"""
           if 'database-url' in options:
               self.database_url = options['database-url']

       def get_shared_data(self):
           """Return JSON-serializable data for worker processes"""
           return None

       def set_shared_data(self, data):
           """Receive shared data in worker processes"""
           pass

       async def post_session_set_up(self, cases, *, ui):
           """Called after test class setup is complete"""
           pass

   class MyTestCase(unittest.TestCase):
       database = DatabaseFixture()

       def test_something(self):
           # self.database is automatically available
           result = self.database.query("SELECT 1")
           self.assertEqual(result, 1)

Fixtures support:

- **Automatic lifecycle management**: ``set_up()`` and ``tear_down()`` are
  called automatically
- **Option integration**: ``set_options()`` receives command-line options
  passed via ``-X``
- **Shared data**: Fixtures can share data across processes using
  ``get_shared_data()`` and ``set_shared_data()``
- **Post-session setup**: ``post_session_set_up()`` is called after all
  class setup is complete

Test Case Protocol
------------------

For advanced test cases that need session-level setup and configuration,
implement the ``GGTProto`` protocol:

.. code-block:: python

   class MyAdvancedTestCase(unittest.TestCase):
       @classmethod
       def set_options(cls, options):
           """Receive command-line options passed via -X"""
           cls.database_url = options.get('database-url')
           cls.enable_cache = options.get('cache') == 'on'

       @classmethod
       async def set_up_class_once(cls, ui):
           """Called once per test class during session setup"""
           if cls.database_url:
               cls.connection = await connect(cls.database_url)

       @classmethod
       async def tear_down_class_once(cls, ui):
           """Called once per test class during session teardown"""
           if hasattr(cls, 'connection'):
               await cls.connection.close()

       @classmethod
       def get_shared_data(cls):
           """Return JSON-serializable data to share with worker processes"""
           return {}

       @classmethod
       def update_shared_data(cls, **data):
           """Receive data exported during session setup"""
           pass

The protocol methods are:

- ``set_options(options)``: Receives command-line options from ``-X`` flags
- ``set_up_class_once(ui)``: Async setup called once per test class
- ``tear_down_class_once(ui)``: Async teardown called once per test class
- ``get_shared_data()``: Returns JSON-serializable data for worker processes
- ``update_shared_data(**data)``: Receives shared data in worker processes

Output Formats
==============

Simple Format
-------------
Classic unittest-style output with dots, F's, and E's:

.. code-block::

   ....F..E...s.......................................

Stacked Format
--------------
Rich progress display showing test progress by module:

.. code-block::

   tests/test_models.py    ....F....................
   tests/test_queries.py   ..................s......
   tests/test_auth.py      .........................

   First few failed: test_user_creation, test_login
   Running: (3) test_complex_query, test_batch_insert, test_migration
   Progress: 45/120 tests.

Verbose Format
--------------
Detailed output for each individual test:

.. code-block::

   test_user_creation (tests.test_models.TestUser): OK
   test_user_validation (tests.test_models.TestUser): FAILED: Validation
   failed
   test_async_operation (tests.test_queries.TestQueries): OK

Silent Format
-------------
Suppress progress output and print only the final summary, failures, errors,
and warnings.

JSON Format
-----------
Machine-readable output: one JSON object per line (NDJSON) on stdout,
with all human-oriented output suppressed.  Each line is a
lograil_-compatible log entry carrying the run stage, status, and
progress, so a live progress bar is one pipe away:

.. code-block:: bash

   ggt tests --output-format=json | lograil

.. _lograil: https://github.com/vercel-labs/lograil

Every event carries ``message``/``levelname``, the stage metadata
``lograil.stage`` (``collect``, ``setup``, ``run``, ``teardown``,
``summary``) and ``lograil.stage.status`` (``started``, ``running``,
``finished``, ``failed``), and ``lograil.progress.*`` counters —
``completed``/``total`` during the run stage (``total`` is omitted
while it is still unknown, e.g. during collection).  Tool-specific
details ride along under ``ggt.*`` keys:

.. code-block:: json

   {"message": "FAILED tests.test_app.TestApp.test_x: AssertionError: 1 != 2",
    "levelname": "ERROR", "lograil.stage": "run",
    "lograil.stage.status": "running",
    "lograil.progress.completed": 42, "lograil.progress.total": 98,
    "lograil.progress.description": "tests.test_app.TestApp.test_x",
    "lograil.progress.process": "ggt", "lograil.progress.subject": "run",
    "ggt.test": "tests.test_app.TestApp.test_x", "ggt.marker": "failed",
    "ggt.traceback": "Traceback (most recent call last): ..."}

Per-test completion events are ``INFO`` (transient in progress-bar
consumers); failures, errors, and unexpected successes are ``ERROR`` or
``WARNING`` with a concise one-line reason in ``message`` and the full
traceback in ``ggt.traceback``.  After the run, one message-less event
per failed case carries the complete captured detail (traceback,
stdout/stderr) under ``ggt.detail``, followed by a final event with
aggregate counts and timings under ``ggt.summary``.

The json format requires output capture (it is incompatible with
``--no-capture``) so that test output cannot corrupt the stream; the
captured output of failing tests is included in their ``ggt.detail``
event instead.

Performance and Optimization
============================

Parallel Execution
------------------

ggt automatically detects a worker count based on your CPU cores. You can
override this:

.. code-block:: bash

   # Use specific number of workers
   ggt -j 8

   # Use single-threaded execution
   ggt -j 1

Fixture Options
---------------

Pass configuration to your fixtures using the ``-X`` option:

.. code-block:: bash

   # Enable caching in your fixtures
   ggt -X test-cache=on

   # Pass database configuration
   ggt -X database-url=postgresql://localhost/testdb

   # Multiple configuration options
   ggt -X cache=on -X timeout=30 -X verbose=true

Your fixtures receive these options in their ``set_options()`` method and
can use them to customize behavior.

Test Sharding
-------------

Distribute tests across multiple CI jobs using sharding:

.. code-block:: bash

   # Job 1 of 4
   ggt -s 1/4

   # Job 2 of 4
   ggt -s 2/4

ggt uses test and setup timing data from ``--running-times-log`` when
available to balance load across shards.

Integration
===========

Continuous Integration
----------------------

Example GitHub Actions configuration:

.. code-block:: yaml

   name: Tests
   on: [push, pull_request]

   jobs:
     test:
       runs-on: ubuntu-latest
       strategy:
         matrix:
           shard: [1, 2, 3, 4]
       steps:
         - uses: actions/checkout@v5
         - uses: actions/setup-python@v5
           with:
             python-version: '3.11'
         - run: pip install ggt
         - run: |
             ggt -s ${{ matrix.shard }}/4 \
               --result-log results-${{ matrix.shard }}.json \
               -X test-cache=on -X timeout=300
         - uses: actions/upload-artifact@v4
           with:
             name: test-results
             path: results-*.json

GitHub Action
~~~~~~~~~~~~~

This repository also ships a ``Setup ggt`` composite action that installs
ggt and persists the ``.ggt_cache`` timing data across workflow runs, so
shard balancing and parallel scheduling stay warm from run to run:

.. code-block:: yaml

   steps:
     - uses: actions/checkout@v5
     - uses: actions/setup-python@v5
       with:
         python-version: '3.12'
     - uses: vercel-labs/ggt@v1.2.0  # pin an exact release tag or SHA
       with:
         version: '1.2.0'                          # optional, default: latest
         extras: 'coverage'                        # optional
         cache-suffix: shard-${{ matrix.shard }}   # per-shard timing data
     - run: ggt -s ${{ matrix.shard }}/4

Inputs:

``install``
   Whether to install ggt (default ``true``). Set to ``false`` to skip
   installation and only persist the ``.ggt_cache`` timing cache, e.g.
   when ggt is already installed as part of the project dependencies.
``version``
   Exact ggt version to install. Empty (the default) installs the
   latest release from PyPI. Version ranges are not supported.
``extras``
   Comma-separated extras to install, e.g. ``coverage,pytest``.
``enable-cache``
   Whether to persist ``.ggt_cache`` across runs (default ``true``).
``cache-dependency-glob``
   Newline-separated glob(s) hashed into the cache key so caches roll
   over when dependencies change (default ``**/pyproject.toml``).
``cache-suffix``
   Extra cache key discriminator, useful to keep matrix legs or shards
   from sharing one cache slot.
``working-directory``
   Directory where ggt will be invoked, i.e. where ``.ggt_cache`` lives
   (default ``.``).

Outputs: ``ggt-version`` (the installed version; empty when
``install: false``) and ``cache-hit`` (whether timing data was
restored).

Notes:

* The action installs ggt into the **active** Python environment (set up
  by ``actions/setup-python`` or equivalent, Python 3.11+). ggt is a test
  runner, so it must share an interpreter with your project's code and
  dependencies — there is deliberately no isolated virtualenv.
* ``.ggt_cache`` holds preload and timing data only; it is purely a
  performance optimization, and stale entries are harmless.
* Pin the action to an exact release tag or commit SHA. Do not expect a
  moving ``v1`` tag: ``v*`` tags in this repository drive PyPI releases.

Coverage Integration
--------------------

Generate coverage reports alongside your tests:

.. code-block:: bash

   ggt --cov myproject --cov myproject.submodule

This integrates with the ``coverage`` package to provide detailed code
coverage analysis. Enable coverage support with
``uv add --dev ggt[coverage]`` or
``python -m pip install 'ggt[coverage]'`` to use this option.

The coverage report is written to the console and a ``.coverage`` data file is
left in the working directory for follow-up ``coverage`` commands.

Option Integration
------------------

Test cases can receive and use options passed via ``-X``:

.. code-block:: python

   class MyTestCase(unittest.TestCase):
       @classmethod
       def set_options(cls, options):
           cls.enable_debug = options.get('debug') == 'on'
           cls.database_url = options.get('database-url')

       @classmethod
       async def set_up_class_once(cls, ui):
           if cls.database_url:
               cls.db = await connect(cls.database_url)

       @classmethod
       async def tear_down_class_once(cls, ui):
           if hasattr(cls, 'db'):
               await cls.db.close()

       @classmethod
       def get_shared_data(cls):
           return {}

       @classmethod
       def update_shared_data(cls, **data):
           pass

Run with custom options:

.. code-block:: bash

   ggt -X debug=on -X database-url=postgresql://localhost/test

Requirements
============

- Python 3.11+
- typing-extensions >= 4.14.0

Optional dependencies:

- coverage >= 7.4 (``ggt[coverage]``)
- pytest >= 7.3.2, < 10 (``ggt[pytest]``, enables pytest compatibility mode)

Development
===========

Set up a development environment and the repository git hooks with:

.. code-block:: bash

   uv sync --dev
   uv run poe setup

Day-to-day tasks are driven by `poethepoet <https://poethepoet.natn.io>`_:

.. code-block:: bash

   uv run poe lint                # ruff check/format, zizmor
   uv run poe typecheck           # mypy, ty
   uv run poe test                # ggt's own test suite
   uv run poe qa                  # all of the above
   uv run poe fix                 # apply ruff autofixes and formatting
   uv run poe test-python-matrix  # test on every supported Python (tox)

Task output is rendered as a live status dashboard by lograil_, which
consumes ggt's own ``--output-format=json`` stream for the test tasks.
Pass ``-v`` to stream plain output instead.

``poe setup`` registers the hook scripts in ``scripts/githooks/`` via
git's configuration-based hooks (see ``scripts/sync-githooks.py``): the
pre-commit hook runs lint and typechecks, and the pre-push hook
additionally runs the test suite.

License
=======

ggt is licensed under the Apache License, Version 2.0. See the LICENSE
file for details.

Contributing
============

Contributions are welcome. Please include tests and keep the type-checking
suite passing.
