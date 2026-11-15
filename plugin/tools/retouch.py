"""Offline V2 renderer. Every variant is replayed from the original source."""
from collections.abc import Generator
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import math
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from PIL import Image, ImageDraw, ImageFont

from .retouch_engine import process_image, _load
from .retouch_state import RetouchState, StateUnavailable


def _reject_constant(value: str):
    raise ValueError('JSON must not contain NaN or Infinity.')


def _unique_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError('JSON objects must not contain duplicate keys: ' + str(key)[:80])
        output[key] = value
    return output


def _json(text, limit=32000):
    if not isinstance(text, str) or not text or len(text) > limit:
        raise ValueError(f'The plan must be JSON containing no more than {limit} characters.')
    result = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
    def check(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('JSON numbers must be finite.')
        if isinstance(value, dict):
            for entry in value.values():
                check(entry)
        elif isinstance(value, list):
            for entry in value:
                check(entry)
    check(result)
    return result


def _get_blob(value, required: bool = False) -> bytes | None:
    if value is None:
        if required:
            raise ValueError('Upload the original image.')
        return None
    blob = value.blob
    if not isinstance(blob, bytes) or not blob:
        raise ValueError('No valid image bytes could be read.')
    if len(blob) > 20 * 1024 * 1024:
        raise ValueError('Each image file must not exceed 20 MB.')
    return blob


def _identity(tool, parameters):
    """IDs are bound from sys variables; validate any SDK identity available."""
    values = {}
    for name in ('user_id', 'conversation_id'):
        value = parameters.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f'Bind {name} directly to its platform sys variable; it must not be empty.')
        values[name] = value
    runtime = getattr(tool, 'runtime', None)
    session = getattr(tool, 'session', None)
    checks = [(getattr(runtime, 'user_id', None), values['user_id'], 'user'),
              (getattr(session, 'conversation_id', None), values['conversation_id'], 'conversation')]
    for actual, requested, label in checks:
        if actual not in (None, '') and actual != requested:
            raise ValueError(f'The {label} ID does not match the platform context. Asset access was refused.')
    app_id = getattr(session, 'app_id', None) or ''
    if not isinstance(app_id, str):
        raise ValueError('The platform application ID is invalid.')
    return values['user_id'], values['conversation_id'], app_id


def _state(tool, parameters):
    user, conversation, app = _identity(tool, parameters)
    try:
        return RetouchState(tool.session.storage, user, conversation, app)
    except StateUnavailable:
        raise
    except Exception as exc:
        raise StateUnavailable('The local session cache is temporarily unavailable.') from exc


def _validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) - {'mode', 'explanation', 'variants'}:
        raise ValueError('Plans support only mode, explanation, and variants fields. Executable code is not accepted.')
    mode = plan.get('mode')
    if mode not in ('final', 'preview'):
        raise ValueError('Plan mode must be final or preview.')
    explanation = plan.get('explanation', '')
    if not isinstance(explanation, str) or len(explanation) > 1600:
        raise ValueError('The plan explanation must be text of no more than 1600 characters.')
    variants = plan.get('variants')
    expected_count = 1 if mode == 'final' else 2
    if not isinstance(variants, list) or len(variants) != expected_count:
        raise ValueError('final requires one variant; preview requires exactly two variants, A and B.')
    ids = []
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) != {'id', 'label', 'recipe'}:
            raise ValueError('Each variant must contain exactly id, label, and recipe.')
        if variant['id'] not in ('A', 'B'):
            raise ValueError('Variant IDs must be A or B.')
        if not isinstance(variant['label'], str) or not variant['label'].strip() or len(variant['label']) > 80:
            raise ValueError('Variant labels must contain 1 to 80 characters.')
        if not isinstance(variant['recipe'], dict) or variant['recipe'].get('version') != 2:
            raise ValueError('V2 edit plans require a recipe object with version=2.')
        ids.append(variant['id'])
    if mode == 'preview' and ids != ['A', 'B']:
        raise ValueError('Preview variants must be unique and ordered A, then B.')
    return plan


def _oriented_image(source):
    rgb, alpha = _load(source, 'image', [])
    image = Image.fromarray(rgb, 'RGB').convert('RGBA')
    image.putalpha(Image.fromarray(alpha, 'L'))
    return image


def _thumbnail(source, size=(1000, 1000)):
    image = _oriented_image(source)
    image.thumbnail(size, Image.Resampling.LANCZOS)
    background = Image.new('RGB', image.size, '#E8ECEA')
    background.paste(image.convert('RGB'), (0, 0), image.getchannel('A'))
    output = BytesIO()
    background.save(output, format='JPEG', quality=90)
    return output.getvalue()


def _review_sheet(source, results):
    """The sheet is only for inspection; deliverable pixels are untouched."""
    panels = [('ORIGINAL', source)] + results
    panel_w, panel_h, header, gap = 600, 650, 42, 12
    sheet = Image.new('RGB', (panel_w * len(panels) + gap * (len(panels) - 1), panel_h + header), '#E8ECEA')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=24)
    for index, (label, blob) in enumerate(panels):
        picture = _oriented_image(blob)
        picture.thumbnail((panel_w, panel_h), Image.Resampling.LANCZOS)
        origin = index * (panel_w + gap)
        draw.text((origin + 18, 9), label, fill='#1B3530', font=font)
        x = origin + (panel_w - picture.width) // 2
        y = header + (panel_h - picture.height) // 2
        sheet.paste(picture.convert('RGB'), (x, y), picture.getchannel('A'))
    output = BytesIO()
    sheet.save(output, format='JPEG', quality=90)
    return output.getvalue()


def _preview(source, result):
    return _review_sheet(source, [('AFTER', result)])


class RetouchTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        mode = ''
        try:
            _identity(self, tool_parameters)
            source = _get_blob(tool_parameters.get('source_image'), required=True)
            digest = sha256(source).hexdigest()
            if tool_parameters.get('source_sha256') != digest:
                raise ValueError('The original does not match the prepared asset fingerprint. Upload the original again.')
            assets = {'source': source}
            for role in ('reference', 'mask', 'background'):
                blob = _get_blob(tool_parameters.get(role + '_image'))
                if blob is not None:
                    assets[role] = blob
            plan = _validate_plan(_json(tool_parameters.get('plan_json')))
            mode = plan['mode']
            cache_warnings = []
            store = None
            cached = None
            try:
                store = _state(self, tool_parameters)
                cached = store.load_session()
            except StateUnavailable:
                cache_warnings.append('The local session cache is unavailable. This turn uses the supplied original; upload the original and required assets again next turn.')
            expected = tool_parameters.get('context_revision') or ''
            if not isinstance(expected, str) or len(expected) > 256:
                raise ValueError('context_revision has an invalid format.')
            can_save = store is not None
            if cached:
                old_source = cached.get('assets', {}).get('source')
                same_source = isinstance(old_source, bytes) and sha256(old_source).hexdigest() == digest
                if not same_source or (expected and cached.get('revision') != expected):
                    can_save = False
                    cache_warnings.append('Session assets or revision have changed. This turn processes only the supplied original and does not overwrite another turn\'s cache; upload the assets again next turn.')
            elif expected:
                cache_warnings.append('The previous cache expired or is missing. It was rebuilt from the supplied original; upload the assets again if continuation is unavailable next turn.')
            # Compute every variant and attachment before yielding any file.
            outputs = []
            for variant in plan['variants']:
                result, report, selection = process_image(
                    source, variant['recipe'], assets.get('mask'), assets.get('background'), assets.get('reference'))
                outputs.append((variant, result, report, selection))
            display = [('AFTER' if mode == 'final' else item[0]['id'], item[1]) for item in outputs]
            review = _review_sheet(source, display)
            attachments = []
            if mode == 'preview':
                for variant, result, _, _ in outputs:
                    attachments.append((_thumbnail(result), 'image/jpeg', f"candidate_{variant['id']}.jpg"))
            else:
                attachments.append((outputs[0][1], 'image/png', 'xiaorun_retouched.png'))
                if outputs[0][3]:
                    attachments.append((outputs[0][3], 'image/png', 'selection_mask.png'))
            attachments.append((review, 'image/jpeg', 'review_preview.jpg'))
            if can_save:
                meta = deepcopy(cached.get('meta', {})) if cached else {}
                meta['source_sha256'] = digest
                if mode == 'preview':
                    meta['pending_variants'] = deepcopy(plan['variants'])
                    meta['last_selected_id'] = ''
                else:
                    meta['last_plan'] = deepcopy(plan)
                    meta['pending_variants'] = []
                    meta['last_selected_id'] = plan['variants'][0]['id']
                try:
                    store.save_session(assets, meta, expected_revision=cached.get('revision') if cached else None)
                except StateUnavailable:
                    cache_warnings.append('Output was generated, but the plan or assets were not cached. Upload the original and required assets next turn; A/B selection requires new previews.')
            labels = '; '.join(f"{v['id']}: {v['label']}" for v, _, _, _ in outputs)
            if mode == 'preview':
                summary = 'Two low-resolution previews were generated independently from the original: ' + labels + '.'
                if not cache_warnings:
                    summary += 'Reply with "select A" or "select B" to generate a full-resolution result, or describe further changes.'
            else:
                summary = outputs[0][2].get('summary', 'The result was recalculated from the original.')
            if cache_warnings:
                summary += '\n' + '\n'.join(dict.fromkeys(cache_warnings))
            report = {'status': 'success', 'mode': mode, 'source_sha256': digest,
                      'variants': [{'id': v['id'], 'label': v['label'], 'report': r} for v, _, r, _ in outputs],
                      'cache_warnings': list(dict.fromkeys(cache_warnings)), 'summary': summary}
            report_text = json.dumps(report, ensure_ascii=False, allow_nan=False)
            audit = json.dumps({'plan': plan, 'report': report}, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
            attachments.append((audit, 'application/json', 'recipe.json'))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            message = 'No new result was generated this turn: ' + str(exc)[:1200]
            yield from self._error(message, mode)
            return
        except Exception as exc:
            message = 'Local image processing failed; no new result was delivered. Error type: ' + type(exc).__name__
            yield from self._error(message, mode)
            return
        for blob, mime_type, filename in attachments:
            yield self.create_blob_message(blob, meta={'mime_type': mime_type, 'filename': filename})
        yield self.create_variable_message('status', 'success')
        yield self.create_variable_message('mode', mode)
        yield self.create_variable_message('summary', summary)
        yield self.create_variable_message('report_json', report_text)
        yield self.create_json_message(report)
        yield self.create_text_message(summary)

    def _error(self, message, mode):
        yield self.create_variable_message('status', 'error')
        yield self.create_variable_message('mode', mode)
        yield self.create_variable_message('summary', message)
        yield self.create_variable_message('report_json', json.dumps({'status': 'error', 'mode': mode, 'error': message}, ensure_ascii=False))
        yield self.create_text_message(message)
