"""Bounded, tenant-local retouch cache over the Dify SDK byte KV store.

The caller supplies IDs bound directly to the authenticated workflow/runtime,
never IDs selected by the model. A whole record (metadata + original image bytes)
is replaced with one SDK set(). Owner checks precede every payload return.

SDK storage has no compare-and-set or native TTL. Revision checks are optimistic;
concurrent requests may evict/lose cached work, but cannot read another owner's
record. Fixed slot counts keep abandoned/expired records bounded without list().
This is a convenience cache, not a durable archive. The original upload remains
the recovery path when a record expires, is evicted, is too large, or storage fails.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
import time
from typing import Any, Callable
import uuid


MIB = 1024 * 1024
SESSION_TTL = 24 * 60 * 60
PREFERENCES_TTL = 90 * 24 * 60 * 60
SESSION_SLOTS = 64
PREFERENCES_SLOTS = 4096
WAYS = 8
MAX_SESSION_BYTES = 12 * MIB
MAX_PREFERENCES_BYTES = 4 * 1024
MAX_INDEX_BYTES = 2 * 1024
MAX_META_BYTES = 96 * 1024
MAX_HEADER_BYTES = 100 * 1024
MAX_CACHE_BYTES = (SESSION_SLOTS * (MAX_SESSION_BYTES + MAX_INDEX_BYTES)
                   + PREFERENCES_SLOTS * MAX_PREFERENCES_BYTES)
ASSET_NAMES = frozenset({'source', 'reference', 'mask', 'background'})
MAGIC = b'LRTSTATE2\x00'
PREFIX_BYTES = len(MAGIC) + 4
STYLE_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
HASH_RE = re.compile(r'^[0-9a-f]{64}$')
REVISION_RE = re.compile(r'^[0-9a-f]{32}$')


class StateUnavailable(RuntimeError):
    """Cache failure; allow current uploaded-file work, request a new upload later."""


class StateTooLarge(StateUnavailable):
    """The original bytes or metadata exceed the documented cache limit."""


class StateConflict(StateUnavailable):
    """The expected cache revision changed; do not overwrite it knowingly."""


@dataclass
class _Slot:
    key: str
    data: bytes | None
    header: dict | None


def _json_bytes(value: Any, limit: int, label: str) -> bytes:
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                            separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise StateUnavailable(label + ' must be finite, serializable JSON.') from exc
    if len(result) > limit:
        raise StateTooLarge(label + ' exceeds the cache limit. This turn can continue; upload the original again for later turns.')
    return result


def _owner(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


class RetouchState:
    """Use ``RetouchState(tool.session.storage, sys_user, sys_conversation, sys_app)``.

    The manifest must enable storage and request 1 GiB. Maximum application value
    bytes are 784.125 MiB (64 * (12 MiB + 2 KiB) + 4096 * 4 KiB),
    leaving quota overhead room. Small session indexes avoid reading other
    users' large image records while searching the eight candidate slots.
    The daemon's actual storage backend and installation policy remain authoritative.
    Binary values are hex-encoded by the SDK in transit (about 2x wire size).
    """

    def __init__(self, storage, user_id: str, conversation_id: str, app_id: str = '',
                 *, clock: Callable[[], float] | None = None):
        for label, value in (('user_id', user_id), ('conversation_id', conversation_id)):
            if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > 512:
                raise StateUnavailable('Missing a trusted ' + label + '; session memory is disabled. Upload the required images again.')
        if not isinstance(app_id, str) or len(app_id.encode('utf-8')) > 512:
            raise StateUnavailable('The application ID is invalid; image memory is disabled.')
        self.storage = storage
        self._clock = clock or time.time
        self._has_app_scope = bool(app_id.strip())
        self._session_owner = _owner('retouch-state-v2', app_id, user_id, conversation_id)
        self._preferences_owner = _owner('retouch-preferences-v2', app_id, user_id)

    def _now(self) -> float:
        now = float(self._clock())
        if not math.isfinite(now) or now < 0:
            raise StateUnavailable('The cache clock is invalid. Upload the required images again.')
        return now

    @staticmethod
    def _settings(kind: str):
        if kind == 'session':
            return SESSION_SLOTS, MAX_SESSION_BYTES, SESSION_TTL, 's'
        return PREFERENCES_SLOTS, MAX_PREFERENCES_BYTES, PREFERENCES_TTL, 'p'

    def _read(self, key: str) -> bytes | None:
        try:
            if not self.storage.exist(key):
                return None
            value = self.storage.get(key)
            if not isinstance(value, bytes):
                raise TypeError('Storage did not return bytes')
            return value
        except Exception as exc:
            raise StateUnavailable('The local image cache is unavailable. This turn can proceed with a supplied original; upload it again for later turns.') from exc

    def _write(self, key: str, value: bytes) -> None:
        try:
            self.storage.set(key, value)
        except Exception as exc:
            raise StateUnavailable('Image memory could not be saved, possibly because of the platform quota. This turn can continue; upload assets again for later turns.') from exc

    def _delete(self, key: str) -> None:
        try:
            self.storage.delete(key)
        except Exception as exc:
            raise StateUnavailable('Image memory could not be cleared. Try again later; upload assets again for subsequent edits.') from exc

    def _header(self, value: bytes | None, kind: str) -> dict | None:
        """Parse only a bounded header. Foreign/corrupt records are never returned."""
        _, limit, ttl, _ = self._settings(kind)
        if value is None:
            return None
        if not PREFIX_BYTES <= len(value) <= limit or not value.startswith(MAGIC):
            return None
        length = struct.unpack('>I', value[len(MAGIC):PREFIX_BYTES])[0]
        if not 0 < length <= MAX_HEADER_BYTES or PREFIX_BYTES + length > len(value):
            return None
        try:
            header = json.loads(value[PREFIX_BYTES:PREFIX_BYTES + length])
            if not isinstance(header, dict) or header.get('kind') != kind or header.get('v') != 2:
                return None
            if not isinstance(header.get('owner'), str) or not HASH_RE.fullmatch(header['owner']):
                return None
            if not isinstance(header.get('revision'), str) or not REVISION_RE.fullmatch(header['revision']):
                return None
            created, expires = header.get('created'), header.get('expires')
            if isinstance(created, bool) or isinstance(expires, bool):
                return None
            if not isinstance(created, (int, float)) or not isinstance(expires, (int, float)):
                return None
            if not math.isfinite(created) or not math.isfinite(expires) or created < 0:
                return None
            if abs(expires - created - ttl) > 0.001:
                return None
            header['_offset'] = PREFIX_BYTES + length
            return header
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
            return None

    def _scan(self, kind: str, owner: str) -> list[_Slot]:
        slots, _, _, short = self._settings(kind)
        bucket = int(owner[:16], 16) % (slots // WAYS)
        result = []
        for way in range(WAYS):
            key = f'lrt2:{short}:{bucket:04x}:{way}'
            if kind == 'session':
                value = self._read(key + ':head')
                header = self._index_header(value)
                result.append(_Slot(key, None, header))
            else:
                value = self._read(key)
                result.append(_Slot(key, value, self._header(value, kind)))
        return result

    def _index_header(self, value: bytes | None) -> dict | None:
        if value is None or len(value) > MAX_INDEX_BYTES:
            return None
        header = self._header(value, 'session')
        if (not header or header.get('index') is not True
                or type(header.get('record_bytes')) is not int
                or not PREFIX_BYTES <= header['record_bytes'] <= MAX_SESSION_BYTES
                or not isinstance(header.get('payload_sha256'), str)
                or not HASH_RE.fullmatch(header['payload_sha256'])):
            return None
        return header

    def _index(self, packed: bytes) -> bytes:
        header = self._header(packed, 'session')
        if not header:
            raise StateUnavailable('The image cache index could not be generated.')
        small = {key: header[key] for key in
                 ('v', 'kind', 'owner', 'revision', 'created', 'expires', 'payload_sha256')}
        small.update(index=True, record_bytes=len(packed))
        encoded = _json_bytes(small, MAX_INDEX_BYTES - PREFIX_BYTES, 'Image cache index')
        return MAGIC + struct.pack('>I', len(encoded)) + encoded

    def _session_body(self, slot: _Slot, owner: str) -> _Slot:
        """Fetch one selected record; an index never grants access by itself."""
        data = self._read(slot.key)
        header = self._header(data, 'session')
        index = slot.header
        if (not index or index['owner'] != owner or not header
                or header['owner'] != owner or header.get('index') is True
                or header['revision'] != index['revision']
                or header.get('payload_sha256') != index['payload_sha256']
                or len(data) != index['record_bytes']):
            raise StateConflict('The image cache index and original changed concurrently. No image was read; upload the original again.')
        hydrated = _Slot(slot.key, data, header)
        self._payload(hydrated, owner)
        return hydrated

    def _clear_session_slot(self, slot: _Slot, owner: str) -> None:
        fresh = self._index_header(self._read(slot.key + ':head'))
        if not fresh or fresh['owner'] != owner:
            return
        # Only delete a body observed to belong to this same owner/revision.
        # No CAS is available, so concurrent changes may still lose cache.
        data = self._read(slot.key)
        body = self._header(data, 'session')
        if body and body['owner'] == owner and body['revision'] == fresh['revision']:
            self._delete(slot.key)
        latest = self._index_header(self._read(slot.key + ':head'))
        if latest and latest['owner'] == owner and latest['revision'] == fresh['revision']:
            self._delete(slot.key + ':head')

    @staticmethod
    def _find(slots: list[_Slot], owner: str, now: float) -> _Slot | None:
        matches = [slot for slot in slots if slot.header
                   and slot.header['owner'] == owner and slot.header['expires'] > now]
        return max(matches, key=lambda slot: slot.header['created']) if matches else None

    def _clear_owner(self, kind: str, owner: str) -> None:
        for slot in self._scan(kind, owner):
            if slot.header and slot.header['owner'] == owner:
                if kind == 'session':
                    self._clear_session_slot(slot, owner)
                    continue
                # Recheck before deletion. SDK offers no CAS; a race may evict cache,
                # never authorize returning another owner's payload.
                fresh = self._header(self._read(slot.key), kind)
                if fresh and fresh['owner'] == owner:
                    self._delete(slot.key)

    def _load(self, kind: str, owner: str) -> _Slot | None:
        now = self._now()
        slots = self._scan(kind, owner)
        current = self._find(slots, owner, now)
        if current:
            return self._session_body(current, owner) if kind == 'session' else current
        for slot in slots:
            if slot.header and slot.header['owner'] == owner and slot.header['expires'] <= now:
                if kind == 'session':
                    fresh = self._index_header(self._read(slot.key + ':head'))
                    if fresh and fresh['owner'] == owner and fresh['expires'] <= now:
                        self._clear_session_slot(slot, owner)
                    continue
                fresh = self._header(self._read(slot.key), kind)
                if fresh and fresh['owner'] == owner and fresh['expires'] <= now:
                    self._delete(slot.key)
        return None

    def _pack(self, kind: str, owner: str, payload: bytes, fields: dict) -> tuple[bytes, str]:
        _, limit, ttl, _ = self._settings(kind)
        now = self._now()
        revision = uuid.uuid4().hex
        header = dict(fields, v=2, kind=kind, owner=owner, revision=revision,
                      created=now, expires=now + ttl,
                      payload_sha256=hashlib.sha256(payload).hexdigest())
        encoded_header = _json_bytes(header, MAX_HEADER_BYTES, 'Image memory metadata')
        packed = MAGIC + struct.pack('>I', len(encoded_header)) + encoded_header + payload
        if len(packed) > limit:
            raise StateTooLarge('The original, reference, mask, and background exceed the combined 12 MiB cache limit. This turn can continue; upload assets again for later turns.'
                                if kind == 'session' else 'Saved preferences exceed the 4 KiB cache limit.')
        return packed, revision

    def _save(self, kind: str, owner: str, packed: bytes, revision: str,
              expected_revision: str | None = None) -> None:
        if expected_revision is not None and (not isinstance(expected_revision, str)
                                              or not REVISION_RE.fullmatch(expected_revision)):
            raise StateConflict('The image memory revision is invalid. Read the current session state again.')
        now = self._now()
        slots = self._scan(kind, owner)
        current = self._find(slots, owner, now)
        if current and expected_revision is not None and current.header['revision'] != expected_revision:
            raise StateConflict('Another turn updated image memory. It was not overwritten; refresh and retry or upload the original again.')
        if current:
            target = current
        else:
            reusable = [slot for slot in slots if not slot.header or slot.header['expires'] <= now]
            target = reusable[0] if reusable else min(slots, key=lambda slot: slot.header['created'])
        self._write(target.key, packed)
        if kind == 'session':
            self._write(target.key + ':head', self._index(packed))
            verified = self._index_header(self._read(target.key + ':head'))
        else:
            verified = self._header(self._read(target.key), kind)
        if not verified or verified['owner'] != owner or verified['revision'] != revision:
            raise StateConflict('The session cache was replaced concurrently. This turn can continue; upload the original again for later turns.')
        if kind == 'session':
            self._session_body(_Slot(target.key, None, verified), owner)

    @staticmethod
    def _payload(slot: _Slot, owner: str) -> bytes:
        if not slot.header or slot.header['owner'] != owner or slot.data is None:
            raise StateUnavailable('Image memory ownership validation failed. Upload the required images again.')
        payload = slot.data[slot.header['_offset']:]
        if hashlib.sha256(payload).hexdigest() != slot.header.get('payload_sha256'):
            raise StateUnavailable('Image memory integrity validation failed. Upload the original again.')
        return payload

    def load_session(self) -> dict | None:
        slot = self._load('session', self._session_owner)
        if slot is None:
            return None
        payload = self._payload(slot, self._session_owner)
        descriptors = slot.header.get('assets')
        meta = slot.header.get('meta')
        if not isinstance(descriptors, list) or len(descriptors) > len(ASSET_NAMES) or not isinstance(meta, dict):
            raise StateUnavailable('The session image record format is invalid. Upload the original again.')
        assets, offset = {}, 0
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise StateUnavailable('The session image record format is invalid. Upload the original again.')
            name, size = descriptor.get('name'), descriptor.get('size')
            if name not in ASSET_NAMES or name in assets or type(size) is not int or size <= 0:
                raise StateUnavailable('The session image record format is invalid. Upload the original again.')
            if offset + size > len(payload):
                raise StateUnavailable('The session image record is incomplete. Upload the original again.')
            assets[name] = payload[offset:offset + size]
            offset += size
        if offset != len(payload):
            raise StateUnavailable('The session image record is incomplete. Upload the original again.')
        _json_bytes(meta, MAX_META_BYTES, 'Session editing parameters')
        return {'assets': assets, 'meta': meta, 'revision': slot.header['revision']}

    def save_session(self, assets: dict[str, bytes], meta: dict,
                     expected_revision: str | None = None) -> str:
        if not isinstance(assets, dict) or set(assets) - ASSET_NAMES:
            raise StateUnavailable('Only the original, reference, mask, and background may be cached.')
        if not isinstance(meta, dict):
            raise StateUnavailable('Session metadata must be a JSON object.')
        # Round-trip isolates caller mutation and rejects non-finite JSON values.
        clean_meta = json.loads(_json_bytes(meta, MAX_META_BYTES, 'Session editing parameters'))
        chunks, descriptors, total = [], [], 0
        for name in sorted(assets):
            value = assets[name]
            if not isinstance(value, bytes) or not value:
                raise StateUnavailable('Cached images must contain nonempty original bytes.')
            total += len(value)
            if total > MAX_SESSION_BYTES:
                raise StateTooLarge('Image assets exceed the 12 MiB cache limit. This turn can continue; upload them again for later turns.')
            chunks.append(value)
            descriptors.append({'name': name, 'size': len(value)})
        packed, revision = self._pack('session', self._session_owner, b''.join(chunks),
                                      {'assets': descriptors, 'meta': clean_meta})
        self._save('session', self._session_owner, packed, revision, expected_revision)
        return revision

    def clear_session(self) -> None:
        self._clear_owner('session', self._session_owner)

    def _preferences(self, preferences: dict, *, saving: bool) -> dict:
        if not isinstance(preferences, dict) or set(preferences) - {'notes', 'preferred_style', 'updated_at'}:
            raise StateUnavailable('Preference memory supports only style notes and a preferred style, not identity information or images.')
        notes = preferences.get('notes', [])
        if not isinstance(notes, list) or len(notes) > 8 or any(not isinstance(n, str) for n in notes):
            raise StateUnavailable('At most 8 text preference notes are allowed.')
        notes = [note.strip() for note in notes if note.strip()]
        if sum(len(note) for note in notes) > 1600:
            raise StateTooLarge('Preference notes must not exceed 1600 characters in total.')
        if any(any(ord(char) < 32 and char not in '\n\t' for char in note) for note in notes):
            raise StateUnavailable('Preference notes contain unsupported control characters.')
        result = {'notes': notes}
        style = preferences.get('preferred_style')
        if style not in (None, ''):
            if not isinstance(style, str) or not STYLE_RE.fullmatch(style):
                raise StateUnavailable('The preferred style must be a valid style ID.')
            result['preferred_style'] = style
        updated = self._now() if saving else preferences.get('updated_at')
        if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not math.isfinite(updated):
            raise StateUnavailable('The preference update timestamp is invalid.')
        result['updated_at'] = updated
        return result

    def load_preferences(self) -> dict:
        if not self._has_app_scope:
            raise StateUnavailable('No trusted application ID is available. Saved preferences are disabled; current editing and conversation updates can continue.')
        slot = self._load('preferences', self._preferences_owner)
        if slot is None:
            return {}
        try:
            data = json.loads(self._payload(slot, self._preferences_owner))
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as exc:
            raise StateUnavailable('The saved preference record is invalid. State your preferences again.') from exc
        return self._preferences(data, saving=False)

    def save_preferences(self, preferences: dict) -> None:
        if not self._has_app_scope:
            raise StateUnavailable('No trusted application ID is available. Preferences were not saved; this edit can continue.')
        # The wrapper only calls this after an explicit long-term-memory request.
        clean = self._preferences(preferences, saving=True)
        payload = _json_bytes(clean, MAX_PREFERENCES_BYTES, 'Saved style preferences')
        packed, revision = self._pack('preferences', self._preferences_owner, payload, {})
        self._save('preferences', self._preferences_owner, packed, revision)

    def clear_preferences(self) -> None:
        if not self._has_app_scope:
            raise StateUnavailable('No trusted application ID is available, so saved preferences cannot be located or cleared.')
        self._clear_owner('preferences', self._preferences_owner)
