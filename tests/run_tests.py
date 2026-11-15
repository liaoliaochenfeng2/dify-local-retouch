#!/usr/bin/env python3
"""Run the 67 standalone core tests in an SDK-first, fresh Python process.

These tests use synthetic images and in-memory storage. They do not contact a
live Dify installation, execute a workflow graph, or call a language model.
"""
from __future__ import annotations

# Import the SDK before libraries that may import SSL; it initializes gevent.
from dify_plugin import DifyPluginEnv  # noqa: F401

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root / 'plugin')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / 'manifest.yaml').is_file():
        parser.error('--source must point to the plugin directory')
    os.environ['LOCAL_RETOUCH_TEST_PLUGIN'] = str(source)
    sys.dont_write_bytecode = True
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern='test_*.py')
    expected = suite.countTestCases()
    if expected < 67:
        raise RuntimeError(f'Expected at least 67 core tests, discovered {expected}')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        'result': 'pass' if result.wasSuccessful() else 'fail',
        'source': str(source),
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'tests_discovered': expected,
        'tests_run': result.testsRun,
        'dependencies': {name: version(name) for name in ('dify_plugin', 'Pillow', 'numpy', 'opencv-python-headless')},
        'failures': [{'test': str(test), 'traceback': text} for test, text in result.failures],
        'errors': [{'test': str(test), 'traceback': text} for test, text in result.errors],
        'skipped': [{'test': str(test), 'reason': reason} for test, reason in result.skipped],
        'scope': 'Standalone engine, bounded storage, and real SDK wrapper tests with synthetic images and in-memory KV; not live Dify end-to-end validation.',
        'publication_status': 'not_published',
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
