#!/usr/bin/env python3
"""Independent V2 integration checks with real SDK Tool/File and an in-memory KV.

No live Dify server or language model is exercised. All intent/recipe JSON is
hand-written to test the implementation contract, not semantic understanding.
"""
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolRuntime
from dify_plugin.file.file import File

from contextlib import ExitStack
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import os
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
import httpx
import requests

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = Path(os.environ.get('LOCAL_RETOUCH_TEST_PLUGIN', ROOT / 'plugin'))
sys.path.insert(0, str(PLUGIN))

from tools.prepare import PrepareTool
from tools.retouch import RetouchTool
from tools.retouch_engine import process_image
from tools.retouch_state import RetouchState


class MemoryKV:
    def __init__(self):
        self.values = {}
        self.fail_reads = False
        self.fail_writes = False

    def exist(self, key):
        if self.fail_reads:
            raise RuntimeError('simulated unavailable daemon')
        return key in self.values

    def get(self, key):
        if self.fail_reads:
            raise RuntimeError('simulated unavailable daemon')
        return self.values[key]

    def set(self, key, value):
        if self.fail_writes:
            raise RuntimeError('simulated full quota')
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def png(color, size=(96, 64)):
    out = BytesIO()
    image = Image.new('RGB', size, color)
    # Texture makes repeated rendering observably different from replay.
    for x in range(size[0]):
        for y in range(size[1]):
            image.putpixel((x, y), tuple(min(255, c + ((x + y) % 13)) for c in color))
    image.save(out, 'PNG')
    return out.getvalue()


def file(data, name='source.png'):
    value = File(url='https://dify.test.invalid/files/' + name,
                 mime_type='image/png', filename=name, extension='.png',
                 size=len(data), type='image')
    value._blob = data
    return value


def unpack(messages):
    messages = list(messages)
    variables = {m.message.variable_name: m.message.variable_value
                 for m in messages if m.type.value == 'variable'}
    blobs = {m.meta['filename']: m.message.blob
             for m in messages if m.type.value == 'blob'}
    return variables, blobs


def intent(action='edit', **overrides):
    value = {'action': action, 'asset_roles': [], 'selected_id': '',
             'preference_notes': [], 'preferred_style': '', 'style_hints': []}
    value.update(overrides)
    return value


def variant(id, brightness):
    return {'id': id, 'label': '方案' + id,
            'recipe': {'version': 2, 'operations': [
                {'op': 'adjust', 'brightness': brightness}]}}


class IntegrationAudit(unittest.TestCase):
    def setUp(self):
        self.kv = MemoryKV()
        self.user, self.conversation, self.app = 'user-1', 'conversation-1', 'app-1'
        self.source = png((60, 85, 110))
        self.source2 = png((130, 90, 55))
        self.reference = png((100, 80, 60), (64, 96))
        self.a, self.b = variant('A', 1.08), variant('B', .92)
        self.network_attempts = []
        self.patches = ExitStack()

        def deny(*args, **kwargs):
            self.network_attempts.append(repr(args)[:150])
            raise AssertionError('unexpected network request')

        for obj, method in [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                            (socket.socket, 'sendto'), (socket, 'create_connection'),
                            (httpx, 'get'), (httpx.Client, 'request'),
                            (httpx.AsyncClient, 'request'), (requests, 'get'),
                            (requests.Session, 'request')]:
            self.patches.enter_context(patch.object(obj, method, deny))

    def tearDown(self):
        self.patches.close()
        self.assertEqual(self.network_attempts, [])

    def tool(self, cls, *, user=None, conversation=None, app=None):
        runtime = ToolRuntime(credentials={}, user_id=self.user if user is None else user,
                              session_id='invocation-is-not-conversation')
        session = SimpleNamespace(storage=self.kv,
                                  conversation_id=self.conversation if conversation is None else conversation,
                                  app_id=self.app if app is None else app)
        value = cls(runtime=runtime, session=session)
        self.assertIsInstance(value, Tool)
        return value

    def prepare(self, request=None, images=None, query='自然一点', **params):
        return unpack(self.tool(PrepareTool)._invoke(dict(
            user_id=self.user, conversation_id=self.conversation,
            request_json=json.dumps(request or intent(), ensure_ascii=False),
            images=images or [], query=query, **params)))

    def initial(self, reference=False):
        images = [file(self.source)]
        if reference:
            images.append(file(self.reference, 'reference.png'))
        variables, blobs = self.prepare(images=images)
        self.assertEqual(variables['status'], 'ready', variables)
        self.assertEqual(variables['has_source'], 1)
        self.assertEqual(blobs['source_original.png'], self.source)
        self.assertEqual(variables['source_sha256'], hashlib.sha256(self.source).hexdigest())
        return variables, blobs

    def render(self, prepared, plan, blobs=None, **overrides):
        variables = prepared
        params = dict(user_id=self.user, conversation_id=self.conversation,
                      source_image=file((blobs or {}).get('source_original.png', self.source)),
                      plan_json=json.dumps(plan, ensure_ascii=False),
                      source_sha256=variables['source_sha256'],
                      context_revision=variables.get('context_revision', ''))
        if blobs and 'reference_original.png' in blobs:
            params['reference_image'] = file(blobs['reference_original.png'])
        params.update(overrides)
        return unpack(self.tool(RetouchTool)._invoke(params))

    def preview(self):
        variables, blobs = self.initial(reference=True)
        result, files = self.render(variables, {'mode': 'preview', 'explanation': '两个轻度方向',
                                                'variants': [self.a, self.b]}, blobs)
        self.assertEqual(result['status'], 'success', result)
        self.assertTrue({'candidate_A.jpg', 'candidate_B.jpg', 'review_preview.jpg', 'recipe.json'} <= files.keys())
        self.assertNotIn('xiaorun_retouched.png', files)
        return variables, blobs

    def state(self):
        return RetouchState(self.kv, self.user, self.conversation, self.app)

    def test_preview_select_b_final_replays_original(self):
        self.preview()
        selected, files = self.prepare(intent('select', selected_id='B'), query='B')
        self.assertEqual(selected['status'], 'ready', selected)
        chosen = json.loads(selected['selected_recipe_json'])
        self.assertEqual(chosen['mode'], 'final')
        self.assertEqual(chosen['variants'], [self.b])
        result, output = self.render(selected, chosen, files)
        self.assertEqual(result['status'], 'success', result)
        expected = process_image(self.source, self.b['recipe'])[0]
        self.assertEqual(output['xiaorun_retouched.png'], expected)
        cached = self.state().load_session()
        self.assertEqual(cached['assets']['source'], self.source)
        self.assertFalse(cached['meta'].get('pending_variants'))

    def test_selection_and_feedback_uses_b_without_skipping_planner(self):
        self.preview()
        selected, files = self.prepare(intent('edit', selected_id='B'), query='就B，但再淡一点')
        self.assertEqual(selected['status'], 'ready', selected)
        self.assertEqual(selected['selected_recipe_json'], '')
        context = json.loads(selected['context_json'])
        self.assertEqual(context['last_recipe'], self.b['recipe'])
        revised = variant('B', .96)
        result, output = self.render(selected, {'mode': 'final', 'explanation': 'B方向减轻', 'variants': [revised]}, files)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(output['xiaorun_retouched.png'], process_image(self.source, revised['recipe'])[0])

    def test_feedback_after_final_never_reuses_result_as_source(self):
        prepared, files = self.initial()
        self.render(prepared, {'mode': 'final', 'explanation': '轻度提亮', 'variants': [self.a]}, files)
        prepared, files = self.prepare(query='再自然一点，其他别动')
        self.assertEqual(files['source_original.png'], self.source)
        self.assertEqual(json.loads(prepared['context_json'])['last_recipe'], self.a['recipe'])
        revised = variant('A', 1.03)
        result, output = self.render(prepared, {'mode': 'final', 'explanation': '减轻', 'variants': [revised]}, files)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(output['xiaorun_retouched.png'], process_image(self.source, revised['recipe'])[0])

    def test_new_source_clears_previous_assets_and_recipes(self):
        self.preview()
        prepared, files = self.prepare(images=[file(self.source2)])
        self.assertEqual(prepared['status'], 'ready', prepared)
        self.assertEqual(files['source_original.png'], self.source2)
        self.assertNotIn('reference_original.png', files)
        context = json.loads(prepared['context_json'])
        self.assertFalse(context.get('last_recipe'))
        self.assertFalse(context.get('pending_variants'))
        self.assertEqual(set(self.state().load_session()['assets']), {'source'})

    def test_new_source_cannot_select_old_candidate(self):
        self.preview()
        prepared, files = self.prepare(intent('select', selected_id='B'),
                                       images=[file(self.source2)], query='B')
        self.assertFalse(prepared.get('selected_recipe_json'), prepared)

    def test_new_reference_does_not_replace_source(self):
        self.initial()
        prepared, files = self.prepare(intent(asset_roles=['reference']), [file(self.reference)], query='参考这张颜色')
        self.assertEqual(prepared['status'], 'ready', prepared)
        self.assertEqual(files['source_original.png'], self.source)
        self.assertEqual(files['reference_original.png'], self.reference)

    def test_changed_auxiliary_asset_invalidates_pending_selection(self):
        self.preview()
        prepared, files = self.prepare(intent('select', selected_id='B', asset_roles=['reference']),
                                       [file(self.source2, 'new-reference.png')], query='选B，参考这张')
        self.assertFalse(prepared.get('selected_recipe_json'), prepared)

    def test_changed_auxiliary_asset_clears_pending_for_later_turn(self):
        self.preview()
        prepared, files = self.prepare(intent(asset_roles=['reference']),
                                       [file(self.source2, 'new-reference.png')], query='换成这张参考')
        self.assertEqual(prepared['status'], 'ready', prepared)
        self.assertFalse(json.loads(prepared['context_json']).get('pending_variants'))

    def test_unavailable_cache_still_emits_current_turn_result(self):
        self.kv.fail_reads = True
        prepared, files = self.initial()
        self.assertEqual(prepared['continuity'], 'no')
        result, output = self.render(prepared, {'mode': 'final', 'explanation': '轻度提亮', 'variants': [self.a]}, files)
        self.assertEqual(result['status'], 'success', result)
        self.assertIn('xiaorun_retouched.png', output)

    def test_failed_cache_save_still_emits_current_turn_result(self):
        self.kv.fail_writes = True
        prepared, files = self.initial()
        self.assertEqual(prepared['continuity'], 'no')
        result, output = self.render(prepared, {'mode': 'final', 'explanation': '轻度提亮', 'variants': [self.a]}, files)
        self.assertEqual(result['status'], 'success', result)
        self.assertIn('xiaorun_retouched.png', output)

    def test_render_save_failure_does_not_discard_images(self):
        prepared, files = self.initial()
        self.assertEqual(prepared['continuity'], 'yes')
        self.kv.fail_writes = True
        result, output = self.render(prepared, {'mode': 'final', 'explanation': '轻度提亮', 'variants': [self.a]}, files)
        self.assertEqual(result['status'], 'success', result)
        self.assertIn('xiaorun_retouched.png', output)
        self.assertTrue(json.loads(result['report_json'])['cache_warnings'])

    def test_source_exceeding_cache_cap_still_renders(self):
        with patch('tools.retouch_state.MAX_SESSION_BYTES', len(self.source) // 2):
            prepared, files = self.initial()
            self.assertEqual(prepared['continuity'], 'no')
            result, output = self.render(prepared, {'mode': 'final', 'explanation': '轻度提亮', 'variants': [self.a]}, files)
        self.assertEqual(result['status'], 'success', result)
        self.assertIn('xiaorun_retouched.png', output)

    def test_source_digest_mismatch_produces_no_partial_files(self):
        prepared, files = self.initial()
        result, output = self.render(prepared, {'mode': 'final', 'explanation': '', 'variants': [self.a]}, files,
                                     source_image=file(self.source2))
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(output, {})

    def test_invalid_second_variant_produces_no_partial_files(self):
        prepared, files = self.initial()
        invalid = deepcopy(self.b)
        invalid['recipe']['operations'][0]['brightness'] = 99
        result, output = self.render(prepared, {'mode': 'preview', 'explanation': '', 'variants': [self.a, invalid]}, files)
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(output, {})

    def test_cross_user_and_app_do_not_load_source(self):
        self.preview()
        self.user = 'different-user'
        result, files = self.prepare(query='选B')
        self.assertEqual(result.get('has_source', 0), 0, result)
        self.assertNotIn('source_original.png', files)
        self.user, self.app = 'user-1', 'different-app'
        result, files = self.prepare(query='继续')
        self.assertEqual(result.get('has_source', 0), 0, result)
        self.assertNotIn('source_original.png', files)

    def test_mismatched_runtime_identity_rejected(self):
        result, files = unpack(self.tool(PrepareTool, user='attacker')._invoke(dict(
            user_id=self.user, conversation_id=self.conversation, images=[file(self.source)],
            request_json=json.dumps(intent()), query='自然一点')))
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(files, {})

    def test_preference_opt_in_persists_across_conversation_only_for_same_user_app(self):
        self.initial()
        request = intent('preferences', preference_notes=['偏自然，保留本来肤色'], preferred_style='clean_natural')
        self.prepare(request, query='记住，我以后偏自然，保留本来肤色')
        self.assertIn('偏自然，保留本来肤色', self.state().load_preferences()['notes'])
        self.conversation = 'new-conversation'
        self.assertIn('偏自然，保留本来肤色', self.state().load_preferences()['notes'])
        self.user = 'different-user'
        self.assertFalse(self.state().load_preferences())
        self.user, self.app = 'user-1', 'different-app'
        self.assertFalse(self.state().load_preferences())

    def test_ordinary_feedback_cannot_become_long_term_preferences(self):
        self.initial()
        self.prepare(intent(preference_notes=['喜欢偏红']), query='这张红一点很好看')
        self.assertFalse(self.state().load_preferences())

    def test_negated_memory_request_cannot_persist_preferences(self):
        self.initial()
        request = intent('preferences', preference_notes=['喜欢偏红'], preferred_style='retro_warm')
        self.prepare(request, query='这次红一点就行，不要记住，别设成默认')
        self.assertFalse(self.state().load_preferences())

    def test_negative_aesthetic_preference_is_not_negative_memory_consent(self):
        self.initial()
        self.prepare(intent('preferences', preference_notes=['不要过度磨皮']), query='记住，以后不要过度磨皮')
        self.assertIn('不要过度磨皮', self.state().load_preferences().get('notes', []))

    def test_missing_app_scope_keeps_session_but_disables_long_term_preferences(self):
        self.app = ''
        prepared, files = self.initial()
        self.assertEqual(prepared['continuity'], 'yes')
        self.assertIn('preferences', prepared['summary'])
        self.prepare(intent('preferences', preference_notes=['偏自然']), query='记住以后偏自然')
        prepared, files = self.prepare(query='继续调整')
        self.assertEqual(prepared['status'], 'ready', prepared)
        self.assertEqual(files['source_original.png'], self.source)
        self.assertFalse(json.loads(prepared['context_json'])['preferences'])

    def test_reset_session_and_reset_preferences_are_independent(self):
        self.initial()
        self.prepare(intent('preferences', preference_notes=['偏自然']), query='记住以后偏自然')
        self.prepare(intent('reset'), query='清空图片会话')
        self.assertIsNone(self.state().load_session())
        self.assertTrue(self.state().load_preferences())
        self.prepare(intent('reset_preferences'), query='清空长期偏好')
        self.assertFalse(self.state().load_preferences())


if __name__ == '__main__':
    unittest.main(verbosity=2)
