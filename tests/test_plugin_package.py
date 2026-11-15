#!/usr/bin/env python3
"""Load the real Dify SDK and exercise a source tree or unpacked .difypkg.

Run with the plugin requirements installed. No live Dify instance is contacted.
Uploaded Dify File objects are seeded with bytes, modelling files already fetched
from a Dify file service. Network connections and HTTP clients are denied in tests.
"""
from __future__ import annotations

# The SDK applies gevent patches; import it before HTTP libraries.
from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.file.file import File
from dify_plugin.entities.tool import ToolRuntime

import argparse
from contextlib import contextmanager
from importlib.metadata import version
from io import BytesIO
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import httpx
import requests
from PIL import Image
import yaml


class MemoryStorage:
    """Only the SDK storage transport is substituted; real wrappers execute."""
    def __init__(self):
        self.data = {}
        self.reads = 0
        self.writes = 0

    def exist(self, key):
        return key in self.data

    def get(self, key):
        self.reads += 1
        return self.data[key]

    def set(self, key, value):
        assert isinstance(key, str) and isinstance(value, bytes)
        self.writes += 1
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


def file_from_bytes(blob, filename='test.png', mime='image/png'):
    result = File(url='https://dify.test.invalid/files/' + filename,
                  mime_type=mime, filename=filename, extension=Path(filename).suffix,
                  size=len(blob), type='image')
    # Model an upload already fetched from a Dify file service.
    result._blob = blob
    return result


def unpack(messages):
    variables = {m.message.variable_name: m.message.variable_value
                 for m in messages if m.type.value == 'variable'}
    blobs = {m.meta['filename']: m for m in messages if m.type.value == 'blob'}
    return variables, blobs


def file_from_message(message):
    return file_from_bytes(message.message.blob, message.meta['filename'], message.meta['mime_type'])


def assert_error_without_files(tool, parameters):
    messages = list(tool._invoke(parameters))
    variables, blobs = unpack(messages)
    assert variables.get('status') == 'error', variables
    assert not blobs, list(blobs)
    return variables


@contextmanager
def plugin_context(plugin_dir: Path):
    old_cwd = Path.cwd()
    old_path = list(sys.path)
    try:
        os.chdir(plugin_dir)
        sys.path.insert(0, str(plugin_dir))
        yield
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path


def run_test(plugin_dir: Path) -> dict:
    sys.dont_write_bytecode = True
    attempted_network = []

    def deny_network(*args, **kwargs):
        attempted_network.append(repr(args)[:250])
        raise AssertionError('External network access is disabled in this test')

    with plugin_context(plugin_dir), \
         patch.object(socket.socket, 'connect', deny_network), \
         patch.object(socket.socket, 'connect_ex', deny_network), \
         patch.object(socket.socket, 'sendto', deny_network), \
         patch.object(socket, 'create_connection', deny_network), \
         patch.object(httpx, 'get', deny_network), \
         patch.object(httpx.Client, 'request', deny_network), \
         patch.object(httpx.AsyncClient, 'request', deny_network), \
         patch.object(requests, 'get', deny_network), \
         patch.object(requests.Session, 'request', deny_network):
        registration = PluginRegistration(DifyPluginEnv())
        manifest = yaml.safe_load(Path('manifest.yaml').read_text())
        assert isinstance(manifest.get('author'), str) and manifest['author'].strip()
        assert manifest['name'] == 'local_retouch'
        assert isinstance(manifest.get('version'), str) and manifest['version'].strip()
        assert manifest['resource']['permission']['storage']['enabled'] is True
        assert not registration.models_mapping
        assert not registration.endpoints_configuration
        assert list(registration.tools_mapping) == ['local_retouch']
        provider_cfg, provider_cls, tools = registration.tools_mapping['local_retouch']
        assert provider_cfg.identity.author == manifest['author']
        assert not provider_cfg.credentials_schema
        assert not provider_cfg.oauth_schema
        provider_cls().validate_credentials({})
        assert set(tools) == {'prepare', 'retouch'}
        style_resource = Path('resources/style_guide.json')
        assert style_resource.is_file()
        style_guide = json.loads(style_resource.read_text(encoding='utf-8'))
        assert style_guide['schema_version'] == '2.0'
        assert len(style_guide['style_cards']) == 8
        tool_cfg, tool_cls = tools['retouch']
        parameters = {p.name: p for p in tool_cfg.parameters}
        assert parameters['source_image'].type.value == 'file'
        assert parameters['source_image'].required
        assert parameters['plan_json'].type.value == 'string'
        assert parameters['source_sha256'].type.value == 'string'
        assert {'user_id', 'conversation_id', 'context_revision', 'reference_image'} <= parameters.keys()
        prepare_cfg, prepare_cls = tools['prepare']
        prepare_parameters = {p.name: p for p in prepare_cfg.parameters}
        assert {'images', 'request_json', 'query', 'user_id', 'conversation_id'} <= prepare_parameters.keys()

        store = MemoryStorage()
        identity = {'user_id': 'sdk-smoke-user', 'conversation_id': 'sdk-smoke-conversation'}
        session = SimpleNamespace(storage=store, app_id='sdk-smoke-app',
                                  conversation_id=identity['conversation_id'])
        runtime = ToolRuntime(credentials={}, user_id=identity['user_id'], session_id='sdk-smoke-session')
        tool = tool_cls(runtime=runtime, session=session)
        prepare = prepare_cls(runtime=runtime, session=session)

        source_buf = BytesIO()
        Image.new('RGB', (192, 128), (85, 105, 125)).save(source_buf, 'PNG')
        source_bytes = source_buf.getvalue()
        source = file_from_bytes(source_bytes)

        # Prepare an uploaded original using the real SDK tool and cache logic.
        prepare_request = {'action': 'edit', 'asset_roles': ['source'], 'style_hints': ['clean_natural']}
        prepared, prepared_files = unpack(list(prepare._invoke({
            **identity, 'query': '自然一点，保留原貌', 'images': [source],
            'request_json': json.dumps(prepare_request),
        })))
        assert prepared['status'] == 'ready', prepared
        assert prepared['continuity'] == 'yes'
        assert prepared['has_source'] == 1
        assert prepared['source_sha256'] == sha256(source_bytes).hexdigest()
        assert prepared['context_revision']
        assert {'source_original.png', 'source_preview.jpg'} <= prepared_files.keys()
        assert prepared_files['source_original.png'].message.blob == source_bytes
        prepared_context = json.loads(prepared['context_json'])
        assert len(prepared_context['style_catalog']) == 8
        assert prepared_context['style_cards']
        assert 'cases' not in prepared_context

        recipe = {'version': 2, 'operations': [
            {'op': 'tone', 'exposure': .15, 'highlights': -.08},
            {'op': 'style', 'name': 'clean_natural', 'strength': .3},
        ]}
        plan = {'mode': 'final', 'explanation': '真实SDK小图测试',
                'variants': [{'id': 'A', 'label': '自然调色', 'recipe': recipe}]}

        def retouch_params(prepared_vars, files, chosen_plan):
            return {**identity,
                    'source_image': file_from_message(files['source_original.png']),
                    'source_sha256': prepared_vars['source_sha256'],
                    'context_revision': prepared_vars['context_revision'],
                    'plan_json': json.dumps(chosen_plan, ensure_ascii=False)}

        base_params = retouch_params(prepared, prepared_files, plan)
        variables, blobs = unpack(list(tool._invoke(base_params)))
        assert variables.get('status') == 'success', variables
        assert variables['mode'] == 'final'
        report = json.loads(variables['report_json'])
        assert report['status'] == 'success'
        assert report['variants'][0]['report']['operations_completed'] == 2
        assert not report['cache_warnings']
        assert {'xiaorun_retouched.png', 'review_preview.jpg', 'recipe.json'} <= blobs.keys()
        assert blobs['xiaorun_retouched.png'].meta['mime_type'] == 'image/png'
        assert Image.open(BytesIO(blobs['xiaorun_retouched.png'].message.blob)).size == (192, 128)
        saved_recipe = json.loads(blobs['recipe.json'].message.blob)
        assert saved_recipe['plan'] == plan
        assert saved_recipe['report']['status'] == 'success'
        final_files = list(blobs)

        # Continuing without re-upload must supply the original bytes, not the
        # last processed PNG. Candidate recipes are independently replayed.
        continued, continued_files = unpack(list(prepare._invoke({
            **identity, 'query': '给我两个风格小样', 'images': [],
            'request_json': json.dumps({'action': 'edit', 'asset_roles': []}),
        })))
        assert continued['status'] == 'ready', continued
        assert continued_files['source_original.png'].message.blob == source_bytes
        assert json.loads(continued['context_json'])['last_recipe'] == recipe
        variant_a = {'id': 'A', 'label': '暖柔', 'recipe': {'version': 2, 'operations': [{'op': 'style', 'name': 'warm_soft', 'strength': .5}]}}
        variant_b = {'id': 'B', 'label': '冷调', 'recipe': {'version': 2, 'operations': [{'op': 'style', 'name': 'cool_editorial', 'strength': .5}]}}
        preview_plan = {'mode': 'preview', 'explanation': 'A/B小样测试', 'variants': [variant_a, variant_b]}
        preview_vars, preview_blobs = unpack(list(tool._invoke(retouch_params(continued, continued_files, preview_plan))))
        assert preview_vars['status'] == 'success', preview_vars
        assert preview_vars['mode'] == 'preview'
        assert {'candidate_A.jpg', 'candidate_B.jpg', 'review_preview.jpg', 'recipe.json'} <= preview_blobs.keys()
        assert 'xiaorun_retouched.png' not in preview_blobs
        for name in ('candidate_A.jpg', 'candidate_B.jpg'):
            assert max(Image.open(BytesIO(preview_blobs[name].message.blob)).size) <= 1000
        assert preview_blobs['candidate_A.jpg'].message.blob != preview_blobs['candidate_B.jpg'].message.blob

        selected, selected_files = unpack(list(prepare._invoke({
            **identity, 'query': '选B', 'images': [],
            'request_json': json.dumps({'action': 'select', 'asset_roles': [], 'selected_id': 'B'}),
        })))
        assert selected['status'] == 'ready', selected
        selected_plan = json.loads(selected['selected_recipe_json'])
        assert selected_plan['mode'] == 'final'
        assert selected_plan['variants'] == [variant_b]
        selected_vars, selected_blobs = unpack(list(tool._invoke(retouch_params(selected, selected_files, selected_plan))))
        assert selected_vars['status'] == 'success', selected_vars
        from tools.retouch_engine import process_image
        expected_png, _, _ = process_image(source_bytes, variant_b['recipe'])
        assert selected_blobs['xiaorun_retouched.png'].message.blob == expected_png

        failures = [
            {**base_params, 'source_image': None},
            {**base_params, 'source_sha256': '0' * 64},
            {**base_params, 'plan_json': json.dumps({'mode': 'final', 'variants': [{'id': 'A', 'label': 'bad', 'recipe': {'version': 2, 'operations': [{'op': 'unsupported'}]}}]})},
            {**base_params, 'plan_json': '{"mode":"final","value":NaN}'},
            {**base_params, 'plan_json': '{"mode":"final","mode":"preview"}'},
            {**base_params, 'user_id': 'different-user'},
            {**base_params, 'conversation_id': 'different-conversation'},
            # First candidate computes successfully, second fails: no partial A
            # attachment may escape before the whole call has been validated.
            {**base_params, 'plan_json': json.dumps({'mode': 'preview', 'variants': [variant_a, {'id': 'B', 'label': 'bad second variant', 'recipe': {'version': 2, 'operations': [{'op': 'unsupported'}]}}]})},
        ]
        cache_snapshot = dict(store.data)
        for params in failures:
            assert_error_without_files(tool, params)
            assert store.data == cache_snapshot
        prepare_failures = [
            {**identity, 'query': 'test', 'images': [source], 'request_json': json.dumps({'action': 'edit', 'asset_roles': ['source', 'reference']})},
            {**identity, 'query': 'test', 'images': [], 'request_json': '{"action":"unsupported"}'},
            {**identity, 'user_id': 'different-user', 'query': 'test', 'images': [source], 'request_json': json.dumps(prepare_request)},
        ]
        for params in prepare_failures:
            assert_error_without_files(prepare, params)
            assert store.data == cache_snapshot
        assert not attempted_network, attempted_network
        return {
            'result': 'pass',
            'plugin_version': manifest['version'],
            'sdk': version('dify_plugin'),
            'dependencies': {name: version(name) for name in ('Pillow', 'numpy', 'opencv-python-headless')},
            'provider': provider_cfg.identity.name,
            'tools': list(tools),
            'credentials_required': False,
            'prepare_output_files': list(prepared_files),
            'smoke_output_files': final_files,
            'preview_output_files': list(preview_blobs),
            'selected_candidate_replayed_from_original': True,
            'style_resource_cards': len(style_guide['style_cards']),
            'fake_storage': {'keys': len(store.data), 'reads': store.reads, 'writes': store.writes},
            'image_size': [192, 128],
            'negative_tests': len(failures) + len(prepare_failures),
            'network_attempts': 0,
            'network_guards': ['socket.connect', 'socket.connect_ex', 'socket.sendto', 'socket.create_connection', 'httpx.get', 'httpx.Client.request', 'httpx.AsyncClient.request', 'requests.get', 'requests.Session.request'],
            'scope': 'Real SDK registration and local invocation with in-memory storage; not a live Dify install or end-to-end test',
            'publication_status': 'not_published',
        }


def main():
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--source', type=Path)
    choice.add_argument('--package', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.source:
        result = run_test(args.source.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='local-retouch-package-') as directory:
            destination = Path(directory)
            with zipfile.ZipFile(args.package.resolve()) as package:
                assert package.testzip() is None
                names = package.namelist()
                assert len(names) == len(set(names)), 'Duplicate archive entries are not allowed'
                assert {'manifest.yaml', 'tools/retouch_engine.py', 'tools/prepare.py', 'tools/prepare.yaml', 'resources/style_guide.json'} <= set(names)
                for name in names:
                    path = Path(name)
                    assert not path.is_absolute() and '..' not in path.parts
                    assert not any(p in {'.venv', '__pycache__', '.git', '.env'} for p in path.parts)
                    assert path.suffix not in {'.pyc', '.pyo'}
                    assert not stat.S_ISLNK(package.getinfo(name).external_attr >> 16), 'Symlink archive entries are not allowed'
                package.extractall(destination)
            result = run_test(destination)
            result['package_file_count'] = len(names)
            result['package'] = args.package.name
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
