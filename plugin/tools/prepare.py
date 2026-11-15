"""Prepare explicit asset roles and bounded local conversation context."""
from collections.abc import Generator
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from PIL import Image

from .retouch import _get_blob, _identity, _json, _state, _thumbnail, _validate_plan
from .retouch_engine import STYLE_PRESETS, _load
from .retouch_state import StateUnavailable

ROLES = ('source', 'reference', 'mask', 'background')
ACTIONS = ('edit', 'select', 'reset', 'reset_preferences', 'preferences', 'help')


def _request(value):
    allowed = {'action', 'asset_roles', 'selected_id', 'preference_notes', 'preferred_style', 'style_hints'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('The asset request contains unsupported fields.')
    if value.get('action') not in ACTIONS:
        raise ValueError('The requested action is not supported.')
    roles = value.get('asset_roles', [])
    if not isinstance(roles, list) or any(not isinstance(x, str) or x not in ROLES for x in roles) or len(set(roles)) != len(roles):
        raise ValueError('asset_roles must be a list of unique source/reference/mask/background roles.')
    selected = value.get('selected_id', '')
    if selected not in ('', 'A', 'B'):
        raise ValueError('Select an existing A or B variant.')
    notes = value.get('preference_notes', [])
    if not isinstance(notes, list) or len(notes) > 8 or any(not isinstance(x, str) or not x.strip() for x in notes) or sum(len(x) for x in notes) > 1600:
        raise ValueError('Preferences must contain at most 8 text notes totaling no more than 1600 characters.')
    style = value.get('preferred_style', '')
    if not isinstance(style, str) or style not in ('', *STYLE_PRESETS):
        raise ValueError('preferred_style is not a valid built-in style.')
    hints = value.get('style_hints', [])
    if not isinstance(hints, list) or len(hints) > 3 or any(not isinstance(x, str) or x not in STYLE_PRESETS for x in hints):
        raise ValueError('style_hints may contain at most 3 valid style IDs.')
    return {'action': value['action'], 'asset_roles': roles, 'selected_id': selected,
            'preference_notes': notes, 'preferred_style': style, 'style_hints': list(dict.fromkeys(hints))}


def _style_context(hints, preferences):
    catalog = [{'id': key, 'name': value['label']} for key, value in STYLE_PRESETS.items()]
    cards = []
    try:
        data = json.loads((Path(__file__).resolve().parents[1] / 'resources' / 'style_guide.json').read_text(encoding='utf-8'))
        values = data.get('style_cards', data.get('cards', [])) if isinstance(data, dict) else data
        if isinstance(values, dict):
            values = [{'id': key, **value} for key, value in values.items() if isinstance(value, dict)]
        if isinstance(values, list):
            cards = [entry for entry in values if isinstance(entry, dict) and entry.get('id') in STYLE_PRESETS]
            catalog = [{'id': c['id'], 'name': c.get('name', c['id']), 'goal': c.get('goal', '')} for c in cards] or catalog
    except (OSError, ValueError, TypeError):
        pass
    chosen = list(hints)
    preferred = preferences.get('preferred_style')
    if preferred in STYLE_PRESETS and preferred not in chosen:
        chosen.append(preferred)
    if not chosen:
        chosen = ['clean_natural', 'portrait_neutral']
    return [c for c in cards if c['id'] in chosen[:3]], catalog


def _format(blob, label):
    try:
        with Image.open(BytesIO(blob)) as image:
            extension, mime = {'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'),
                               'WEBP': ('webp', 'image/webp')}[image.format]
    except (KeyError, OSError) as exc:
        raise ValueError(f'{label} must be a static JPEG, PNG, or WebP image.') from exc
    _load(blob, label, [], preserve_profile=label != 'mask')
    return extension, mime


def _explicit_preference_request(query):
    positive = re.search(r'记住|以后|今后|默认|我的偏好', query)
    # Negating an editing effect ("记住，以后不要过度磨皮") is an
    # explicit negative preference, not a refusal to save preferences.
    negative = re.search(r'(?:不要|别|无需|不用|不必|不需要|不想|暂不|不)[^，。；！？,.!?;]{0,8}(?:记住|保存|记忆|记录|默认|偏好)', query)
    return bool(positive and not negative)


def _merge_notes(previous, added):
    values = []
    for item in [*previous, *added]:
        if item in values:
            values.remove(item)
        values.append(item)
    while len(values) > 8 or sum(map(len, values)) > 1600:
        values.pop(0)
    return values


class PrepareTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        result = {'status': 'error', 'summary': '', 'context_json': '{}', 'selected_recipe_json': '',
                  'source_sha256': '', 'context_revision': '', 'continuity': 'no',
                  **{'has_' + role: 0 for role in ROLES}}
        attachments = []
        try:
            _identity(self, tool_parameters)
            request = _request(_json(tool_parameters.get('request_json'), 24000))
            query = tool_parameters.get('query')
            if not isinstance(query, str) or len(query) > 8000:
                raise ValueError('Bind query directly to sys.query; it must not exceed 8000 characters.')
            incoming = tool_parameters.get('images') or []
            if not isinstance(incoming, (list, tuple)) or len(incoming) > 4:
                raise ValueError('Provide at most 4 images per turn and specify each source, reference, mask, or background role.')
            roles = request['asset_roles']
            if not roles and incoming:
                if len(incoming) >= 3:
                    raise ValueError('For 3 or more uploads, explicitly specify the role of every image.')
                roles = ['source'] if len(incoming) == 1 else ['source', 'reference']
            if len(roles) != len(incoming):
                raise ValueError('The number of roles does not match the number of uploaded images. Specify one role per image.')
            supplied = {}
            for role, file in zip(roles, incoming):
                blob = _get_blob(file, required=True)
                _format(blob, 'mask' if role == 'mask' else role)
                supplied[role] = blob
            warnings = []
            store = None
            cached = None
            preferences = {}
            try:
                store = _state(self, tool_parameters)
                cached = store.load_session()
            except StateUnavailable:
                warnings.append('The local session cache is unavailable. Current uploads can still be processed; upload the original and required assets again for the next turn.')
            if store is not None:
                try:
                    preferences = store.load_preferences()
                except StateUnavailable:
                    warnings.append('Saved preferences are unavailable. This turn will use only the current request.')
            action = request['action']
            if action == 'reset_preferences':
                if store is None:
                    raise ValueError('Preference storage is unavailable. Preferences have not been cleared; try again later.')
                try:
                    store.clear_preferences()
                except StateUnavailable as exc:
                    raise ValueError('Preference deletion could not be confirmed. Try again later.') from exc
                preferences = {}
                result.update(status='control', summary='Saved style preferences have been cleared. Images in this conversation remain available.')
            elif action == 'reset':
                if store is None:
                    raise ValueError('The session cache is unavailable. Deletion has not been confirmed; try again later or start a new conversation.')
                try:
                    store.clear_session()
                except StateUnavailable as exc:
                    raise ValueError('Session deletion could not be confirmed. Try again later or start a new conversation.') from exc
                cached = None
                result.update(status='control', summary='The original, reference assets, and edit plans for this conversation have been cleared. Saved preferences are retained.')
            else:
                notes = request['preference_notes']
                if (notes or request['preferred_style']) and _explicit_preference_request(query):
                    updated = {'notes': _merge_notes(preferences.get('notes', []), notes),
                               'preferred_style': request['preferred_style'] or preferences.get('preferred_style', '')}
                    if store is not None:
                        try:
                            store.save_preferences(updated)
                            preferences = updated
                        except StateUnavailable:
                            warnings.append('Preferences requested in this turn were not saved. The current editing request still applies.')
                    else:
                        warnings.append('Preferences were not saved. The current editing request still applies.')
                if action == 'preferences':
                    result.update(status='control', summary='Currently saved preferences: ' + json.dumps(preferences, ensure_ascii=False))
                elif action == 'help':
                    _, catalog = _style_context([], preferences)
                    style_lines = '\n'.join(f"{index}. {card['name']}: {card.get('goal') or 'A restrained color treatment adapted to the original image.'}" for index, card in enumerate(catalog, 1))
                    result.update(status='control', summary='Eight built-in style directions adapt to the original image and your preservation requirements. They are starting points, not guaranteed visual results:\n' + style_lines + '\n\nOther operations include reference color matching, local brightening, mild smoothing, small-area inpainting, constrained background replacement, and local liquify. Upload the original and describe your request. For ambiguous styles, compare A/B previews before producing a full-resolution PNG.')
                else:
                    assets = deepcopy(cached.get('assets', {})) if cached else {}
                    meta = deepcopy(cached.get('meta', {})) if cached else {}
                    previous_revision = cached.get('revision') if cached else None
                    if 'source' in supplied:
                        # A new original always starts a fresh material set.
                        assets, meta = {}, {}
                        if request['selected_id']:
                            raise ValueError('A new original was uploaded. Existing A/B choices cannot be applied to it; generate a new plan first.')
                        if store is not None:
                            try:
                                store.clear_session()
                                previous_revision = None
                            except StateUnavailable:
                                warnings.append('Deletion of the old session cache was not confirmed. Only new uploads will be used this turn; upload them again next turn to avoid referring to an old image.')
                    elif supplied:
                        if request['selected_id']:
                            raise ValueError('The reference, mask, or background changed this turn. Existing A/B previews no longer match these assets; generate a new plan first.')
                        # A/B must refer to the exact materials used for those
                        # previews, even when the original photo is unchanged.
                        meta['pending_variants'] = []
                        meta['last_selected_id'] = ''
                    assets.update(supplied)
                    source = assets.get('source')
                    if not source:
                        result.update(status='control', summary='No original is available. Upload the photo to edit; identify any reference image as a color reference.')
                    else:
                        selected = request['selected_id']
                        if action == 'select' and not selected:
                            raise ValueError('Explicitly select an existing A or B variant.')
                        if selected:
                            pending = meta.get('pending_variants', [])
                            matching = [v for v in pending if isinstance(v, dict) and v.get('id') == selected]
                            if len(matching) != 1:
                                raise ValueError('No valid saved A/B preview matches this selection. Generate previews again or upload the original.')
                            selected_plan = _validate_plan({'mode': 'final', 'explanation': 'Generate the selected full-resolution result from the original.', 'variants': [deepcopy(matching[0])]})
                            meta['last_plan'] = deepcopy(selected_plan)
                            meta['last_selected_id'] = selected
                            if action == 'select':
                                result['selected_recipe_json'] = json.dumps(selected_plan, ensure_ascii=False, allow_nan=False)
                        digest = sha256(source).hexdigest()
                        meta['source_sha256'] = digest
                        meta['previous_instruction'] = meta.get('current_instruction', '')
                        meta['current_instruction'] = query
                        revision = ''
                        if store is not None:
                            try:
                                revision = store.save_session(assets, meta, expected_revision=previous_revision)
                                result['continuity'] = 'yes'
                            except StateUnavailable:
                                warnings.append('Assets or context were not cached. This turn can still proceed; upload the original and required assets again next turn.')
                        style_cards, style_catalog = _style_context(request['style_hints'], preferences)
                        last_plan = meta.get('last_plan', {})
                        last_variants = last_plan.get('variants', []) if isinstance(last_plan, dict) else []
                        last_recipe = deepcopy(last_variants[0].get('recipe')) if len(last_variants) == 1 and isinstance(last_variants[0], dict) else None
                        context = {'preferences': preferences, 'style_cards': style_cards, 'style_catalog': style_catalog,
                                   'last_recipe': last_recipe, 'previous_instruction': meta.get('previous_instruction', ''),
                                   'pending_variants': meta.get('pending_variants', []), 'current_instruction': query,
                                   'continuity': result['continuity']}
                        result.update(status='ready', source_sha256=digest, context_revision=revision,
                                      context_json=json.dumps(context, ensure_ascii=False, allow_nan=False),
                                      summary='The original and explicitly assigned assets are ready.')
                        for role in ROLES:
                            if role in assets:
                                extension, mime = _format(assets[role], 'mask' if role == 'mask' else role)
                                attachments.append((assets[role], mime, f'{role}_original.{extension}'))
                                result['has_' + role] = 1
                        for role in ('source', 'reference'):
                            if role in assets:
                                attachments.append((_thumbnail(assets[role], (1400, 1400)), 'image/jpeg', f'{role}_preview.jpg'))
            if warnings:
                result['summary'] += '\n' + '\n'.join(dict.fromkeys(warnings))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            result.update(status='error', summary='Asset preparation did not complete: ' + str(exc)[:1200])
            attachments = []
            for role in ROLES:
                result['has_' + role] = 0
        except Exception as exc:
            result.update(status='error', summary='Local asset preparation failed; no assets were delivered. Error type: ' + type(exc).__name__)
            attachments = []
            for role in ROLES:
                result['has_' + role] = 0
        for blob, mime, filename in attachments:
            yield self.create_blob_message(blob, meta={'mime_type': mime, 'filename': filename})
        for name, value in result.items():
            yield self.create_variable_message(name, value)
        yield self.create_text_message(result['summary'])
