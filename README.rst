=======
Geltest
=======

**Supercharged Python Unittest Runner**

Geltest is a powerful, parallel test runner designed for Gel database
applications. It extends Python's built-in unittest framework with advanced
features like parallel execution, flexible fixture management, comprehensive
reporting, and intelligent test sharding. While optimized for Gel workflows,
it provides a generalized architecture for complex test suite requirements.

.. contents:: Table of Contents
   :local:
   :depth: 2

Features
========

🚀 **Parallel Execution**
   Run tests across multiple worker processes to dramatically reduce test
   suite execution time

🗄️ **Flexible Fixture System**
   Powerful session-level fixture management with automatic setup and teardown
   of test prerequisites

📊 **Rich Output Formats**
   Multiple output formats including simple dots, verbose descriptions, and a
   beautiful stacked progress display

🎯 **Smart Test Selection**
   Filter tests with regex patterns, run specific shards, or include only
   previously failed tests

📈 **Performance Tracking**
   Track test execution times and maintain running time logs for performance
   analysis

⚡ **Advanced Features**
   - Test shuffling for randomization
   - Coverage reporting integration
   - Configurable timeouts with ``@async_timeout``
   - Expected failure decorators (``@xfail``, ``@xerror``,
     ``@not_implemented``)
   - Comprehensive warning capture and reporting
   - Extensible option system for test suite customization

Installation
============

Install geltest using pip:

.. code-block:: bash

   pip install geltest

Or install from source:

.. code-block:: bash

   git clone https://github.com/geldata/geltest.git
   cd geltest
   pip install -e .

Quick Start
===========

Basic Usage
-----------

Run all tests in the current directory:

.. code-block:: bash

   geltest

Run tests in specific files or directories:

.. code-block:: bash

   geltest tests/test_models.py tests/integration/

Run tests in parallel using 4 worker processes:

.. code-block:: bash

   geltest -j 4

Verbose output with detailed test descriptions:

.. code-block:: bash

   geltest -v

Filter Tests
------------

Run only tests matching a pattern:

.. code-block:: bash

   geltest -k "test_user.*create"

Exclude tests matching a pattern:

.. code-block:: bash

   geltest -e "test_slow.*"

Run tests in shards (useful for CI):

.. code-block:: bash

   # Run shard 2 out of 4 total shards
   geltest -s 2/4

Command Line Options
====================

Core Options
------------

``-v, --verbose``
   Increase verbosity level. Shows detailed test descriptions and results.

``-q, --quiet``
   Decrease verbosity level. Minimal output.

``-j, --jobs INTEGER``
   Number of parallel worker processes. Default is 0 (auto-detect based on
   CPU cores).

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

``--output-format [auto|simple|stacked|verbose]``
   Control test progress output style:

   - ``auto``: Automatically choose based on terminal capabilities
   - ``simple``: Simple dot notation (like standard unittest)
   - ``stacked``: Rich progress display with module grouping
   - ``verbose``: Detailed output for each test

``--warnings/--no-warnings``
   Enable or disable warning capture and reporting (enabled by default).

``--result-log FILEPATH``
   Write test results to a JSON log file. Use ``%TIMESTAMP%`` for automatic
   timestamping.

``--running-times-log FILEPATH``
   Maintain a CSV file tracking test execution times for performance
   analysis.

``-X, --option KEY=VALUE``
   Test suite specific options in key-value format. Can be specified multiple
   times to pass configuration options to test fixtures and test cases.

   Examples:

   .. code-block:: bash

      # Enable database caching
      geltest -X test-db-cache=on

      # Specify custom data directory
      geltest -X data-dir=/custom/path

      # Multiple options
      geltest -X backend-dsn=postgresql://... -X use-ssl=true

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
   multiple times.


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

Geltest provides a powerful fixture system for managing test prerequisites
at the session level. Fixtures are declared as class attributes and
automatically handle setup and teardown across the entire test session.

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
implement the ``DatabaseTestCaseProto`` protocol:

.. code-block:: python

   class MyAdvancedTestCase(unittest.TestCase, DatabaseTestCaseProto):
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

The protocol methods are:

- ``set_options(options)``: Receives command-line options from ``-X`` flags
- ``set_up_class_once(ui)``: Async setup called once per test class
- ``tear_down_class_once(ui)``: Async teardown called once per test class

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

Performance and Optimization
============================

Parallel Execution
------------------

Geltest automatically detects the optimal number of worker processes based
on your CPU cores. You can override this:

.. code-block:: bash

   # Use specific number of workers
   geltest -j 8

   # Use single-threaded execution
   geltest -j 1

Fixture Options
---------------

Pass configuration to your fixtures using the ``-X`` option:

.. code-block:: bash

   # Enable caching in your fixtures
   geltest -X test-cache=on

   # Pass database configuration
   geltest -X database-url=postgresql://localhost/testdb

   # Multiple configuration options
   geltest -X cache=on -X timeout=30 -X verbose=true

Your fixtures receive these options in their ``set_options()`` method and
can use them to customize behavior.

Test Sharding
-------------

Distribute tests across multiple CI jobs using sharding:

.. code-block:: bash

   # Job 1 of 4
   geltest -s 1/4

   # Job 2 of 4
   geltest -s 2/4

Geltest intelligently distributes tests to balance load across shards.

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
         - uses: actions/checkout@v3
         - uses: actions/setup-python@v4
           with:
             python-version: '3.11'
         - run: pip install geltest
         - run: |
             geltest -s ${{ matrix.shard }}/4 \
               --result-log results-${{ matrix.shard }}.json \
               -X test-cache=on -X timeout=300
         - uses: actions/upload-artifact@v3
           with:
             name: test-results
             path: results-*.json

Coverage Integration
--------------------

Generate coverage reports alongside your tests:

.. code-block:: bash

   geltest --cov myproject --cov myproject.submodule

This integrates with the ``coverage`` package to provide detailed code
coverage analysis.

Option Integration
------------------

Test cases can receive and use options passed via ``-X``:

.. code-block:: python

   class MyTestCase(DatabaseTestCaseProto):
       @classmethod
       def set_options(cls, options):
           cls.enable_debug = options.get('debug') == 'on'
           cls.database_url = options.get('database-url')

       @classmethod
       async def set_up_class_once(cls, ui):
           if cls.database_url:
               cls.db = await connect(cls.database_url)

Run with custom options:

.. code-block:: bash

   geltest -X debug=on -X database-url=postgresql://localhost/test

Requirements
============

- Python 3.10+
- click >= 8.1.0
- coverage >= 7.4
- typing-extensions >= 4.14.0

The package is compatible with CPython on Linux, macOS, and Windows.

License
=======

Geltest is licensed under the Apache License, Version 2.0. See the LICENSE
file for details.

Contributing
============

We welcome contributions! Please see our `GitHub repository
<https://github.com/geldata/geltest>`_ for:

- Issue reporting
- Feature requests
- Pull request guidelines
- Development setup instructions
