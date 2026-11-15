"""Numeric and boundary tests for V2, using offline pixel fixtures."""
import importlib.util
import io
import os
from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = Path(os.environ.get("LOCAL_RETOUCH_TEST_PLUGIN", ROOT / "plugin"))
spec = importlib.util.spec_from_file_location("engine_v2", PLUGIN / "tools/retouch_engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def encode(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def run(array, operations, **kwargs):
    png, report, mask = engine.process_image(encode(array), {"version": 2, "operations": operations}, **kwargs)
    return np.asarray(Image.open(io.BytesIO(png))), report, mask


class V2Tests(unittest.TestCase):
    def setUp(self):
        y, x = np.mgrid[:96, :128]
        self.image = np.dstack((40 + x, 50 + y, 100 + x // 2)).astype(np.uint8)
        self.ramp = np.repeat(np.arange(256, dtype=np.uint8)[None, :, None], 32, axis=0)
        self.ramp = np.repeat(self.ramp, 3, axis=2)

    def test_exposure_uses_linear_light_and_keeps_shape(self):
        output, report, _ = run(np.full((32, 32, 3), 100, np.uint8), [{"op": "tone", "exposure": 1}])
        self.assertTrue(130 < int(output[0, 0, 0]) < 145)
        self.assertEqual(output.shape, (32, 32, 3))
        self.assertEqual(report["steps"][0]["exposure"], 1)

    def test_highlights_and_shadows_are_selective(self):
        high, _, _ = run(self.ramp, [{"op": "tone", "highlights": -.5}])
        shadow, _, _ = run(self.ramp, [{"op": "tone", "shadows": .5}])
        self.assertGreater(220 - int(high[0, 220, 0]), 40 - int(high[0, 40, 0]))
        self.assertGreater(int(shadow[0, 40, 0]) - 40, int(shadow[0, 220, 0]) - 220)

    def test_gamma_and_levels(self):
        gamma, _, _ = run(self.ramp, [{"op": "tone", "gamma": 1.2}])
        levels, _, _ = run(self.ramp, [{"op": "tone", "blackpoint": .1, "whitepoint": .9}])
        self.assertGreater(int(gamma[0, 128, 0]), 128)
        self.assertEqual(int(levels[0, 20, 0]), 0)
        self.assertEqual(int(levels[0, 240, 0]), 255)

    def test_curve_monotonic_channel_specific(self):
        output, _, _ = run(self.ramp, [{"op": "curves", "red": [[0, .04], [.5, .6], [1, 1]]}])
        self.assertTrue(np.all(np.diff(output[0, :, 0].astype(int)) >= 0))
        np.testing.assert_array_equal(output[:, :, 1:], self.ramp[:, :, 1:])
        self.assertGreater(int(output[0, 128, 0]), 128)

    def test_bad_curves_rejected(self):
        for points in [[[.1, 0], [1, 1]], [[0, 0], [.5, .8], [1, .7]], [[0, 0], [.5, .4], [.5, .5], [1, 1]], [[0, False], [1, 1]]]:
            with self.subTest(points=points), self.assertRaises(ValueError):
                run(self.image, [{"op": "curves", "rgb": points}])

    def test_hsl_changes_only_target_hue(self):
        blocks = np.zeros((40, 80, 3), np.uint8)
        blocks[:, :40] = [200, 40, 40]
        blocks[:, 40:] = [40, 40, 200]
        output, _, _ = run(blocks, [{"op": "hsl", "bands": [{"center": 0, "width": 50, "hue": 15, "saturation": -.2}]}])
        self.assertFalse(np.array_equal(output[:, :40], blocks[:, :40]))
        np.testing.assert_array_equal(output[:, 40:], blocks[:, 40:])

    def test_all_styles_execute_and_report_parameters(self):
        for name in engine.STYLE_PRESETS:
            with self.subTest(name=name):
                output, report, _ = run(self.image, [{"op": "style", "name": name}])
                self.assertEqual(output.shape, self.image.shape)
                self.assertEqual(report["steps"][0]["name"], name)
                self.assertIn("preset", report["steps"][0])

    def test_zero_strength_is_identity(self):
        for operation in [{"op": "style", "name": "retro_warm", "strength": 0}, {"op": "reference_match", "strength": 0}, {"op": "tone", "exposure": 1, "strength": 0}]:
            with self.subTest(op=operation["op"]):
                output, report, _ = run(self.image, [operation], reference_bytes=encode(self.image))
                np.testing.assert_array_equal(output, self.image)
                self.assertTrue(any("No pixels changed" in note for note in report["warnings"]))

    def test_exact_protection_mask_for_all_new_operations(self):
        mask = np.zeros(self.image.shape[:2], np.uint8)
        mask[20:65, 30:80] = 255
        operations = [
            {"op": "tone", "exposure": .3},
            {"op": "curves", "rgb": [[0, 0], [.5, .65], [1, 1]]},
            {"op": "hsl", "bands": [{"center": 250, "width": 120, "hue": 10, "lightness": .05}]},
            {"op": "style", "name": "warm_soft"},
            {"op": "reference_match", "strength": .4},
        ]
        reference = self.image.copy()
        reference[:, :, 0] = np.minimum(reference[:, :, 0].astype(int) + 40, 255)
        for operation in operations:
            with self.subTest(op=operation["op"]):
                output, _, _ = run(self.image, [{**operation, "protect_mask": {"kind": "upload"}}], mask_bytes=encode(mask), reference_bytes=encode(reference))
                np.testing.assert_array_equal(output[mask == 255], self.image[mask == 255])

    def test_apply_mask_keeps_outside_exact(self):
        mask = np.zeros(self.image.shape[:2], np.uint8)
        mask[20:65, 30:80] = 255
        output, _, _ = run(self.image, [{"op": "style", "name": "cool_editorial", "mask": {"kind": "upload"}}], mask_bytes=encode(mask))
        np.testing.assert_array_equal(output[mask == 0], self.image[mask == 0])

    def test_reference_match_moves_colour_without_geometry(self):
        reference = self.image.copy()
        reference[:, :, 0] = np.minimum(reference[:, :, 0].astype(int) + 45, 255)
        reference[:, :, 2] = np.maximum(reference[:, :, 2].astype(int) - 15, 0)
        output, report, _ = run(self.image, [{"op": "reference_match", "strength": .4, "protect_skin": False}], reference_bytes=encode(reference))
        self.assertEqual(output.shape, self.image.shape)
        self.assertGreater(float(output[:, :, 0].mean()), float(self.image[:, :, 0].mean()))
        original_lab = cv2.cvtColor(self.image.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
        output_lab = cv2.cvtColor(output.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
        self.assertLess(float(np.abs(original_lab[:, :, 0] - output_lab[:, :, 0]).mean()), 1.0)
        self.assertTrue(report["steps"][0]["preserve_luminance"])

    def test_reference_requires_reference_image(self):
        with self.assertRaisesRegex(ValueError, "reference image"):
            run(self.image, [{"op": "reference_match"}])

    def test_high_bit_depth_image_rejected_instead_of_clipped(self):
        high_bit = np.full((32, 32), 30000, np.uint16)
        with self.assertRaisesRegex(ValueError, "high bit depth"):
            engine.process_image(encode(high_bit), {"version": 2, "operations": [{"op": "tone"}]})

    def test_transparency_unchanged(self):
        alpha = np.zeros(self.image.shape[:2], np.uint8)
        alpha[10:80, 10:100] = 180
        rgba = np.dstack((self.image, alpha))
        output, _, _ = run(rgba, [{"op": "style", "name": "night_atmosphere"}, {"op": "tone", "shadows": .1}])
        np.testing.assert_array_equal(output[:, :, 3], alpha)

    def test_limits_versions_and_repeats(self):
        for operations in [[{"op": "style", "name": "warm_soft"}] * 2, [{"op": "reference_match"}] * 2, [{"op": "tone"}] * 11]:
            with self.subTest(operations=operations), self.assertRaises(ValueError):
                run(self.image, operations, reference_bytes=encode(self.image))
        with self.assertRaises(ValueError):
            engine.process_image(encode(self.image), {"version": 1, "operations": [{"op": "tone"}]})
        output, report, _ = run(self.image, [{"op": "tone"}] * 10)
        np.testing.assert_array_equal(output, self.image)
        self.assertEqual(report["operations_completed"], 10)

    def test_unknown_nonfinite_and_bool_parameters_rejected(self):
        cases = [{"op": "tone", "exposure": True}, {"op": "tone", "gamma": float("nan")}, {"op": "style", "name": "warm_soft", "protect_skin": 1}, {"op": "reference_match", "strength": .51}, {"op": "hsl", "bands": [{"center": 20, "width": 40, "code": "no"}]}]
        for operation in cases:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                run(self.image, [operation], reference_bytes=encode(self.image))


if __name__ == "__main__":
    unittest.main(verbosity=2)
