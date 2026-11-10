#!/usr/bin/env python3
"""Run API controls requiring no downloaded project, dataset, or evidence archive."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from test_planning_framework import APITests


def main():
    excluded = {'test_archived_default_choices_and_comparisons'}
    names = unittest.defaultTestLoader.getTestCaseNames(APITests)
    suite = unittest.TestSuite(APITests(name) for name in names if name not in excluded)
    print('Source-only API checks. Archived parity and application experiments are NOT run.', flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
