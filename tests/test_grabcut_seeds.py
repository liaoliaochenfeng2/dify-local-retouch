import importlib.util
import io
import os
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

root = Path(__file__).resolve().parents[1]
PLUGIN = Path(os.environ.get("LOCAL_RETOUCH_TEST_PLUGIN", root / "plugin"))
spec = importlib.util.spec_from_file_location("engine", PLUGIN / "tools/retouch_engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def png(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


class GrabCutSeedsTest(unittest.TestCase):
    def setUp(self):
        self.image = np.full((200, 200, 3), 245, np.uint8)
        self.image[35:200, 45:155] = [30, 45, 60]
        self.image[70:125, 70:125] = [245, 245, 245]
        self.source = png(self.image)
        self.foreground = {"kind": "grabcut", "rect": [.15, .1, .85, 1]}
        self.keep = [[[.40, .40], [.55, .40], [.55, .55], [.40, .55]]]
        self.remove = [[[.02, .02], [.1, .02], [.1, .08], [.02, .08]]]

    def execute(self, foreground, source=None):
        recipe = {"version": 1, "operations": [{"op": "background", "foreground": foreground, "source": "color", "color": "#FF00FF", "feather": 0}]}
        output, report, mask = engine.process_image(source or self.source, recipe)
        return np.asarray(Image.open(io.BytesIO(output))), report, np.asarray(Image.open(io.BytesIO(mask)))

    def test_white_detail_protected(self):
        plain, _, plain_mask = self.execute(self.foreground)
        protected, report, protected_mask = self.execute({**self.foreground, "keep": self.keep, "remove": self.remove})
        self.assertTrue(np.all(protected[85:105, 85:105] == self.image[85:105, 85:105]))
        self.assertTrue(np.all(protected_mask[85:105, 85:105] == 255))
        self.assertTrue(np.all(protected[5:10, 5:10] == [255, 0, 255]))
        self.assertTrue(np.all(protected_mask[5:10, 5:10] == 0))
        self.assertLess(float(plain_mask[85:105, 85:105].mean()), 255)
        self.assertTrue(any("foreground keep regions" in note for note in report["warnings"]))

    def test_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.execute({**self.foreground, "keep": self.keep, "remove": self.keep})

    def test_keep_outside_rect_rejected(self):
        with self.assertRaisesRegex(ValueError, "inside the foreground rect"):
            self.execute({**self.foreground, "keep": self.remove})

    def test_malformed_and_excess_seeds_rejected(self):
        for invalid in [None, True, {}, [self.keep[0]] * 7, [[[.4, .4], [.5, .5]]], [[[True, .4], [.5, .4], [.5, .5]]]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.execute({**self.foreground, "keep": invalid})

    def test_other_mask_types_reject_seeds(self):
        for kind in ["rect", "ellipse", "upload", "alpha"]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.execute({"kind": kind, "rect": [.2, .2, .8, .8], "keep": self.keep})

    def test_scaled_working_mask_still_respects_full_size_seeds(self):
        large = np.asarray(Image.fromarray(self.image).resize((1600, 1600), Image.Resampling.NEAREST))
        output, report, mask = self.execute({**self.foreground, "keep": self.keep, "remove": self.remove}, png(large))
        self.assertEqual(output.shape, large.shape)
        self.assertTrue(np.all(output[650:850, 650:850] == large[650:850, 650:850]))
        self.assertTrue(np.all(mask[650:850, 650:850] == 255))
        self.assertTrue(any("1200 pixels" in note for note in report["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
