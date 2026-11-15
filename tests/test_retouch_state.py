"""Tests for bounded persistence and isolation, independent of Dify networking."""
from pathlib import Path
import os
import sys
import unittest

PLUGIN = Path(os.environ.get('LOCAL_RETOUCH_TEST_PLUGIN', Path(__file__).resolve().parents[1] / 'plugin'))
sys.path.insert(0, str(PLUGIN))
from tools.retouch_state import (
    RetouchState, StateConflict, StateTooLarge, StateUnavailable,
    MAX_CACHE_BYTES, MAX_SESSION_BYTES, SESSION_TTL, PREFERENCES_TTL,
)


class MemoryStorage:
    def __init__(self):
        self.data = {}
        self.fail = False
        self.read_bytes = 0
        self.read_keys = []

    def exist(self, key):
        if self.fail:
            raise RuntimeError('simulated service failure')
        return key in self.data

    def get(self, key):
        if self.fail:
            raise RuntimeError('simulated service failure')
        value = self.data[key]
        self.read_bytes += len(value)
        self.read_keys.append(key)
        return value

    def set(self, key, value):
        if self.fail:
            raise RuntimeError('simulated quota failure')
        self.data[key] = value

    def delete(self, key):
        if self.fail:
            raise RuntimeError('simulated service failure')
        self.data.pop(key, None)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStorage()
        self.time = 1000.0
        self.state = self.make('alice', 'c1')

    def make(self, user, conversation='c1', app='a1'):
        return RetouchState(self.store, user, conversation, app, clock=lambda: self.time)

    def test_round_trip_original_bytes_and_meta(self):
        assets = {'source': b'original\x00\xff', 'reference': b'ref', 'mask': b'mask', 'background': b'bg'}
        meta = {'last_recipe': {'operations': []}, 'pending_recipes': [{}, {}], 'source_sha256': '123'}
        revision = self.state.save_session(assets, meta)
        loaded = self.state.load_session()
        self.assertEqual(loaded, {'assets': assets, 'meta': meta, 'revision': revision})
        self.assertNotIn('alice', ''.join(self.store.data))
        self.assertNotIn('c1', ''.join(self.store.data))

    def test_isolated_users_apps_and_conversations(self):
        self.state.save_session({'source': b'alice'}, {})
        self.state.save_preferences({'notes': ['以后自然一点'], 'preferred_style': 'clean'})
        for other in (self.make('bob'), self.make('alice', 'c2'), self.make('alice', app='a2')):
            self.assertIsNone(other.load_session())
        self.assertEqual(self.make('bob').load_preferences(), {})
        self.assertEqual(self.make('alice', app='a2').load_preferences(), {})
        self.assertEqual(self.make('alice', 'c2').load_preferences()['notes'], ['以后自然一点'])

    def test_revision_rejects_stale_write_without_mutation(self):
        rev1 = self.state.save_session({'source': b'old'}, {})
        rev2 = self.state.save_session({'source': b'new'}, {}, rev1)
        snapshot = dict(self.store.data)
        with self.assertRaises(StateConflict):
            self.state.save_session({'source': b'bad'}, {}, rev1)
        self.assertEqual(self.store.data, snapshot)
        self.assertEqual(self.state.load_session()['revision'], rev2)

    def test_expiry_deletes_owner_and_allows_fresh_save(self):
        old = self.state.save_session({'source': b'old'}, {})
        self.time += SESSION_TTL + 1
        self.assertIsNone(self.state.load_session())
        self.assertFalse(self.store.data)
        self.state.save_session({'source': b'fresh'}, {}, old)
        self.assertEqual(self.state.load_session()['assets']['source'], b'fresh')

    def test_preference_expiry_and_whitelist(self):
        self.state.save_preferences({'notes': ['自然皮肤纹理'], 'preferred_style': '', 'updated_at': 0})
        self.assertEqual(self.state.load_preferences()['updated_at'], 1000)
        self.assertNotIn('preferred_style', self.state.load_preferences())
        self.time += PREFERENCES_TTL + 1
        self.assertEqual(self.state.load_preferences(), {})
        for invalid in ({'email': 'x'}, {'notes': ['x'] * 9}, {'preferred_style': 'https://site'},
                        {'notes': ['x' * 1601]}):
            with self.assertRaises(StateUnavailable):
                self.state.save_preferences(invalid)

    def test_missing_app_allows_session_but_disables_preferences(self):
        state = self.make('alice', app='')
        state.save_session({'source': b'original'}, {})
        self.assertEqual(state.load_session()['assets']['source'], b'original')
        for call in (state.load_preferences, lambda: state.save_preferences({'notes': []}), state.clear_preferences):
            with self.assertRaises(StateUnavailable):
                call()

    def test_too_large_invalid_assets_and_backend_failure(self):
        with self.assertRaises(StateTooLarge):
            self.state.save_session({'source': b'x' * MAX_SESSION_BYTES}, {})
        with self.assertRaises(StateUnavailable):
            self.state.save_session({'variant_a': b'output'}, {})
        self.store.fail = True
        with self.assertRaises(StateUnavailable):
            self.state.load_session()
        with self.assertRaises(StateUnavailable):
            self.state.save_session({'source': b'original'}, {})

    def test_bounded_slots_collision_eviction_never_leaks(self):
        states = [self.make('user-' + str(i)) for i in range(170)]
        for i, state in enumerate(states):
            self.time += 1
            state.save_session({'source': ('owner-' + str(i)).encode()}, {})
        self.assertLessEqual(len(self.store.data), 128)
        self.assertLessEqual(len([key for key in self.store.data if not key.endswith(':head')]), 64)
        hits, misses = 0, 0
        for i, state in enumerate(states):
            loaded = state.load_session()
            if loaded:
                hits += 1
                self.assertEqual(loaded['assets']['source'], ('owner-' + str(i)).encode())
            else:
                misses += 1
        self.assertGreater(misses, 0)
        self.assertGreater(hits, 0)
        self.assertEqual(MAX_CACHE_BYTES, 784 * 1024 * 1024 + 64 * 2048)

    def test_clear_only_matching_owners(self):
        other = self.make('bob')
        self.state.save_session({'source': b'alice'}, {})
        other.save_session({'source': b'bob'}, {})
        self.state.save_preferences({'notes': ['warm']})
        other.save_preferences({'notes': ['cool']})
        self.state.clear_session()
        self.state.clear_preferences()
        self.assertIsNone(self.state.load_session())
        self.assertEqual(self.state.load_preferences(), {})
        self.assertEqual(other.load_session()['assets']['source'], b'bob')
        self.assertEqual(other.load_preferences()['notes'], ['cool'])

    def test_payload_corruption_detected(self):
        self.state.save_session({'source': b'original'}, {})
        key = next(iter(self.store.data))
        self.store.data[key] = self.store.data[key][:-1] + b'X'
        with self.assertRaises(StateUnavailable):
            self.state.load_session()

    def test_race_after_set_detected_without_returning_foreign_bytes(self):
        original_set = self.store.set
        def lose_write(key, value):
            original_set(key, value)
            self.store.data[key] = b'concurrently evicted'
        self.store.set = lose_write
        with self.assertRaises(StateConflict):
            self.state.save_session({'source': b'original'}, {})
        self.assertIsNone(self.state.load_session())

    def test_full_bucket_reads_small_indexes_and_only_one_image_record(self):
        bucket = int(self.state._session_owner[:16], 16) % 8
        group = [self.state]
        counter = 0
        while len(group) < 8:
            candidate = self.make('bucket-user-' + str(counter))
            counter += 1
            if int(candidate._session_owner[:16], 16) % 8 == bucket:
                group.append(candidate)
        picture_bytes = 2 * 1024 * 1024
        for i, state in enumerate(group):
            self.time += 1
            state.save_session({'source': bytes([i]) * picture_bytes}, {})
        self.store.read_bytes = 0
        self.store.read_keys = []
        loaded = self.state.load_session()
        self.assertEqual(loaded['assets']['source'], bytes([0]) * picture_bytes)
        self.assertLess(self.store.read_bytes, picture_bytes + 20_000)
        self.assertEqual(len([key for key in self.store.read_keys if not key.endswith(':head')]), 1)
        self.assertEqual(len([key for key in self.store.read_keys if key.endswith(':head')]), 8)

    def test_wrong_owner_body_behind_valid_index_is_rejected(self):
        self.state.save_session({'source': b'alice-private'}, {})
        alice_key = next(key for key in self.store.data if not key.endswith(':head'))
        other = self.make('bob')
        other.save_session({'source': b'bob-private'}, {})
        bob_key = next(key for key, value in self.store.data.items()
                       if not key.endswith(':head') and b'bob-private' in value)
        self.store.data[alice_key] = self.store.data[bob_key]
        with self.assertRaises(StateConflict):
            self.state.load_session()
        # Clearing Alice's mismatched head must not delete the observed Bob body.
        self.state.clear_session()
        self.assertIn(alice_key, self.store.data)
        self.assertNotIn(alice_key + ':head', self.store.data)

    def test_same_owner_stale_index_new_body_is_rejected(self):
        self.state.save_session({'source': b'old'}, {})
        key = next(key for key in self.store.data if key.endswith(':head'))
        old_head = self.store.data[key]
        self.state.save_session({'source': b'new'}, {})
        self.store.data[key] = old_head
        with self.assertRaises(StateConflict):
            self.state.load_session()

    def test_head_write_failure_is_explicit_and_partial_body_not_returned(self):
        original_set = self.store.set
        def fail_head(key, value):
            if key.endswith(':head'):
                raise RuntimeError('head write failed')
            original_set(key, value)
        self.store.set = fail_head
        with self.assertRaises(StateUnavailable):
            self.state.save_session({'source': b'original'}, {})
        self.assertIsNone(self.state.load_session())


if __name__ == '__main__':
    unittest.main(verbosity=2)
