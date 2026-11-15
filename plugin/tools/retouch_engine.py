"""Offline, deterministic image processing for the Local Retouch Studio workflow.

The public entry point accepts image bytes and a deliberately small JSON recipe.
There is no network access, model loading, code execution, or filesystem access.
"""
from __future__ import annotations

import io
import math
import re
import warnings as pywarnings

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageEnhance, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 12_600_000
MAX_SIDE = 6000
MAX_OPERATIONS = 10

# These are small, explainable colour recipes, not learned artistic models.
STYLE_PRESETS = {
    "clean_natural": {"label": "Clean natural", "tone": {"highlights": -.08, "shadows": .05}, "contrast": 1.04, "saturation": 1.03, "warmth": 0.0},
    "warm_soft": {"label": "Warm soft", "tone": {"highlights": -.12, "shadows": .08, "gamma": 1.02}, "contrast": .97, "saturation": .98, "warmth": .035},
    "cool_editorial": {"label": "Cool editorial", "tone": {"highlights": -.05, "shadows": -.03}, "contrast": 1.08, "saturation": .90, "warmth": -.04},
    "film_matte": {"label": "Matte film", "tone": {}, "contrast": .96, "saturation": .90, "warmth": .015, "rgb": [[0, .04], [.25, .26], [.7, .72], [1, .97]]},
    "retro_warm": {"label": "Warm retro", "tone": {}, "contrast": 1.02, "saturation": .88, "warmth": .055, "rgb": [[0, .03], [.5, .51], [1, .96]], "blue": [[0, .015], [.5, .47], [1, .94]]},
    "night_atmosphere": {"label": "Night atmosphere", "tone": {"exposure": -.18, "highlights": -.16, "shadows": .06}, "contrast": 1.06, "saturation": .96, "warmth": -.04},
    "travel_fresh": {"label": "Fresh travel", "tone": {"highlights": -.09, "shadows": .07}, "contrast": 1.05, "saturation": 1.10, "warmth": .01},
    "portrait_neutral": {"label": "Neutral portrait", "tone": {"highlights": -.10, "shadows": .08}, "contrast": 1.01, "saturation": .98, "warmth": 0.0},
}


def _keys(obj, allowed, label):
    if not isinstance(obj, dict):
        raise ValueError(f"{label} must be a JSON object.")
    extra = set(obj) - set(allowed)
    if extra:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(sorted(str(x) for x in extra))}.")


def _number(value, low, high, label, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    v = float(value)
    if not math.isfinite(v) or not low <= v <= high or (integer and v != int(v)):
        raise ValueError(f"{label} must be between {low} and {high}" + (" and must be an integer." if integer else "."))
    return int(v) if integer else v


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be true or false.")
    return value


def _point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be [x,y].")
    return tuple(_number(x, 0, 1, label) for x in value)


def _rect(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must be [left,top,right,bottom].")
    x0, y0, x1, y1 = [_number(v, 0, 1, label) for v in value]
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"{label} right and bottom bounds must exceed the left and top bounds.")
    return x0, y0, x1, y1


def _load(data, label, notes, preserve_profile=True):
    if not isinstance(data, bytes) or not data:
        raise ValueError(f"{label} is empty or is not binary image data.")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"{label} exceeds 20 MiB. Reduce the file size and try again.")
    try:
        with pywarnings.catch_warnings():
            pywarnings.simplefilter("error", Image.DecompressionBombWarning)
            opened = Image.open(io.BytesIO(data))
            width, height = opened.size
            if width < 16 or height < 16 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
                raise ValueError(f"{label} must be at least 16 x 16 pixels, at most 6000 pixels on each side, and at most 12.6 million pixels in total. The tool does not silently resize the source.")
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError(f"{label} contains multiple frames. Upload a single still image.")
            if opened.mode in ("I", "F") or opened.mode.startswith("I;16"):
                raise ValueError(f"{label} has high bit depth or floating-point pixels. Export it as an 8-bit sRGB PNG or JPEG before uploading.")
            profile = opened.info.get("icc_profile")
            orientation = opened.getexif().get(274, 1)
            im = ImageOps.exif_transpose(opened)
            im.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(f"Cannot decode {label}. Upload a valid still PNG, JPEG, or WebP image.") from exc
    if orientation not in (None, 1):
        notes.append(f"{label} was rotated according to its EXIF orientation. Output dimensions follow the corrected orientation.")
    alpha = np.asarray(im.convert("RGBA").getchannel("A"), dtype=np.uint8).copy()
    if preserve_profile and profile:
        try:
            original = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            srgb = ImageCms.createProfile("sRGB")
            color_input = im if im.mode in ("RGB", "CMYK", "LAB") else im.convert("RGB")
            rgb = ImageCms.profileToProfile(color_input, original, srgb, outputMode="RGB")
            notes.append(f"{label} was converted to sRGB using its embedded ICC profile.")
        except Exception as exc:
            raise ValueError(f"Cannot convert the ICC profile in {label}. Export the image as sRGB first.") from exc
    else:
        rgb = im.convert("RGB")
    return np.asarray(rgb, dtype=np.uint8).copy(), alpha


def _png(rgb, alpha=None):
    im = Image.fromarray(rgb.astype(np.uint8), "RGB")
    if alpha is not None and np.any(alpha != 255):
        im.putalpha(Image.fromarray(alpha.astype(np.uint8), "L"))
    out = io.BytesIO()
    # No original EXIF, ICC bytes, comments, or other source metadata are copied.
    im.save(out, format="PNG", compress_level=6)
    return out.getvalue()


def _mask_png(mask):
    out = io.BytesIO()
    Image.fromarray(np.clip(np.rint(mask * 255), 0, 255).astype(np.uint8), "L").save(out, format="PNG")
    return out.getvalue()


def _mask_bbox(mask):
    ys, xs = np.nonzero(mask > 0)
    if len(xs) == 0:
        raise ValueError("The selection is empty. Specify the area to edit again.")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _inward_feather(mask, pixels):
    """Feather inward only: every originally zero pixel remains exactly zero."""
    if pixels <= 0:
        return mask.astype(np.float32)
    hard = (mask > 0).astype(np.uint8)
    # A black border makes the image edge an actual mask boundary too.
    dist = cv2.distanceTransform(np.pad(hard, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    return (mask * np.minimum(dist / pixels, 1.0)).astype(np.float32)


def _grabcut_seed_mask(polygons, size, name, keep_rect=None):
    """Rasterize bounded user/model-specified certain foreground/background."""
    if not isinstance(polygons, list) or len(polygons) > 6:
        raise ValueError(f"GrabCut {name} must be an array of 0 to 6 polygons.")
    height, width = size
    result = np.zeros((height, width), np.uint8)
    for polygon in polygons:
        if not isinstance(polygon, list) or not 3 <= len(polygon) <= 32:
            raise ValueError(f"Each GrabCut {name} polygon must contain 3 to 32 vertices.")
        points = [_point(p, f"GrabCut {name} vertex") for p in polygon]
        if keep_rect is not None:
            l, t, r, b = keep_rect
            if any(x < l or x > r or y < t or y > b for x, y in points):
                raise ValueError("GrabCut keep regions must be completely inside the foreground rect.")
        pts = np.array([[int(round(x * (width - 1))), int(round(y * (height - 1)))] for x, y in points], np.int32)
        if abs(cv2.contourArea(pts)) < 4:
            raise ValueError(f"The GrabCut {name} seed polygon is too small. Enlarge the marked region.")
        cv2.fillPoly(result, [pts], 1)
    return result


def _make_mask(spec, size, uploaded_mask, *, foreground=False, rgb=None, alpha=None, notes=None):
    _keys(spec, ["kind", "rect", "points", "keep", "remove"], "Selection")
    kind = spec.get("kind")
    height, width = size
    if kind == "upload":
        if set(spec) != {"kind"}:
            raise ValueError("An uploaded-mask selection accepts only the kind field.")
        if uploaded_mask is None:
            raise ValueError("This operation requires an uploaded mask. White selects pixels to edit; black preserves them.")
        mask = uploaded_mask.copy()
    elif kind in ("rect", "ellipse", "grabcut"):
        if kind == "grabcut":
            if not {"kind", "rect"}.issubset(spec) or not set(spec).issubset({"kind", "rect", "keep", "remove"}):
                raise ValueError("A GrabCut selection requires kind and rect; only keep/remove seed polygons may be added.")
        elif set(spec) != {"kind", "rect"}:
            raise ValueError("Rectangle and ellipse selections must contain exactly kind and rect.")
        rect = _rect(spec["rect"], "Selection bounds")
        x0, y0, x1, y1 = rect
        left, top = int(math.floor(x0 * width)), int(math.floor(y0 * height))
        right, bottom = int(math.ceil(x1 * width)), int(math.ceil(y1 * height))
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right - left < 2 or bottom - top < 2:
            raise ValueError("The selection is too small. Specify a larger region.")
        if kind == "rect":
            mask = np.zeros((height, width), np.float32)
            mask[top:bottom, left:right] = 1
        elif kind == "ellipse":
            mask = np.zeros((height, width), np.uint8)
            center = ((left + right - 1) // 2, (top + bottom - 1) // 2)
            axes = (max(1, (right - left - 1) // 2), max(1, (bottom - top - 1) // 2))
            cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
            mask = mask.astype(np.float32) / 255
        else:
            if not foreground or rgb is None:
                raise ValueError("GrabCut is supported only for foreground selections in background replacement.")
            # The box may touch some image edges (e.g. a portrait reaches the
            # bottom); only require enough certain background OUTSIDE it.
            if ((right - left) * (bottom - top)) / (width * height) > .99:
                raise ValueError("The automatic-cutout rectangle must leave at least 1% of the image as background outside it. Alternatively, upload an accurate foreground mask.")
            scale = min(1.0, 1200.0 / max(width, height))
            small = cv2.resize(rgb, (max(16, int(round(width * scale))), max(16, int(round(height * scale)))), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
            sh, sw = small.shape[:2]
            lx, ty = max(0, int(x0 * sw)), max(0, int(y0 * sh))
            rx, by = min(sw, int(math.ceil(x1 * sw))), min(sh, int(math.ceil(y1 * sh)))
            if rx - lx < 2 or by - ty < 2:
                raise ValueError("The automatic-cutout rectangle is too small at the working resolution.")
            labels = np.zeros((sh, sw), np.uint8)
            keep = spec.get("keep", [])
            remove = spec.get("remove", [])
            # Validate even empty or wrongly typed values; explicit certain
            # seeds must never silently disappear or contradict one another.
            keep_mask = _grabcut_seed_mask(keep, (height, width), "keep", rect)
            remove_mask = _grabcut_seed_mask(remove, (height, width), "remove")
            if np.any(keep_mask & remove_mask):
                raise ValueError("GrabCut keep and remove regions overlap. Correct the conflicting annotations.")
            seeded = bool(keep or remove)
            if seeded:
                labels[ty:by, lx:rx] = cv2.GC_PR_FGD
                keep_small = cv2.resize(keep_mask, (sw, sh), interpolation=cv2.INTER_NEAREST) if scale < 1 else keep_mask
                remove_small = cv2.resize(remove_mask, (sw, sh), interpolation=cv2.INTER_NEAREST) if scale < 1 else remove_mask
                if keep and not np.any(keep_small):
                    raise ValueError("The GrabCut keep region is too small at the working resolution. Enlarge it.")
                if remove and not np.any(remove_small):
                    raise ValueError("The GrabCut remove region is too small at the working resolution. Enlarge it.")
                if np.any(keep_small & remove_small):
                    raise ValueError("GrabCut seeds overlap at the working resolution. Increase the separation between keep and remove regions.")
                labels[keep_small > 0] = cv2.GC_FGD
                labels[remove_small > 0] = cv2.GC_BGD
                if not np.any(np.isin(labels, [cv2.GC_FGD, cv2.GC_PR_FGD])) or not np.any(labels == cv2.GC_BGD):
                    raise ValueError("GrabCut annotations must leave both candidate foreground and definite background. Check the rectangle and seed regions.")
            try:
                cv2.setRNGSeed(0)
                cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), labels, (lx, ty, rx - lx, by - ty), np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 5, cv2.GC_INIT_WITH_MASK if seeded else cv2.GC_INIT_WITH_RECT)
            except cv2.error as exc:
                raise ValueError("Automatic cutout failed. Use a tighter foreground rectangle or upload a foreground mask.") from exc
            mask = np.isin(labels, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.float32)
            if scale < 1:
                mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_LINEAR)
                notes.append("Automatic cutout was computed at a maximum side length of 1200 pixels, then the mask was resized to the source dimensions. Check hair and fine edges.")
            if seeded:
                mask[keep_mask > 0] = 1
                mask[remove_mask > 0] = 0
                notes.append(f"Used {len(keep)} foreground keep regions and {len(remove)} background remove regions to guide cutout. Review the annotations for accuracy.")
            notes.append("Background replacement uses traditional GrabCut rather than a portrait-segmentation model. Check mask edges and missing areas.")
    elif kind == "polygon":
        if set(spec) != {"kind", "points"}:
            raise ValueError("A polygon selection must contain exactly kind and points.")
        points = spec["points"]
        if not isinstance(points, list) or not 3 <= len(points) <= 32:
            raise ValueError("A polygon must contain 3 to 32 vertices.")
        normalized = [_point(p, "Polygon vertex") for p in points]
        pts = np.array([[min(width - 1, int(round(x * (width - 1)))), min(height - 1, int(round(y * (height - 1))))] for x, y in normalized], np.int32)
        if abs(cv2.contourArea(pts)) < 4:
            raise ValueError("The polygon selection is too small.")
        mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(mask, [pts], 255)
        mask = mask.astype(np.float32) / 255
    elif kind == "alpha":
        if set(spec) != {"kind"} or not foreground or alpha is None or not np.any(alpha < 255):
            raise ValueError("An alpha foreground selection requires a source image with existing transparency and a background-replacement operation.")
        mask = alpha.astype(np.float32) / 255
    else:
        raise ValueError("Unsupported selection type. Use upload, rect, ellipse, or polygon; background replacement also supports grabcut and alpha.")
    ratio = float(np.count_nonzero(mask > 0) / (height * width))
    if ratio < 4 / (height * width):
        raise ValueError("The selection is empty or too small. Specify it again.")
    if foreground and (ratio < 0.001 or float(mask.mean()) > 0.995):
        raise ValueError("The foreground mask is nearly empty or covers the entire image. Check the white foreground selection before replacing the background.")
    return mask


def _warm(rgb, amount):
    if amount == 0:
        return rgb
    # Mild channel gain in sRGB; not presented as physical colour temperature.
    gains = np.array([1 + amount, 1.0, 1 - amount], np.float32)
    return np.clip(np.rint(rgb.astype(np.float32) * gains), 0, 255).astype(np.uint8)


def _blend_roi(base, changed, mask, box=None):
    x0, y0, x1, y1 = box or _mask_bbox(mask)
    weight = mask[y0:y1, x0:x1, None].astype(np.float32)
    region = base[y0:y1, x0:x1]
    if changed.shape != region.shape:
        raise ValueError("Internal region-processing dimensions do not match.")
    base[y0:y1, x0:x1] = np.clip(np.rint(region.astype(np.float32) * (1 - weight) + changed.astype(np.float32) * weight), 0, 255).astype(np.uint8)
    return base


def _tone_values(operation):
    return {
        "exposure": _number(operation.get("exposure", 0), -1, 1, "Exposure EV"),
        "highlights": _number(operation.get("highlights", 0), -.5, .5, "Highlight adjustment"),
        "shadows": _number(operation.get("shadows", 0), -.5, .5, "Shadow adjustment"),
        "blackpoint": _number(operation.get("blackpoint", 0), 0, .15, "Input black point"),
        "whitepoint": _number(operation.get("whitepoint", 1), .75, 1, "Input white point"),
        "gamma": _number(operation.get("gamma", 1), .7, 1.4, "Gamma"),
    }


def _tone_chunk(rgb, values):
    if values == {"exposure": 0, "highlights": 0, "shadows": 0, "blackpoint": 0, "whitepoint": 1, "gamma": 1}:
        return rgb.copy()
    arr = rgb.astype(np.float32) / 255
    exposure = values["exposure"]
    if exposure:
        linear = np.where(arr <= .04045, arr / 12.92, ((arr + .055) / 1.055) ** 2.4)
        linear = np.clip(linear * (2.0 ** exposure), 0, 1)
        arr = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)
    if values["shadows"] or values["highlights"]:
        luminance = np.sum(arr * np.array([.2126, .7152, .0722], np.float32), axis=2)
        delta = .25 * (values["shadows"] * (1 - luminance) ** 2 + values["highlights"] * luminance ** 2)
        arr = arr + delta[:, :, None]
    arr = np.clip((arr - values["blackpoint"]) / (values["whitepoint"] - values["blackpoint"]), 0, 1)
    if values["gamma"] != 1:
        arr = arr ** (1 / values["gamma"])
    return np.clip(np.rint(arr * 255), 0, 255).astype(np.uint8)


def _validate_curve(points, name):
    if not isinstance(points, list) or not 2 <= len(points) <= 8:
        raise ValueError(f"The {name} curve must contain 2 to 8 [x,y] control points.")
    pairs = [_point(p, f"{name} curve control point") for p in points]
    if pairs[0][0] != 0 or pairs[-1][0] != 1:
        raise ValueError(f"The first x value of the {name} curve must be 0 and the last must be 1.")
    if any(pairs[i][0] >= pairs[i + 1][0] or pairs[i][1] > pairs[i + 1][1] for i in range(len(pairs) - 1)):
        raise ValueError(f"The {name} curve must have strictly increasing x values and nondecreasing y values to avoid inverted tones.")
    return [[x, y] for x, y in pairs]


def _curve_lut(curves):
    x = np.linspace(0, 1, 256, dtype=np.float32)
    base = x
    if "rgb" in curves:
        pts = np.array(curves["rgb"], np.float32)
        base = np.interp(base, pts[:, 0], pts[:, 1])
    channels = []
    for channel in ("red", "green", "blue"):
        mapped = base
        if channel in curves:
            pts = np.array(curves[channel], np.float32)
            mapped = np.interp(base, pts[:, 0], pts[:, 1])
        channels.append(np.clip(np.rint(mapped * 255), 0, 255).astype(np.uint8))
    return np.stack(channels, axis=1).reshape(256, 1, 3)


def _hsl_chunk(rgb, bands):
    if all(b["hue"] == 0 and b["saturation"] == 0 and b["lightness"] == 0 for b in bands):
        return rgb.copy()
    hls = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2HLS)
    hue, lightness, saturation = [hls[:, :, k].copy() for k in range(3)]
    total = np.zeros(hue.shape, np.float32)
    hue_shift = total.copy()
    sat_shift = total.copy()
    light_shift = total.copy()
    for band in bands:
        distance = np.abs(hue - (band["center"] % 360))
        distance = np.minimum(distance, 360 - distance)
        weight = np.clip(1 - distance / (band["width"] / 2), 0, 1)
        weight = weight * weight * (3 - 2 * weight)
        # Neutral grays have no stable hue and must not be selected by chance.
        weight *= np.clip(saturation / .08, 0, 1)
        total += weight
        hue_shift += weight * band["hue"]
        sat_shift += weight * band["saturation"]
        light_shift += weight * band["lightness"]
    denominator = np.maximum(total, 1)
    hls[:, :, 0] = (hue + hue_shift / denominator) % 360
    hls[:, :, 1] = np.clip(lightness + light_shift / denominator, 0, 1)
    hls[:, :, 2] = np.clip(saturation * (1 + sat_shift / denominator), 0, 1)
    changed = cv2.cvtColor(hls, cv2.COLOR_HLS2RGB)
    return np.clip(np.rint(changed * 255), 0, 255).astype(np.uint8)


def _skin_weight(rgb):
    """Conservative colour heuristic only; this is NOT a skin/person model."""
    hls = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2HLS)
    hue, lightness, saturation = hls[:, :, 0], hls[:, :, 1], hls[:, :, 2]
    distance = np.abs(hue - 24)
    distance = np.minimum(distance, 360 - distance)
    hue_weight = np.clip(1 - distance / 42, 0, 1)
    sat_weight = np.minimum(np.clip((saturation - .04) / .12, 0, 1), np.clip((.82 - saturation) / .2, 0, 1))
    light_weight = np.minimum(np.clip((lightness - .08) / .18, 0, 1), np.clip((.98 - lightness) / .12, 0, 1))
    return (hue_weight * sat_weight * light_weight).astype(np.float32)


def _new_effect_mask(operation, rgb, alpha, uploaded_mask, strength, protect_skin, notes):
    height, width = rgb.shape[:2]
    if "mask" in operation:
        apply_mask = _make_mask(operation["mask"], (height, width), uploaded_mask)
        feather = _number(operation.get("feather", 8), 0, 30, "Inward feather pixels")
        weight = _inward_feather(apply_mask, feather)
    else:
        _number(operation.get("feather", 8), 0, 30, "Inward feather pixels")
        weight = np.ones((height, width), np.float32)
    weight *= (alpha > 0).astype(np.float32)
    if "protect_mask" in operation:
        protected = _make_mask(operation["protect_mask"], (height, width), uploaded_mask)
        weight *= 1 - protected
    if protect_skin:
        for row in range(0, height, 256):
            weight[row:row + 256] *= 1 - .7 * _skin_weight(rgb[row:row + 256])
        notes.append("Skin protection is a color heuristic that reduces adjustment strength by at most 70%. It may miss skin or misclassify background. Use protect_mask for precise protection.")
    weight *= strength
    if not np.any(weight > 0):
        notes.append("No pixels changed because the step has zero strength or its entire region is protected.")
    return weight


def _style_chunk(rgb, preset):
    result = _tone_chunk(rgb, _tone_values(preset.get("tone", {})))
    curves = {k: preset[k] for k in ("rgb", "red", "green", "blue") if k in preset}
    if curves:
        result = cv2.LUT(result, _curve_lut(curves))
    arr = result.astype(np.float32) / 255
    arr = (arr - .5) * preset["contrast"] + .5
    gray = np.sum(arr * np.array([.2126, .7152, .0722], np.float32), axis=2)
    arr = gray[:, :, None] + preset["saturation"] * (arr - gray[:, :, None])
    arr *= np.array([1 + preset["warmth"], 1, 1 - preset["warmth"]], np.float32)
    return np.clip(np.rint(arr * 255), 0, 255).astype(np.uint8)


def _lab_statistics(rgb, valid):
    stride = max(1, int(math.ceil(max(rgb.shape[:2]) / 640)))
    sampled_rgb = rgb[::stride, ::stride]
    sampled_valid = valid[::stride, ::stride]
    samples = cv2.cvtColor(sampled_rgb.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[sampled_valid > 0]
    if len(samples) < 16:
        raise ValueError("The reference-color sample region is too small. At least 16 valid pixels are required.")
    return samples.mean(axis=0), samples.std(axis=0)


def _apply_v2_operation(rgb, alpha, operation, uploaded_mask, reference_bytes, notes):
    op = operation["op"]
    common = ["op", "strength", "mask", "protect_mask", "feather", "protect_skin"]
    fields = {
        "tone": ["exposure", "highlights", "shadows", "blackpoint", "whitepoint", "gamma"],
        "curves": ["rgb", "red", "green", "blue"],
        "hsl": ["bands"],
        "style": ["name"],
        "reference_match": ["preserve_luminance", "match_contrast"],
    }
    _keys(operation, common + fields[op], "V2 color operation")
    default_strength = .6 if op == "style" else .2 if op == "reference_match" else 1
    strength = _number(operation.get("strength", default_strength), 0, .5 if op == "reference_match" else 1, "Color blending strength")
    protect_skin = _boolean(operation.get("protect_skin", op in ("style", "reference_match")), "Heuristic skin protection")
    weight = _new_effect_mask(operation, rgb, alpha, uploaded_mask, strength, protect_skin, notes)
    details = {"strength": strength, "protect_skin": protect_skin, "has_apply_mask": "mask" in operation, "has_protect_mask": "protect_mask" in operation, "effective_area_ratio": round(float(np.count_nonzero(weight > 0) / weight.size), 6)}
    if op == "tone":
        values = _tone_values(operation)
        transform = lambda block: _tone_chunk(block, values)
        details.update(values)
        details["description"] = "Adjusted exposure EV, highlights, shadows, black/white points, or gamma. Missing highlight or shadow detail was not generated."
    elif op == "curves":
        curves = {name: _validate_curve(operation[name], name) for name in ("rgb", "red", "green", "blue") if name in operation}
        if not curves:
            raise ValueError("A curves operation requires at least one rgb, red, green, or blue curve.")
        lut = _curve_lut(curves)
        transform = lambda block: cv2.LUT(block, lut)
        details.update(curves=curves, interpolation="monotonic_piecewise_linear", description="Mapped tones using monotonic piecewise-linear RGB/channel curves.")
    elif op == "hsl":
        bands = operation.get("bands")
        if not isinstance(bands, list) or not 1 <= len(bands) <= 6:
            raise ValueError("HSL must contain 1 to 6 hue bands.")
        parsed = []
        for band in bands:
            _keys(band, ["center", "width", "hue", "saturation", "lightness"], "HSL hue band")
            parsed.append({
                "center": _number(band.get("center"), 0, 360, "Hue center"),
                "width": _number(band.get("width"), 5, 120, "Hue band width"),
                "hue": _number(band.get("hue", 0), -20, 20, "Hue shift in degrees"),
                "saturation": _number(band.get("saturation", 0), -.4, .4, "Saturation ratio change"),
                "lightness": _number(band.get("lightness", 0), -.2, .2, "Lightness shift"),
            })
        transform = lambda block: _hsl_chunk(block, parsed)
        details.update(bands=parsed, description="Selectively adjusted HSL within the specified hue bands. This does not semantically identify skin or background.")
    elif op == "style":
        name = operation.get("name")
        if not isinstance(name, str) or name not in STYLE_PRESETS:
            raise ValueError("Unsupported style name. Choose one of the 8 built-in base styles.")
        preset = STYLE_PRESETS[name]
        transform = lambda block: _style_chunk(block, preset)
        details.update(name=name, preset=preset, description=f"Blended the {preset['label']} base color recipe. Backgrounds, people, and light sources were not generated or replaced.")
    else:
        if reference_bytes is None:
            raise ValueError("Reference color matching requires an uploaded reference image.")
        preserve_luminance = _boolean(operation.get("preserve_luminance", True), "Preserve luminance")
        match_contrast = _boolean(operation.get("match_contrast", False), "Match color-distribution spread")
        reference, reference_alpha = _load(reference_bytes, "Reference image", notes)
        # A zero-strength or fully protected recipe is a valid no-op. Statistics
        # then describe the source only; no transformed pixels will be blended.
        sample_valid = weight > 0
        if not np.any(sample_valid):
            sample_valid = alpha > 0
            if not np.any(sample_valid):
                sample_valid = np.ones(alpha.shape, np.uint8)
        src_mean, src_std = _lab_statistics(rgb, sample_valid)
        ref_mean, ref_std = _lab_statistics(reference, reference_alpha > 0)
        scale = np.clip(ref_std / np.maximum(src_std, 1), .7, 1.3) if match_contrast else np.ones(3, np.float32)
        if preserve_luminance:
            scale[0] = 1
        def transform(block):
            lab = cv2.cvtColor(block.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
            shifted = (lab - src_mean) * scale + ref_mean
            if preserve_luminance:
                shifted[:, :, 0] = lab[:, :, 0]
            shifted[:, :, 0] = np.clip(shifted[:, :, 0], 0, 100)
            shifted[:, :, 1:] = np.clip(shifted[:, :, 1:], -110, 110)
            return np.clip(np.rint(cv2.cvtColor(shifted, cv2.COLOR_LAB2RGB) * 255), 0, 255).astype(np.uint8)
        details.update(preserve_luminance=preserve_luminance, match_contrast=match_contrast, source_lab_mean=[round(float(x), 3) for x in src_mean], reference_lab_mean=[round(float(x), 3) for x in ref_mean], channel_scale=[round(float(x), 3) for x in scale], description="Applied low-strength Lab color-statistics matching. Only colors changed; reference content and subject structure were not copied or moved.")
        notes.append("Reference matching uses valid-pixel statistics from the whole reference image without understanding its subjects or lighting. Reduce strength when scenes differ substantially.")
    result = rgb.copy()
    for row in range(0, rgb.shape[0], 256):
        local_weight = weight[row:row + 256, :, None]
        if not np.any(local_weight):
            continue
        changed = transform(rgb[row:row + 256])
        result[row:row + 256] = np.clip(np.rint(rgb[row:row + 256].astype(np.float32) * (1 - local_weight) + changed.astype(np.float32) * local_weight), 0, 255).astype(np.uint8)
    return result, details, weight


def process_image(source_bytes: bytes, recipe: dict, mask_bytes: bytes | None = None, background_bytes: bytes | None = None, reference_bytes: bytes | None = None):
    """Return (PNG bytes, JSON-serializable report, last mask PNG or None).

    Errors raise ValueError and return no partly edited image. All operation and
    mask parameters are bounded. Uploaded mask white means the selected region;
    in a background operation it instead means the foreground to retain.
    """
    _keys(recipe, ["version", "operations"], "Retouch recipe")
    if type(recipe.get("version")) is not int or recipe["version"] not in (1, 2):
        raise ValueError("Recipe version must be the integer 1 or 2.")
    version = recipe["version"]
    operations = recipe.get("operations")
    max_operations = 6 if version == 1 else MAX_OPERATIONS
    if not isinstance(operations, list) or not 1 <= len(operations) <= max_operations:
        raise ValueError(f"This recipe version requires 1 to {max_operations} operations.")
    notes = []
    rgb, alpha = _load(source_bytes, "Source image", notes)
    height, width = rgb.shape[:2]
    original_alpha = alpha.copy()
    uploaded_mask = None
    if mask_bytes is not None:
        mask_rgb, mask_alpha = _load(mask_bytes, "Mask", notes, preserve_profile=False)
        if mask_rgb.shape[:2] != (height, width):
            raise ValueError("Uploaded mask dimensions must exactly match the EXIF-corrected source. Masks are not automatically stretched.")
        uploaded_mask = cv2.cvtColor(mask_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
        uploaded_mask *= mask_alpha.astype(np.float32) / 255
    background = None
    steps = []
    last_mask = None
    mask_role = None
    erase_union = np.zeros((height, width), np.uint8)
    background_seen = False
    style_seen = False
    reference_seen = False
    for index, operation in enumerate(operations, 1):
        if not isinstance(operation, dict):
            raise ValueError(f"Step {index} must be a JSON object.")
        op = operation.get("op")
        step = {"index": index, "op": op}
        if op in ("tone", "curves", "hsl", "style", "reference_match"):
            if version != 2:
                raise ValueError("tone, curves, hsl, style, and reference_match require a version=2 recipe.")
            if op == "style":
                if style_seen:
                    raise ValueError("A recipe may apply at most one style preset.")
                style_seen = True
            if op == "reference_match":
                if reference_seen:
                    raise ValueError("A recipe may apply reference_match at most once.")
                reference_seen = True
            rgb, details, last_mask = _apply_v2_operation(rgb, alpha, operation, uploaded_mask, reference_bytes, notes)
            step.update(details)
            mask_role = {"operation_index": index, "meaning": "Brighter white indicates greater effective color-blending strength in this step; black pixels retain their original values."}
        elif op == "adjust":
            _keys(operation, ["op", "brightness", "contrast", "saturation", "sharpness", "warmth"], "Global adjustment")
            brightness = _number(operation.get("brightness", 1), .75, 1.35, "Brightness")
            contrast = _number(operation.get("contrast", 1), .8, 1.3, "Contrast")
            saturation = _number(operation.get("saturation", 1), .65, 1.4, "Saturation")
            sharpness = _number(operation.get("sharpness", 1), 0, 1.8, "Sharpness")
            warmth = _number(operation.get("warmth", 0), -.15, .15, "Warmth shift")
            im = Image.fromarray(rgb)
            for name, value in [("Brightness", brightness), ("Contrast", contrast), ("Color", saturation), ("Sharpness", sharpness)]:
                if value != 1:
                    im = getattr(ImageEnhance, name)(im).enhance(value)
            rgb = _warm(np.asarray(im, dtype=np.uint8), warmth).copy()
            step.update(brightness=brightness, contrast=contrast, saturation=saturation, sharpness=sharpness, warmth=warmth, description="Adjusted global brightness, contrast, saturation, sharpness, or warmth.")
        elif op in ("local_adjust", "smooth", "inpaint"):
            allowed = {"local_adjust": ["op", "mask", "brightness", "warmth", "feather"], "smooth": ["op", "mask", "strength", "feather"], "inpaint": ["op", "mask", "radius"]}[op]
            _keys(operation, allowed, "Local processing")
            mask = _make_mask(operation.get("mask"), (height, width), uploaded_mask)
            # Fully transparent source pixels cannot become visible in local edits.
            mask *= (alpha > 0).astype(np.float32)
            ratio = float(np.count_nonzero(mask > 0) / (height * width))
            if op == "inpaint":
                radius = _number(operation.get("radius", 3), 1, 8, "Inpaint radius", integer=True)
                hard = (mask >= .5).astype(np.uint8)
                if not np.any(hard):
                    raise ValueError("The inpaint mask has no sufficiently bright selection. Mark the target in white.")
                area = float(hard.mean())
                if area > .08:
                    raise ValueError("A single inpaint selection exceeds 8% of the image. This version supports small texture repairs only; reduce the selection.")
                erase_union |= hard
                if float(erase_union.mean()) > .12:
                    raise ValueError("Cumulative inpaint selections exceed 12% of the image. Processing stopped to avoid large blurred repairs.")
                x0, y0, x1, y1 = _mask_bbox(hard)
                pad = max(24, radius * 5)
                x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad)
                local_hard = hard[y0:y1, x0:x1]
                repaired = cv2.inpaint(rgb[y0:y1, x0:x1], local_hard * 255, radius, cv2.INPAINT_TELEA)
                # Inpaint already preserves all unmasked pixels; enforce it here too.
                rgb[y0:y1, x0:x1][local_hard > 0] = repaired[local_hard > 0]
                last_mask = hard.astype(np.float32)
                step.update(radius=radius, mask_area_ratio=round(area, 6), description="Repaired the selection using traditional neighboring-texture inpainting, not generative scene filling.")
                notes.append("Small-area inpainting may leave blur, repeated textures, or missing structure. Review the result; complex occluded content cannot be reliably restored.")
            else:
                feather = _number(operation.get("feather", 8), 0, 30 if op == "local_adjust" else 24, "Inward feather pixels")
                effective = _inward_feather(mask, feather)
                x0, y0, x1, y1 = _mask_bbox(effective)
                if op == "local_adjust":
                    brightness = _number(operation.get("brightness", 1), .7, 1.4, "Local brightness")
                    warmth = _number(operation.get("warmth", 0), -.15, .15, "Local warmth shift")
                    local = rgb[y0:y1, x0:x1]
                    changed = np.clip(np.rint(local.astype(np.float32) * brightness), 0, 255).astype(np.uint8)
                    changed = _warm(changed, warmth)
                    rgb = _blend_roi(rgb, changed, effective, (x0, y0, x1, y1))
                    step.update(brightness=brightness, warmth=warmth, feather=feather, description="Adjusted brightness or warmth only inside the selection. Feathering did not extend beyond it.")
                else:
                    strength = _number(operation.get("strength", .25), .05, .5, "Smoothing strength")
                    if ratio > .5:
                        raise ValueError("The smoothing selection exceeds 50% of the image. Select the intended skin area to avoid losing detail across the image.")
                    # Filter a padded ROI, then blend only inside the requested mask.
                    px0, py0, px1, py1 = max(0, x0 - 6), max(0, y0 - 6), min(width, x1 + 6), min(height, y1 + 6)
                    filtered = cv2.bilateralFilter(rgb[py0:py1, px0:px1], 9, 28, 7)
                    changed = filtered[y0 - py0:y1 - py0, x0 - px0:x1 - px0]
                    rgb = _blend_roi(rgb, changed, effective * strength, (x0, y0, x1, y1))
                    step.update(strength=strength, feather=feather, description="Applied light bilateral smoothing to the selected skin region without redrawing facial features. Review fine details.")
                    notes.append("Smoothing does not automatically identify eyes, lips, or hair. These may also be smoothed if included in the selection.")
                last_mask = effective
                step["mask_area_ratio"] = round(ratio, 6)
            mask_role = {"operation_index": index, "meaning": "White shows the region modified in this step; gray indicates partial blending."}
        elif op == "background":
            _keys(operation, ["op", "foreground", "source", "color", "feather"], "Background replacement")
            if background_seen:
                raise ValueError("A recipe may replace the background at most once.")
            background_seen = True
            mode = operation.get("source", "color")
            if mode not in ("color", "upload"):
                raise ValueError("Background source must be color or upload.")
            color = operation.get("color", "#FFFFFF")
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Background color must use #RRGGBB format.")
            feather = _number(operation.get("feather", 2), 0, 8, "Foreground inward feather pixels")
            fg = _make_mask(operation.get("foreground"), (height, width), uploaded_mask, foreground=True, rgb=rgb, alpha=alpha, notes=notes)
            # Existing transparency always remains transparent to the replacement bg.
            if operation["foreground"].get("kind") != "alpha":
                fg *= alpha.astype(np.float32) / 255
            fg = _inward_feather(fg, feather)
            if mode == "upload":
                if background_bytes is None:
                    raise ValueError("This operation requires an uploaded replacement background image.")
                if background is None:
                    background, bg_alpha = _load(background_bytes, "Background image", notes)
                    if np.any(bg_alpha != 255):
                        white = np.full_like(background, 255)
                        weight = bg_alpha.astype(np.float32)[:, :, None] / 255
                        background = np.clip(np.rint(background * weight + white * (1 - weight)), 0, 255).astype(np.uint8)
                        notes.append("Transparent areas in the uploaded background were flattened onto white.")
                fitted = ImageOps.fit(Image.fromarray(background), (width, height), method=Image.Resampling.LANCZOS, centering=(.5, .5))
                bg = np.asarray(fitted, dtype=np.uint8).copy()
                notes.append("The uploaded background was scaled and center-cropped to cover the source dimensions. No new background content was generated.")
            else:
                channels = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
                bg = np.empty_like(rgb)
                bg[:] = channels
            # Row-wise blend keeps peak memory bounded on 12MP images.
            for row in range(0, height, 256):
                weight = fg[row:row + 256, :, None]
                rgb[row:row + 256] = np.clip(np.rint(rgb[row:row + 256] * weight + bg[row:row + 256] * (1 - weight)), 0, 255).astype(np.uint8)
            alpha = np.full((height, width), 255, np.uint8)
            last_mask = fg
            mask_role = {"operation_index": index, "meaning": "White shows retained source foreground, black shows replacement background, and gray shows edge blending."}
            step.update(source=mode, color=color if mode == "color" else None, feather=feather, foreground_area_ratio=round(float(fg.mean()), 6), description="Composited the replacement background. Inspect the mask and result for subject edges, color mismatch, and source-background remnants.")
        elif op == "liquify":
            _keys(operation, ["op", "center", "radius", "displacement"], "Light liquify")
            cx, cy = _point(operation.get("center"), "Liquify center")
            radius = _number(operation.get("radius"), .02, .18, "Liquify radius")
            displacement = operation.get("displacement")
            if not isinstance(displacement, (list, tuple)) or len(displacement) != 2:
                raise ValueError("Liquify displacement must be [dx,dy].")
            dx = _number(displacement[0], -.03, .03, "Horizontal liquify displacement") * width
            dy = _number(displacement[1], -.03, .03, "Vertical liquify displacement") * height
            r = radius * min(width, height)
            if math.hypot(dx, dy) > min(.2 * r, .025 * min(width, height)):
                raise ValueError("Liquify displacement is too large: it must not exceed 20% of the radius or 2.5% of the shorter image side.")
            center_x, center_y = cx * (width - 1), cy * (height - 1)
            if center_x - r < 0 or center_y - r < 0 or center_x + r >= width or center_y + r >= height:
                raise ValueError("The liquify circle must be completely inside the source image without touching its edges.")
            x0, y0 = max(0, int(math.floor(center_x - r - abs(dx) - 2))), max(0, int(math.floor(center_y - r - abs(dy) - 2)))
            x1, y1 = min(width, int(math.ceil(center_x + r + abs(dx) + 3))), min(height, int(math.ceil(center_y + r + abs(dy) + 3)))
            grid_y, grid_x = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            distance = np.sqrt((grid_x - center_x) ** 2 + (grid_y - center_y) ** 2)
            weight = np.maximum(1 - (distance / r) ** 2, 0) ** 2
            map_x, map_y = grid_x - dx * weight - x0, grid_y - dy * weight - y0
            original_region = rgb[y0:y1, x0:x1].copy()
            warped = cv2.remap(original_region, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
            active = distance < r
            rgb[y0:y1, x0:x1][active] = warped[active]
            if np.any(alpha != 255):
                warped_alpha = cv2.remap(alpha[y0:y1, x0:x1], map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
                alpha[y0:y1, x0:x1][active] = warped_alpha[active]
            last_mask = np.zeros((height, width), np.float32)
            last_mask[y0:y1, x0:x1] = weight
            mask_role = {"operation_index": index, "meaning": "Brighter white indicates greater local displacement weight. Pixels outside the region are unchanged."}
            step.update(center=[cx, cy], radius=radius, displacement=list(displacement), max_displacement_pixels=round(math.hypot(dx, dy), 2), description="Applied a small local displacement without identifying facial or body structure.")
            notes.append("Liquify moves all pixels inside the circle, including background. Check straight lines, facial features, and outlines for distortion.")
        else:
            raise ValueError(f"Unsupported operation in step {index}. Use adjust, local_adjust, smooth, inpaint, background, or liquify; V2 also supports tone, curves, hsl, style, and reference_match.")
        steps.append(step)
    summary_names = {"adjust": "Global color adjustment", "local_adjust": "Local brightness/warmth adjustment", "smooth": "Light smoothing", "inpaint": "Small-area inpainting", "background": "Background replacement", "liquify": "Light local liquify", "tone": "Tonal adjustment", "curves": "Curves adjustment", "hsl": "Selective color adjustment", "style": "Base style adjustment", "reference_match": "Reference color matching"}
    notes = list(dict.fromkeys(notes))
    report = {
        "version": version,
        "status": "success",
        "width": width,
        "height": height,
        "operations_completed": len(steps),
        "steps": steps,
        "warnings": notes,
        "mask_preview_role": mask_role,
        "source_had_transparency": bool(np.any(original_alpha != 255)),
        "output_has_transparency": bool(np.any(alpha != 255)),
        "summary": "Completed: " + ", ".join(summary_names[s["op"]] for s in steps) + f". Output dimensions: {width} x {height}, retaining the EXIF-corrected source dimensions. No external image service was called.",
    }
    return _png(rgb, alpha), report, _mask_png(last_mask) if last_mask is not None else None
