#!/usr/bin/env python3
"""Build with the official Dify CLI, validate with the real SDK, and record IDs.

The CLI must be installed separately from an official Dify release. This script
does not download binaries, fetch models, install dependencies, sign the package,
or change the administrator's signature-verification policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import yaml


def command(args):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        raise RuntimeError(f'Validation/build command failed ({result.returncode}): {args[0]}\n{output}')
    return output


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli', default='dify')
    parser.add_argument('--source', type=Path, default=root / 'plugin')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cli = shutil.which(args.cli)
    if not cli:
        parser.error('Official Dify CLI not found; pass --cli /absolute/path/to/dify')
    source = args.source.resolve()
    manifest = yaml.safe_load((source / 'manifest.yaml').read_text())
    output = (args.output or root / f"{manifest['name']}-{manifest['version']}.difypkg").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    print(command([sys.executable, root / 'tests' / 'run_tests.py', '--source', source,
                   '--report', output.with_suffix('.core-tests.json')]))
    source_result = command([sys.executable, root / 'tests' / 'test_plugin_package.py', '--source', source,
                             '--report', output.with_suffix('.source-test.json')])
    print(source_result)
    print(command([cli, 'plugin', 'package', source, '-o', output]))
    checksum_output = command([cli, 'plugin', 'checksum', output])
    print(checksum_output)
    match = re.search(r'checksum[= :]+([0-9a-f]{64})', checksum_output)
    if not match:
        raise RuntimeError('Official CLI did not return a valid plugin checksum')
    package_test_report = output.with_suffix('.test.json')
    print(command([sys.executable, root / 'tests' / 'test_plugin_package.py', '--package', output,
                   '--report', package_test_report]))
    checksum = match.group(1)
    result = {
        'package': output.name,
        'plugin_unique_identifier': f"{manifest['author']}/{manifest['name']}:{manifest['version']}@{checksum}",
        'provider_id': f"{manifest['author']}/{manifest['name']}/{manifest['name']}",
        'official_plugin_checksum': checksum,
        'archive_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'cli': command([cli, 'version']),
        'signed': False,
        'publication_status': 'not_published',
        'publishing_readiness': 'blocked_until_publisher_identity_and_marketplace_review_are_confirmed',
        'installation_note': 'Unsigned local build. Installation is subject to workspace permissions and signature policy; this script does not publish or submit it.',
        'validation_note': 'Official CLI packaging/decoding and real SDK local tests; live Dify installation, scheduling, and model calls are not tested.',
    }
    output.with_suffix('.metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
