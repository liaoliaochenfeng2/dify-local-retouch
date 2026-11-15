"""Focused V2 wrapper checks: raw assets, previews, identifiers and atomic output."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest

from PIL import Image, ImageCms

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import test_v2_integration_audit as harness
from tools.prepare import PrepareTool, _explicit_preference_request
from tools.retouch import RetouchTool
from tools.retouch_engine import STYLE_PRESETS


class WrapperV2Tests(unittest.TestCase):
    def setUp(self):
        self.base = harness.IntegrationAudit()
        self.base.setUp()

    def tearDown(self):
        self.base.tearDown()

    def test_raw_jpeg_preserved_and_preview_oriented(self):
        image = Image.new('RGB', (80, 48), (90, 120, 150))
        image.putpixel((5, 5), (255, 0, 0))
        exif = Image.Exif()
        exif[274] = 6
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        output = BytesIO()
        image.save(output, format='JPEG', exif=exif, icc_profile=profile, quality=95)
        raw = output.getvalue()
        # The SDK filename/extension are deliberately misleading: inspect bytes.
        variables, blobs = self.base.prepare(images=[harness.file(raw, 'fake.png')])
        self.assertEqual(variables['status'], 'ready', variables)
        self.assertEqual(blobs['source_original.jpg'], raw)
        self.assertNotIn('source_original.png', blobs)
        self.assertEqual(variables['source_sha256'], sha256(raw).hexdigest())
        with Image.open(BytesIO(blobs['source_preview.jpg'])) as preview:
            self.assertEqual(preview.size, (48, 80))
            self.assertNotIn(274, preview.getexif())

    def test_mask_is_never_a_reference_preview(self):
        mask = harness.png((255, 255, 255))
        variables, blobs = self.base.prepare(harness.intent(asset_roles=['source', 'mask']),
            images=[harness.file(self.base.source), harness.file(mask, 'mask.png')])
        self.assertEqual(variables['status'], 'ready')
        self.assertEqual(variables['has_mask'], 1)
        self.assertEqual(variables['has_reference'], 0)
        self.assertIn('mask_original.png', blobs)
        self.assertNotIn('reference_preview.jpg', blobs)
        self.assertNotIn('mask_preview.jpg', blobs)

    def test_identity_mismatch_is_checked_before_reading_file(self):
        class NotReadable:
            @property
            def blob(self):
                raise AssertionError('Identity mismatch must precede file reads')
        variables, blobs = harness.unpack(self.base.tool(PrepareTool)._invoke({
            'user_id': 'wrong-user', 'conversation_id': self.base.conversation,
            'images': [NotReadable()], 'query': '自然一点',
            'request_json': json.dumps(harness.intent())}))
        self.assertEqual(variables['status'], 'error')
        self.assertIn('does not match the platform context', variables['summary'])
        self.assertEqual(blobs, {})

    def test_second_variant_failure_returns_no_first_variant_files(self):
        prepared, blobs = self.base.initial()
        bad = {'id': 'B', 'label': '不支持操作', 'recipe': {'version': 2, 'operations': [{'op': 'raw_python', 'code': 'pass'}]}}
        variables, outputs = self.base.render(prepared,
            {'mode': 'preview', 'explanation': '原子性测试', 'variants': [self.base.a, bad]}, blobs)
        self.assertEqual(variables['status'], 'error')
        self.assertEqual(outputs, {})

    def test_original_fingerprint_mismatch_rejected(self):
        prepared, blobs = self.base.initial()
        variables, outputs = self.base.render(prepared,
            {'mode': 'final', 'explanation': '', 'variants': [self.base.a]}, blobs,
            source_sha256='0' * 64)
        self.assertEqual(variables['status'], 'error')
        self.assertEqual(outputs, {})
        self.assertIn('fingerprint', variables['summary'])

    def test_help_lists_all_eight_style_names(self):
        variables, blobs = self.base.prepare(harness.intent(action='help'), query='看看8种风格卡')
        self.assertEqual(variables['status'], 'control')
        self.assertEqual(blobs, {})
        self.assertIn('Eight', variables['summary'])
        for number in range(1, 9):
            self.assertIn(f'{number}. ', variables['summary'])

    def test_negative_edit_preference_vs_negative_storage_intent(self):
        for query in ['记住，以后不要过度磨皮', '以后别美白', '以后不用强滤镜', '记住我喜欢自然一点']:
            with self.subTest(query=query):
                self.assertTrue(_explicit_preference_request(query))
        for query in ['不要记住这次偏好', '别设成默认', '不用保存我的偏好', '不要把这次当作默认']:
            with self.subTest(query=query):
                self.assertFalse(_explicit_preference_request(query))

    def test_preview_never_delivers_full_resolution_png(self):
        self.base.source = harness.png((90, 110, 130), (1200, 800))
        prepared, blobs = self.base.initial()
        variables, outputs = self.base.render(prepared,
            {'mode': 'preview', 'explanation': '', 'variants': [self.base.a, self.base.b]}, blobs)
        self.assertEqual(variables['status'], 'success', variables)
        self.assertEqual(set(outputs), {'candidate_A.jpg', 'candidate_B.jpg', 'review_preview.jpg', 'recipe.json'})
        for name in ['candidate_A.jpg', 'candidate_B.jpg']:
            with Image.open(BytesIO(outputs[name])) as image:
                self.assertLessEqual(max(image.size), 1000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
