============
Geltest
============

**Supercharged Python Unittest Runner for Gel**

Geltest is a powerful, parallel test runner designed specifically for Gel
database applications. It extends Python's built-in unittest framework with
advanced features like parallel execution, database fixture management,
comprehensive reporting, and intelligent test sharding.

.. contents:: Table of Contents
   :local:
   :depth: 2

Features
========

🚀 **Parallel Execution**
   Run tests across multiple worker processes to dramatically reduce test
   suite execution time

🗄️ **Database Management**
   Automatic setup and teardown of test databases with caching support for
   faster subsequent runs

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

Database and Infrastructure
---------------------------

``--backend-dsn TEXT``
   Use a specific backend database cluster instead of creating a temporary
   one.

``--data-dir TEXT``
   Use a specified data directory for the test cluster.

``--use-db-cache``
   Attempt to use cached test databases (faster but potentially unsafe for
   some test patterns).

``--use-data-dir-dbs``
   Use existing databases in the specified data directory.

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


Test Decorators
===============

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

Database Caching
----------------

Enable database caching to speed up subsequent test runs:

.. code-block:: bash

   geltest --use-db-cache

This caches the populated test databases and reuses them across runs. Use
with caution as it may lead to test isolation issues if tests modify
persistent state.

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
               --result-log results-${{ matrix.shard }}.json
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

Requirements
============

- Python 3.10+
- click >= 8.1.0
- psutil >= 5.8
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
