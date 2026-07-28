# -*- coding: utf-8 -*-
"""The HYBRID: the model's light and geometry, the brand's own lettering.

OWNER'S PRODUCTION RULE (2026-07-28), the way agencies actually do it:

    "بنخلي الـ AI يولد المشهد والعلبة عشان ناخُد منه الإضاءة، الظلال، والزاوية الـ 3D التحفة.
     في المونتاج بنجيب التصميم الأصلي عالي الجودة للعلبة ونعمله إسقاط (Perspective Warp) فوق
     العلبة اللي الـ AI رسمها. بكده بنكون أخدنا إضاءة وظل الـ AI + دقة الحروف الأصلية 100%."

Both halves of the old argument were right and each was fatal on its own:

  * LET THE MODEL DRAW IT — and the label comes back as "LABORATONE DERHATOLOGIQUE", or one
    Arabic letter wrong («جديد» for «حديد»), which for a registered pharmaceutical is a trademark
    distortion, not a taste defect.
  * PASTE THE REAL PHOTOGRAPH — and it never belongs. Measured on Nano Care over three runs:
    "the product boxes are floating illogically", then "a flat graphic floating illogically over
    the scene, not placed within it". A cut-out carries its own camera and its own light into a
    frame that has different ones.

The hybrid takes the model's rendering as the GEOMETRY AND LIGHTING SOURCE — it drew the box
standing in its own scene, lit by its own key light, in correct perspective — and replaces only
the artwork on the label face with the real file, projected onto the quadrilateral the model
drew and multiplied by that face's own shading. The lighting is the model's because it is
literally the model's pixels doing the shading; the letters are the brand's because they were
never generated.

HOW THE FACE IS FOUND matters as much as the warp. Asking a vision model for four corners is a
guess with no error bar — it either lands or it silently pastes artwork onto scenery, and the
answer looks identical either way. So the primary route is SIFT + RANSAC between the real
packshot and the drawn one, which returns an inlier count: evidence, not a claim. The vision
call stays as the fallback for packshots too plain to key on.

`reproject_label` (the quad route) additionally works with no OpenCV at all — its 8-coefficient
perspective solve is plain Python — so a stripped environment degrades instead of breaking.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# The vision model is asked for integers on a 0..1000 grid — the convention Gemini is most
# consistent at for spatial answers. Floats in 0..1 drift; pixel coordinates require it to know
# the resolution it was shown.
_GRID = 1000.0

# A face smaller than this fraction of the canvas is not worth reprojecting: the warp's own
# resampling error would exceed the legibility it buys.
MIN_FACE_AREA = 0.004


class LabelQuad(BaseModel):
    """The four corners of the product's FRONT label face, clockwise from its top-left as the
    face itself is oriented — not as the frame is. `found=False` is the third state."""

    found: bool = False
    # normalized 0..1 canvas coordinates
    corners: list = []
    reason: str = ""

    @property
    def area(self) -> float:
        """Shoelace area of the quad in normalized units."""
        c = self.corners
        if len(c) != 4:
            return 0.0
        s = 0.0
        for i in range(4):
            x1, y1 = c[i]
            x2, y2 = c[(i + 1) % 4]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0


class _QuadResponse(BaseModel):
    found: bool
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    x3: int = 0
    y3: int = 0
    x4: int = 0
    y4: int = 0
    reason: str = ""


def _system(product_name: str) -> str:
    name = (product_name or "the product").strip()
    return (
        "You are a compositing technical director locating a surface for a perspective warp.\n"
        f"In this image find the package of {name} and report the FRONT FACE of its label — the "
        "single flat face that carries the brand name and faces the camera most directly.\n"
        "Return its FOUR CORNERS as integer coordinates on a 0-1000 grid, where 0,0 is the "
        "TOP-LEFT of the image and 1000,1000 the BOTTOM-RIGHT.\n"
        "Order matters and is the FACE's own order, not the frame's: (x1,y1) is the corner at "
        "the top-left OF THE PRINTED LABEL, then go CLOCKWISE around the face — (x2,y2) its "
        "top-right, (x3,y3) its bottom-right, (x4,y4) its bottom-left. If the box is rotated, "
        "these will not be axis-aligned, and that is expected: give the true corners of the "
        "face as it appears in perspective.\n"
        "Report the face ONLY — not the whole box, not its side or top faces, and not any "
        "shadow beneath it.\n"
        "found = false if there is no such package in the image, if more than one competes and "
        "you cannot tell which is meant, or if the face is too obscured to corner accurately. "
        "Guessing is worse than saying no: a wrong quadrilateral pastes the artwork onto empty "
        "scenery."
    )


def find_label_quad(scene: "str | Path | bytes", *, caller: Any = None,
                    product_name: str = "") -> LabelQuad:
    """Locate the drawn label's front face. Never raises; `found=False` when unlocatable."""
    if caller is None:
        return LabelQuad(reason="no vision caller: the label face was not located")
    try:
        data = scene if isinstance(scene, bytes) else Path(str(scene)).read_bytes()
    except Exception:  # noqa: BLE001
        return LabelQuad(reason="scene image could not be read")

    try:
        resp, _u = caller(_system(product_name), "Corner the product's front label face.",
                          _QuadResponse, group_name="label_quad",
                          images=[(data, "image/png")])
    except Exception as exc:  # noqa: BLE001
        return LabelQuad(reason=f"quad call failed ({type(exc).__name__})")

    if not getattr(resp, "found", False):
        return LabelQuad(reason=(getattr(resp, "reason", "") or "no label face reported").strip())

    corners = [(float(getattr(resp, f"x{i}", 0)) / _GRID, float(getattr(resp, f"y{i}", 0)) / _GRID)
               for i in (1, 2, 3, 4)]
    quad = LabelQuad(found=True, corners=corners, reason=(getattr(resp, "reason", "") or "").strip())
    ok, why = validate_quad(quad)
    if not ok:
        logger.info("[label] reported quad rejected: %s", why)
        return LabelQuad(reason=why)
    return quad


def validate_quad(quad: LabelQuad) -> tuple:
    """(ok, reason). A quadrilateral that is off-canvas, inverted, slivered or tiny would warp
    the artwork onto scenery — worse than leaving the drawn label alone."""
    c = list(quad.corners or [])
    if len(c) != 4:
        return False, "not four corners"
    for x, y in c:
        if not (-0.05 <= x <= 1.05 and -0.05 <= y <= 1.05):
            return False, f"corner ({x:.2f}, {y:.2f}) is outside the canvas"
    # CONVEXITY IS CHECKED FIRST, and the order matters. A self-intersecting bow-tie has a
    # shoelace area near zero, so an area test in front of this one rejects it for the wrong
    # reason — and a bow-tie whose lobes happen to be uneven would pass the area test and reach
    # the warp, where it folds the artwork back on itself.
    # Every cross product of consecutive edges must share a sign.
    signs = []
    for i in range(4):
        ax, ay = c[i]
        bx, by = c[(i + 1) % 4]
        cx, cy = c[(i + 2) % 4]
        signs.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    if not (all(s > 0 for s in signs) or all(s < 0 for s in signs)):
        return False, "the corners are not a convex quadrilateral (wrong order or a fold)"
    if quad.area < MIN_FACE_AREA:
        return False, f"the face is {quad.area:.1%} of the canvas — too small to reproject"
    # Aspect sanity: a face thinner than 1:12 is an edge-on sliver, not a readable label.
    def _d(p, q):
        return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5
    top, right = _d(c[0], c[1]), _d(c[1], c[2])
    bottom, left = _d(c[2], c[3]), _d(c[3], c[0])
    w, h = (top + bottom) / 2.0, (right + left) / 2.0
    if min(w, h) <= 0 or max(w, h) / min(w, h) > 12.0:
        return False, "the face is edge-on (aspect beyond 12:1)"
    return True, "ok"


# --------------------------------------------------------------------------------------------
# LOCATING THE FACE, PROPERLY. Asking a vision model for four corner coordinates is a guess with
# no error bar: it either lands or it silently pastes the artwork onto scenery, and nothing in
# the answer tells you which happened. Matching the REAL packshot against the drawn one gives
# the same answer with a number attached — how many keypoints agreed — so the pipeline can
# refuse when the agreement is thin. This is the primary route; the vision call below is the
# fallback for when a packshot has too little texture to match.
# --------------------------------------------------------------------------------------------

# RANSAC agreement below this is not a located product, it is a coincidence. Calibrated as the
# floor where a homography stops being over-determined for a planar face.
MIN_INLIERS = 12
MIN_INLIER_RATIO = 0.28

# How far past the matched keypoints the face is assumed to extend. Keypoints stop where the
# artwork's detail stops, which is short of the packaging's plain border on every side.
_FOOTPRINT_GROWTH = 1.45

# The shading field: how far to blur before the map is LIGHT rather than INK, as a fraction of
# the face, and how far the resulting multiplier may swing from neutral.
_LIGHT_BLUR = 0.22
_LIGHT_SWING = 0.26


class LabelMatch(BaseModel):
    """A located face, with the evidence for it. `matched=False` is the third state."""

    matched: bool = False
    inliers: int = 0
    matches: int = 0
    corners: list = []          # normalized quad of the matched region, clockwise
    reason: str = ""

    @property
    def inlier_ratio(self) -> float:
        return (self.inliers / self.matches) if self.matches else 0.0


def _cv():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except Exception:  # noqa: BLE001
        return None, None


def _as_bgr(img, max_dim: int = 1400):
    """A Pillow image as an OpenCV BGR array, downscaled so SIFT stays fast and scale-stable."""
    cv2, np = _cv()
    rgb = img.convert("RGB")
    w, h = rgb.size
    if max(w, h) > max_dim:
        s = max_dim / float(max(w, h))
        rgb = rgb.resize((max(1, int(w * s)), max(1, int(h * s))))
    arr = np.asarray(rgb)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), (rgb.size[0] / float(w), rgb.size[1] / float(h))


def match_label(scene_img, art_img, *, roi: Optional[tuple] = None) -> tuple:
    """(LabelMatch, homography or None) — where the real packshot appears in the rendered scene.

    The model was given the packshot and told to render that object, so the box it drew is a
    REPAINTING of the real one: same silhouette, same colour blocks, same logo mark, same layout
    — with the fine print garbled. SIFT keys on exactly the structure that survived and ignores
    the text that did not, so a homography estimated from those correspondences maps the real
    artwork onto the drawn face at the model's own scale, position and perspective.

    The inlier count is the honest confidence: a wrong homography does not accumulate agreement.
    """
    cv2, np = _cv()
    if cv2 is None:
        return LabelMatch(reason="OpenCV is not available"), None
    try:
        # MATCH WHERE THE PRODUCT IS, AT THE RESOLUTION IT HAS. Measured live: on a scene whose
        # product occupies 20% of the frame height, downscaling the whole canvas to 1400px left
        # the box about 200px tall and SIFT found 34 candidate correspondences — 8 of them
        # inliers, below the floor, so a perfectly good render fell back to the paste. Cropping
        # to the region the placement decision reserved and matching THAT at full resolution is
        # the same picture with several times the detail.
        full_w, full_h = scene_img.size
        crop_off = (0, 0)
        if roi:
            rx, ry, rw, rh = roi
            m_ = 0.22                          # the model rarely lands exactly on the reservation
            x0 = max(0, int((rx - rw * m_) * full_w))
            y0 = max(0, int((ry - rh * m_) * full_h))
            x1 = min(full_w, int((rx + rw * (1 + m_)) * full_w))
            y1 = min(full_h, int((ry + rh * (1 + m_)) * full_h))
            if x1 - x0 > 64 and y1 - y0 > 64:
                scene_img = scene_img.crop((x0, y0, x1, y1))
                crop_off = (x0, y0)

        scene_bgr, _s = _as_bgr(scene_img)
        art_bgr, _a = _as_bgr(art_img)
        sh, sw = scene_bgr.shape[:2]

        # SPEND THE RESOLUTION ON THE PRODUCT. A store packshot is mostly studio background and,
        # here, a second carton — so most of the artwork's detail budget went to pixels the scene
        # never contains. MEASURED live: a faithful, well-drawn Nano Care carton scored 11/62
        # inliers against the whole packshot. Matching only the unit that will be projected keeps
        # the descriptors on the thing being looked for.
        art_bgr, _a, art_img = _crop_to_product(art_img, art_bgr, _a)

        # ONE UNIT, NOT THE SHEET. A store packshot is routinely a studio composition of two or
        # three cartons, and the scene shows one — measured on Nano Care, where the critic's
        # standing complaint was "2 products in an ad for one named product". A homography is a
        # PLANE: warp the whole packshot and the second carton lands on empty counter. So the
        # packshot is split into its physical units first, and only the unit the evidence points
        # at is projected.
        units, unit_map = _units_of(art_img, art_bgr.shape[1], art_bgr.shape[0])

        sift = cv2.SIFT_create(nfeatures=6000)
        # Features from the PRODUCT only. Studio-white background contributes nothing but noise,
        # and every keypoint spent there is a keypoint not spent on the label.
        art_mask = None if unit_map is None else (unit_map > 0).astype(np.uint8) * 255
        kp_a, des_a = sift.detectAndCompute(cv2.cvtColor(art_bgr, cv2.COLOR_BGR2GRAY), art_mask)
        kp_s, des_s = sift.detectAndCompute(cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY), None)
        if des_a is None or des_s is None or len(kp_a) < 8 or len(kp_s) < 8:
            return LabelMatch(reason="too little texture to match"), None

        bf = cv2.BFMatcher()
        good = []
        for pair in bf.knnMatch(des_a, des_s, k=2):
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:   # Lowe's ratio
                good.append(pair[0])
        if len(good) < MIN_INLIERS:
            return LabelMatch(matches=len(good),
                              reason=f"only {len(good)} candidate correspondences"), None

        src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp_s[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0, maxIters=8000, confidence=0.995)
        if H is None or mask is None:
            return LabelMatch(matches=len(good), reason="no homography survived RANSAC"), None

        inlier_idx = [i for i in range(len(good)) if mask[i]]
        m = LabelMatch(matched=False, inliers=len(inlier_idx), matches=len(good))
        if m.inliers < MIN_INLIERS or m.inlier_ratio < MIN_INLIER_RATIO:
            m.reason = (f"{m.inliers}/{len(good)} inliers ({m.inlier_ratio:.0%}) — below the "
                        "floor, so the product was NOT located")
            return m, None

        # WHICH unit matched: the one holding the most inliers.
        unit_id = 0
        if unit_map is not None and units > 1:
            votes: dict = {}
            for i in inlier_idx:
                px, py = kp_a[good[i].queryIdx].pt
                lbl = int(unit_map[min(int(py), unit_map.shape[0] - 1),
                                   min(int(px), unit_map.shape[1] - 1)])
                if lbl:
                    votes[lbl] = votes.get(lbl, 0) + 1
            if votes:
                unit_id = max(votes, key=votes.get)
                # REFIT on that unit alone. The first homography was fitted across every carton
                # in the packshot, so it is a compromise between planes that are not coplanar.
                keep = [i for i in inlier_idx
                        if int(unit_map[min(int(kp_a[good[i].queryIdx].pt[1]), unit_map.shape[0] - 1),
                                        min(int(kp_a[good[i].queryIdx].pt[0]), unit_map.shape[1] - 1)])
                        == unit_id]
                if len(keep) >= MIN_INLIERS:
                    s2 = np.float32([kp_a[good[i].queryIdx].pt for i in keep]).reshape(-1, 1, 2)
                    d2 = np.float32([kp_s[good[i].trainIdx].pt for i in keep]).reshape(-1, 1, 2)
                    H2, m2 = cv2.findHomography(s2, d2, cv2.RANSAC, 3.0, maxIters=8000,
                                                confidence=0.995)
                    if H2 is not None and m2 is not None and int(m2.sum()) >= MIN_INLIERS:
                        H, inlier_idx = H2, [keep[i] for i in range(len(keep)) if m2[i]]
                        m = LabelMatch(inliers=len(inlier_idx), matches=len(good))
                logger.info("[label] packshot holds %s units; unit %s carries the evidence",
                            units, unit_id)

        # THE HOMOGRAPHY ITSELF MUST BE SANE, and an inlier count does not say that it is.
        # MEASURED live: 23/73 inliers (32%) — comfortably over both floors — produced a
        # near-degenerate transform whose vanishing line fell INSIDE the frame, and the artwork
        # exploded into a radial smear across the entire poster. Enough points agreed; they
        # agreed on something physically impossible.
        ok_h, why_h = _homography_is_sane(H, art_bgr.shape[1], art_bgr.shape[0], sw, sh)
        if not ok_h:
            m.reason = f"{m.inliers}/{len(good)} inliers but {why_h}"
            return m, None

        pts = np.float32([kp_a[good[i].queryIdx].pt for i in inlier_idx]).reshape(-1, 1, 2)
        rect = cv2.minAreaRect(pts)
        # GROW THE FOOTPRINT. Keypoints cluster where the artwork has detail and stop short of a
        # plain margin, so the raw rectangle under-covers the face by roughly its own border.
        # Under-covering is the visible failure: a patch of real artwork inside a still-garbled
        # drawn box, with a seam through the middle of the label.
        grown = (rect[0], (rect[1][0] * _FOOTPRINT_GROWTH, rect[1][1] * _FOOTPRINT_GROWTH),
                 rect[2])
        box_pts = cv2.boxPoints(grown)
        corners = cv2.perspectiveTransform(box_pts.reshape(-1, 1, 2), H).reshape(-1, 2)
        # AND THE FACE IT LANDS ON MUST BE A FACE. `validate_quad` already knows what a usable
        # label surface looks like — convex, on the canvas, not a sliver, not a speck. It was
        # written for the vision route and never run on this one, which is how a projected
        # quadrilateral with corners off the edge of the world reached the compositor.
        _projected = LabelQuad(found=True,
                               corners=[(float(px) / sw, float(py) / sh) for px, py in corners])
        ok_q, why_q = validate_quad(_projected)
        if not ok_q:
            m.reason = f"{m.inliers}/{len(good)} inliers but the projected face is unusable: {why_q}"
            return m, None

        m.matched = True
        ssx, ssy = _s
        m.corners = [((crop_off[0] + float(px) / ssx) / full_w,
                      (crop_off[1] + float(py) / ssy) / full_h) for px, py in corners]
        m.reason = f"{m.inliers}/{len(good)} inliers ({m.inlier_ratio:.0%})"
        return m, {
            "H": H,                       # art_small -> scene_small
            "art_scale": _a,              # full -> small, per axis
            "scene_scale": _s,
            "unit_map": unit_map,         # per-pixel unit labels, art_small resolution
            "unit_id": unit_id or 1,
            "footprint": box_pts,         # the matched unit's face, in art_small pixels
            "crop_off": crop_off,         # where the matched crop sat in the full scene
        }
    except Exception as exc:  # noqa: BLE001
        return LabelMatch(reason=f"matching failed ({type(exc).__name__}: {exc})"), None


def _units_of(art_img, w: int, h: int):
    """(count, label map) — the packshot's separate physical units, at the matcher's resolution.

    Built from the same studio-background key the compositor already trusts, so "what is the
    product" has one definition across this codebase. (0, None) when the shot cannot be keyed —
    a photographic background means every pixel is 'product' and there is nothing to separate."""
    cv2, np = _cv()
    try:
        from poster.product_composite import _key_studio_background
        keyed = _key_studio_background(art_img.convert("RGBA"))
        if keyed is None:
            return 0, None
        alpha = np.asarray(keyed.getchannel("A").resize((w, h)))
        binary = (alpha > 40).astype(np.uint8)
        # Close pinholes so one carton is one component, not a constellation of them.
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        n, labels, stats, _c = cv2.connectedComponentsWithStats(binary, connectivity=8)
        # Drop specks: anything under 2% of the keyed area is a reflection or a crumb.
        total = float(max(1, binary.sum()))
        keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] / total >= 0.02]
        if not keep:
            return 0, None
        out = np.zeros_like(labels, dtype=np.int32)
        for new_id, old in enumerate(keep, start=1):
            out[labels == old] = new_id
        return len(keep), out
    except Exception:  # noqa: BLE001
        return 0, None


def reproject_by_match(scene_path: "str | Path", art_img, transform: dict, *,
                       shading: bool = True,
                       out_path: "str | Path | None" = None) -> Optional[str]:
    """Warp the REAL packshot onto the box the model drew, using the matched homography.

    The result keeps the model's position, scale, perspective and — through the shading field —
    its light, while every glyph on the face is the brand's own file. That is the whole hybrid in
    one operation: «إضاءة وظل الـ AI + دقة الحروف الأصلية 100%»."""
    cv2, np = _cv()
    if cv2 is None or not transform:
        return None
    from PIL import Image, ImageFilter

    try:
        scene = Image.open(str(scene_path)).convert("RGBA")
        SW, SH = scene.size

        # The real artwork, background keyed away so only the product travels.
        from poster.product_composite import _key_studio_background
        art = art_img.convert("RGBA")
        keyed = _key_studio_background(art)
        art = keyed if keyed is not None else art

        # H was estimated between the DOWNSCALED pair; lift it to full resolution.
        asx, asy = transform["art_scale"]
        ssx, ssy = transform["scene_scale"]
        A = np.array([[asx, 0, 0], [0, asy, 0], [0, 0, 1]], dtype=np.float64)
        S_inv = np.array([[1.0 / ssx, 0, 0], [0, 1.0 / ssy, 0], [0, 0, 1]], dtype=np.float64)
        # The match may have run on a CROP of the scene, so the homography lands in crop
        # coordinates; translating it back is the difference between the artwork sitting on the
        # product and sitting wherever the crop's origin happened to be.
        ox, oy = transform.get("crop_off", (0, 0))
        T = np.array([[1, 0, float(ox)], [0, 1, float(oy)], [0, 0, 1]], dtype=np.float64)
        H_full = T @ S_inv @ transform["H"] @ A

        # THE WHOLE MATCHED UNIT TRAVELS, and only it. Confining the warp to the convex hull of
        # the keypoints (the first attempt at this) left a ragged patch of real artwork sitting
        # inside a still-garbled drawn box — the seam was visible and half the fine print stayed
        # wrong. The right region is the product's own silhouette, restricted to the unit the
        # evidence pointed at, so the face is replaced edge to edge with nothing spilling over.
        aw, ah = art.size
        rgba = np.asarray(art).copy()
        asx, asy = transform["art_scale"]
        sel = np.full((ah, aw), 255, dtype=np.uint8)
        unit_map, unit_id = transform.get("unit_map"), transform.get("unit_id") or 0
        if unit_map is not None and unit_id:
            sel = cv2.resize((unit_map == unit_id).astype(np.uint8) * 255, (aw, ah),
                             interpolation=cv2.INTER_NEAREST)
        fp = transform.get("footprint")
        if fp is not None:
            # ...AND ONLY THE MATCHED FACE. Two cartons leaning together are ONE connected
            # component, so the silhouette alone cannot separate them — but they are not
            # coplanar, so RANSAC already chose one, and its keypoints say which.
            poly = np.zeros((ah, aw), dtype=np.uint8)
            cv2.fillConvexPoly(poly, (np.asarray(fp, dtype=np.float64)
                                      / np.array([asx, asy])).astype(np.int32), 255)
            sel = np.minimum(sel, poly)
        rgba[:, :, 3] = np.minimum(rgba[:, :, 3], sel)
        warped = cv2.warpPerspective(rgba, H_full, (SW, SH), flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

        # NOTHING LEAVES THE DRAWN OBJECT. A homography fitted to a few dozen keypoints is close
        # but not exact, so the projected artwork overhangs the box's edge by a few pixels and
        # lands on the counter — a small error that reads instantly as a sticker. Segmenting the
        # object the model drew and intersecting with it makes that class of error impossible
        # rather than merely unlikely: whatever the warp's residual, it is clipped to the silhouette.
        drawn = _drawn_object_mask(scene, warped[:, :, 3])
        if drawn is not None:
            warped[:, :, 3] = np.minimum(warped[:, :, 3], drawn)
        layer = Image.fromarray(warped, "RGBA")

        # Soften the cut so the model's own box edge carries the silhouette.
        alpha = layer.getchannel("A").filter(
            ImageFilter.GaussianBlur(radius=max(1.0, min(SW, SH) * 0.0015)))
        layer.putalpha(alpha)

        if shading:
            box = alpha.getbbox()
            if box:
                field = _shading_field(scene, [(box[0], box[1]), (box[2], box[1]),
                                               (box[2], box[3]), (box[0], box[3])])
                if field is not None:
                    from PIL import ImageChops
                    r, g, b, a = layer.split()
                    layer = Image.merge("RGBA", (
                        ImageChops.multiply(r, field).point(lambda v: min(255, v * 2)),
                        ImageChops.multiply(g, field).point(lambda v: min(255, v * 2)),
                        ImageChops.multiply(b, field).point(lambda v: min(255, v * 2)), a))

        out = Image.alpha_composite(scene, layer).convert("RGB")
        dest = Path(out_path) if out_path else Path(str(scene_path)).with_name(
            Path(str(scene_path)).stem + "_label.png")
        out.save(dest)
        logger.info("[label] real packshot reprojected onto the drawn box via homography")
        return str(dest)
    except Exception as exc:  # noqa: BLE001
        logger.info("[label] reprojection failed (%s: %s)", type(exc).__name__, exc)
        return None


def _solve(matrix: list, rhs: list) -> Optional[list]:
    """Gaussian elimination with partial pivoting. Pure Python: this environment has no numpy,
    and an 8x8 solve does not justify a dependency."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None                      # singular: the correspondences are degenerate
        a[col], a[pivot] = a[pivot], a[col]
        inv = 1.0 / a[col][col]
        for r in range(n):
            if r == col:
                continue
            f = a[r][col] * inv
            if f:
                for k in range(col, n + 1):
                    a[r][k] -= f * a[col][k]
    return [a[i][n] / a[i][i] for i in range(n)]


def perspective_coeffs(dst_quad: list, src_quad: list) -> Optional[list]:
    """The 8 coefficients PIL's `Image.transform(..., Image.PERSPECTIVE, ...)` wants.

    PIL's transform is expressed BACKWARDS from the intuition: for each pixel of the OUTPUT it
    asks where to sample the INPUT. So `dst_quad` (the four corners in the scene) is the source
    of the equations and `src_quad` (the four corners of the flat label file) is what they must
    map to — getting this pair the wrong way round produces a warp that is subtly, confusingly
    inverted rather than an obvious error."""
    if len(dst_quad) != 4 or len(src_quad) != 4:
        return None
    m, rhs = [], []
    for (X, Y), (x, y) in zip(dst_quad, src_quad):
        m.append([X, Y, 1, 0, 0, 0, -X * x, -Y * x])
        rhs.append(x)
        m.append([0, 0, 0, X, Y, 1, -X * y, -Y * y])
        rhs.append(y)
    return _solve(m, rhs)


def _shading_field(scene, quad_px: list):
    """The label face's OWN light, as a CANVAS-ALIGNED multiplier map for the real artwork.

    This is the half that makes the result photographic rather than a sticker. The model lit the
    box it drew — key light, falloff, the shadow the shelf throws across its lower third — and
    all of that lives in the luminance of those pixels. Normalising that luminance around the
    face's own mean turns it into a pure shading field: multiply the brand's flat artwork by it
    and the artwork inherits the scene's lighting without inheriting any of its lettering.

    Built at full canvas size on purpose. A field computed on a crop and then stretched back
    would be misaligned with the very pixels it is meant to shade — the highlight would land
    somewhere the model never put one."""
    from PIL import ImageFilter, ImageStat
    xs = [p[0] for p in quad_px]
    ys = [p[1] for p in quad_px]
    box = (max(0, int(min(xs))), max(0, int(min(ys))),
           min(scene.size[0], int(max(xs)) + 1), min(scene.size[1], int(max(ys)) + 1))
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None
    gray = scene.convert("L")
    # Blur away everything that is PRINTED so only the LIGHT survives. Calibrated by eye on the
    # Nano Care carton: at 6% of the face the red band and the white body still read as structure,
    # so the field carried the drawn artwork's own contrast into the real artwork and bleached a
    # patch across the middle of the label. The lighting on a package is a gradient the size of
    # the package; anything finer is ink.
    radius = max(3.0, min(box[2] - box[0], box[3] - box[1]) * _LIGHT_BLUR)
    light = gray.filter(ImageFilter.GaussianBlur(radius=radius))
    mean = ImageStat.Stat(light.crop(box)).mean[0] or 1.0
    # Clamp the swing. A blown highlight must not erase the artwork under it, and this is where
    # honesty about the technique lives: the goal is "lit by this scene", not "relit from
    # scratch", so the correction stays modest on purpose.
    lo, hi = int(128 * (1.0 - _LIGHT_SWING)), int(128 * (1.0 + _LIGHT_SWING))
    lut = [max(lo, min(hi, int(128.0 * (v / mean)))) for v in range(256)]
    return light.point(lut)


def reproject_label(scene_path: "str | Path", label_asset, quad: LabelQuad, *,
                    shading: bool = True, feather: bool = True,
                    out_path: "str | Path | None" = None) -> Optional[str]:
    """Project the real label artwork onto the face the model drew. None if anything is unsafe.

    `label_asset` is the brand's own packshot or label file — bytes, a path or a URL."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    ok, why = validate_quad(quad)
    if not ok:
        logger.info("[label] not reprojected: %s", why)
        return None

    from poster.product_composite import _load
    try:
        scene = Image.open(str(scene_path)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    art = _load(label_asset)
    if art is None:
        logger.info("[label] not reprojected: the artwork could not be loaded")
        return None
    art = art.convert("RGBA")

    W, H = scene.size
    quad_px = [(x * W, y * H) for (x, y) in quad.corners]
    aw, ah = art.size
    # The artwork's own corners, in the same clockwise-from-top-left order the quad uses.
    src = [(0.0, 0.0), (aw - 1.0, 0.0), (aw - 1.0, ah - 1.0), (0.0, ah - 1.0)]
    coeffs = perspective_coeffs(quad_px, src)
    if coeffs is None:
        logger.info("[label] not reprojected: the perspective solve is degenerate")
        return None

    warped = art.transform((W, H), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)

    # Only inside the quad. The transform paints the whole canvas with the artwork's plane, so
    # without this mask the label would tile across the entire poster.
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(int(x), int(y)) for x, y in quad_px], fill=255)
    if feather:
        # A one-pixel cut reads as a decal; a soft edge lets the model's own box edge show
        # through and the join disappears.
        r = max(1.0, min(W, H) * 0.0025)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=r))

    if shading:
        field = _shading_field(scene, quad_px)
        if field is not None:
            r, g, b, a = warped.split()
            # 128 is neutral in the field, so doubling restores unity gain: pixels the model lit
            # brightly lift the artwork, pixels it shadowed darken it.
            warped = Image.merge("RGBA", (
                ImageChops.multiply(r, field).point(lambda v: min(255, v * 2)),
                ImageChops.multiply(g, field).point(lambda v: min(255, v * 2)),
                ImageChops.multiply(b, field).point(lambda v: min(255, v * 2)), a))

    warped.putalpha(ImageChops.multiply(warped.getchannel("A"), mask))
    out = Image.alpha_composite(scene, warped).convert("RGB")
    dest = Path(out_path) if out_path else Path(str(scene_path)).with_name(
        Path(str(scene_path)).stem + "_label.png")
    out.save(dest)
    logger.info("[label] real artwork reprojected onto the drawn face (%.1f%% of canvas)",
                quad.area * 100)
    return str(dest)


def apply_real_label(scene_path: "str | Path", product_asset, *, caller: Any = None,
                     product_name: str = "", roi: Optional[tuple] = None,
                     out_path: "str | Path | None" = None) -> tuple:
    """(path, note) — the hybrid's one entry point. Returns the ORIGINAL scene path when the
    product could not be located, never a half-applied result.

    Two routes, strongest first:
      1. FEATURE MATCH the real packshot against the drawn box. Carries its own confidence (the
         RANSAC inlier count), needs no model call, and lands the artwork at the model's exact
         perspective.
      2. Ask a vision model to corner the label face. No error bar, so it is the fallback — used
         when the packshot is too flat or too plain to key on.

    `roi` is the placement decision's own rectangle (x, y, w, h as canvas fractions). Matching
    inside it first is not a shortcut but a resolution gain: a product occupying a fifth of the
    frame is a few hundred pixels once the canvas is downscaled for SIFT, and that was measured
    live as 8 inliers and a fallback to the paste on a render that was otherwise fine.
    """
    from PIL import Image

    from poster.product_composite import _load
    art = _load(product_asset)
    if art is None:
        return str(scene_path), "the product artwork could not be loaded"
    try:
        scene_img = Image.open(str(scene_path)).convert("RGB")
    except Exception:  # noqa: BLE001
        return str(scene_path), "the scene could not be read"

    m, transform = match_label(scene_img, art, roi=roi)
    if not m.matched and roi:
        # A reservation the model ignored is common — it places the product near where it was
        # asked, not on it. One retry across the whole frame costs nothing but arithmetic.
        m, transform = match_label(scene_img, art)
    if m.matched and transform:
        done = reproject_by_match(scene_path, art, transform, out_path=out_path)
        if done:
            return done, f"real artwork projected onto the drawn product ({m.reason})"
        note = "the matched homography could not be applied"
    else:
        note = m.reason or "the product was not located by matching"

    # THE VISION FALLBACK IS OFF BY DEFAULT, and it was measured into that position rather than
    # assumed. On a live render where the matcher correctly refused, it "found" a label face at
    # 2.3% of the canvas — on a different object — and the real artwork was projected onto it as
    # a red smear beside the product. That is the exact failure mode the docstring warned about:
    # a coordinate answer with no error bar cannot tell a hit from a miss, and here the miss was
    # worse than doing nothing. Set POSTER_LABEL_VISION_FALLBACK=1 to accept that risk.
    import os as _os
    if _os.environ.get("POSTER_LABEL_VISION_FALLBACK") != "1":
        return str(scene_path), note

    quad = find_label_quad(scene_path, caller=caller, product_name=product_name)
    if quad.found:
        done = reproject_label(scene_path, art, quad, out_path=out_path)
        if done:
            return done, f"real artwork projected onto the located label face ({quad.area:.1%})"
    return str(scene_path), f"{note}; the vision fallback also failed ({quad.reason})"


def _drawn_object_mask(scene_img, warped_alpha):
    """The silhouette of the object the MODEL drew, around where the artwork is about to land.

    GrabCut, seeded from the projection: pixels the warp covers are probable foreground, a ring
    beyond it is probable background, and the segmentation settles the boundary on the image's
    own edges rather than on the homography's residual. Returns None when it cannot decide —
    an unsegmented paste is still the measured, shipping behaviour, so this only ever tightens.
    """
    cv2, np = _cv()
    if cv2 is None:
        return None
    try:
        rgb = cv2.cvtColor(np.asarray(scene_img.convert("RGB")), cv2.COLOR_RGB2BGR)
        cover = (warped_alpha > 8).astype(np.uint8)
        if int(cover.sum()) < 64:
            return None
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
        near = cv2.dilate(cover, k, iterations=2)

        gc = np.full(cover.shape, cv2.GC_BGD, dtype=np.uint8)
        gc[near > 0] = cv2.GC_PR_BGD
        gc[cover > 0] = cv2.GC_PR_FGD
        # A confident core: the warp's centre is certainly on the object.
        core = cv2.erode(cover, k, iterations=1)
        gc[core > 0] = cv2.GC_FGD
        if int((gc == cv2.GC_FGD).sum()) < 32 or int((gc == cv2.GC_BGD).sum()) < 32:
            return None

        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(rgb, gc, None, bgd, fgd, 3, cv2.GC_INIT_WITH_MASK)
        out = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        # The segmentation must AGREE with the projection, not replace it. If it kept almost
        # nothing, or ballooned past the region we seeded, it found some other object.
        kept = float((out & (cover * 255)).sum()) / float(max(1, (cover * 255).sum()))
        grown = float(out.sum()) / float(max(1, (near * 255).sum()))
        if kept < 0.55 or grown > 1.05:
            logger.info("[label] object segmentation disagreed (kept %.0f%%, grew %.0f%%) — "
                        "keeping the unclipped projection", kept * 100, grown * 100)
            return None
        return cv2.GaussianBlur(out, (5, 5), 0)
    except Exception:  # noqa: BLE001
        return None


# A planar homography that maps a packshot onto a box in a photograph is a mild transform: some
# rotation, some scale, a little perspective. These bounds describe that, and reject the rest.
_MAX_SCALE_RATIO = 12.0      # the face may not be a twelfth, or a dozen times, the art's size
_MIN_W_MARGIN = 0.08         # how far the vanishing line must stay from the projected corners


def _homography_is_sane(H, aw: int, ah: int, sw: int, sh: int) -> tuple:
    """(ok, reason) for a homography before anything is warped through it.

    The failure this exists for is specific and was measured, not imagined: RANSAC can reach high
    inlier agreement on a transform whose VANISHING LINE crosses the image. Points near that line
    divide by ~zero, and the warp becomes a radial explosion — visually unmistakable, and
    invisible to every check that only counts agreeing points."""
    cv2, np = _cv()
    if cv2 is None or H is None:
        return False, "no homography"
    try:
        H = np.asarray(H, dtype=np.float64)
        if not np.all(np.isfinite(H)) or abs(H[2, 2]) < 1e-9:
            return False, "the transform is not finite"

        corners = np.float32([[0, 0], [aw - 1, 0], [aw - 1, ah - 1], [0, ah - 1]])
        hom = np.hstack([corners, np.ones((4, 1), dtype=np.float32)]) @ H.T
        w = hom[:, 2]
        # SAME SIGN, AND NOT NEAR ZERO. A sign flip across the corners means the plane folds
        # through the camera; a near-zero w is the vanishing line passing through the artwork.
        if not (np.all(w > 0) or np.all(w < 0)):
            return False, "the projected plane folds through the camera"
        scale = max(abs(H[2, 2]), 1e-9)
        if float(np.min(np.abs(w))) / scale < _MIN_W_MARGIN:
            return False, "the vanishing line crosses the artwork (the warp would diverge)"

        # Area: a plausible product face, not a speck and not the whole world.
        proj = hom[:, :2] / w[:, None]
        area = 0.0
        for i in range(4):
            x1, y1 = proj[i]
            x2, y2 = proj[(i + 1) % 4]
            area += x1 * y2 - x2 * y1
        area = abs(area) / 2.0
        src_area = float(max(1, (aw - 1) * (ah - 1)))
        ratio = area / src_area
        if not (1.0 / (_MAX_SCALE_RATIO ** 2) < ratio < _MAX_SCALE_RATIO ** 2):
            return False, f"the projected face is {ratio:.3g}x the artwork's area"
        if area > float(sw) * float(sh) * 4.0:
            return False, "the projection is larger than the scene it lands in"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"sanity check failed ({type(exc).__name__})"


def _crop_to_product(art_img, art_bgr, scale):
    """(bgr, scale, pil) cropped to the packshot's product, re-scaled to the matcher's budget.

    A packshot is a studio composition: white ground, sometimes a second unit, sometimes props.
    Downscaling the whole sheet to fit SIFT's working size spends most of that size on background
    — the descriptors that matter end up computed on a carton a few hundred pixels tall. Cropping
    first and THEN downscaling gives the same budget to the product alone.

    Returns the inputs unchanged when the shot cannot be keyed: a photographic background means
    there is no "product region" to isolate, and guessing one would crop the subject in half."""
    cv2, np = _cv()
    if cv2 is None:
        return art_bgr, scale, art_img
    try:
        from poster.product_composite import _key_studio_background
        keyed = _key_studio_background(art_img.convert("RGBA"))
        if keyed is None:
            return art_bgr, scale, art_img
        box = keyed.getchannel("A").getbbox()
        if not box:
            return art_bgr, scale, art_img
        w, h = art_img.size
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area >= 0.80 * w * h:
            return art_bgr, scale, art_img       # already tight; cropping buys nothing
        pad_x = int((box[2] - box[0]) * 0.04)
        pad_y = int((box[3] - box[1]) * 0.04)
        tight = art_img.crop((max(0, box[0] - pad_x), max(0, box[1] - pad_y),
                              min(w, box[2] + pad_x), min(h, box[3] + pad_y)))
        bgr, sc = _as_bgr(tight)
        logger.info("[label] packshot cropped to its product (%.0f%% -> 100%% of the frame)",
                    100.0 * area / float(w * h))
        return bgr, sc, tight
    except Exception:  # noqa: BLE001
        return art_bgr, scale, art_img
