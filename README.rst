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

``-x, --failfast``
   Stop execution after the first test failure or error.

``--shuffle``
   Randomize the order in which tests are executed.

``--repeat INTEGER``
   Repeat the test suite N times or until the first failure.

Output and Reporting
--------------------

``--output-format [auto|simple|stacked|verbose|silent]``
   Control test progress output style:

   - ``auto``: Automatically choose based on terminal capabilities
   - ``simple``: Simple dot notation (like standard unittest)
   - ``stacked``: Rich progress display with module grouping
   - ``verbose``: Detailed output for each test
   - ``silent``: Suppress progress output while still printing the final
     result summary

``--warnings/--no-warnings``
   Enable or disable warning capture and reporting (enabled by default).

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

- coverage >= 7.4

License
=======

ggt is licensed under the Apache License, Version 2.0. See the LICENSE
file for details.

Contributing
============

Contributions are welcome. Please include tests and keep the type-checking
suite passing.
