# -*- coding: utf-8 -*-
"""Put the copy where the canvas is CLEAR — measured, not hoped.

REVIEWER (2026-07-26, on the v7 render): «التلت العلوي فاضي 99%… يعني تقريباً نص البوستر مفيهوش
أي معلومة، وكل الحمل واقع على التلت السفلي المزنوق. الهيدلاين مكانه الطبيعي فوق في الفراغ ده —
وكده بتحل مشكلتين بضربة واحدة: الكونتراست والتوزيع».

The layout has always defaulted the copy to the bottom band while the scene model happily
leaves a third of the canvas empty. This measures each horizontal band's BUSYNESS (local
detail) and offers the clearest one as the copy's home. Deterministic Pillow arithmetic;
returns None when no band is meaningfully clearer than the rest, so a deliberately full-bleed
composition keeps its designed layout.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _band_busyness(gray, x0, y0, x1, y1) -> float:
    """Mean local detail inside the box: the stddev of the band plus edge density from a
    small-kernel difference — a plain gradient scores LOW (it can host copy), a face or a
    product scores HIGH."""
    from PIL import ImageFilter, ImageStat
    crop = gray.crop((x0, y0, x1, y1))
    stat = ImageStat.Stat(crop)
    edges = crop.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    return float(stat.stddev[0]) * 0.4 + float(edge_stat.mean[0]) * 0.6


def clearest_copy_zone(background: "str | Path", *, product_zone: Optional[tuple] = None,
                       min_advantage: float = 0.30) -> Optional[Tuple[float, float, float]]:
    """The (x, y, w) text_box for the clearest horizontal third — or None.

    `product_zone` (x, y, h fractions) is excluded from candidacy: the copy must not move ONTO
    the composited product while fleeing a busy background. `min_advantage` is how much clearer
    (relative) the winner must be than the busiest band — below that, the canvas is uniform and
    the DESIGNED layout stands."""
    try:
        from PIL import Image
        with Image.open(str(background)) as im:
            gray = im.convert("L")
            W, H = gray.size
            bands = {
                "top": (0, 0, W, int(H * 0.33)),
                "middle": (0, int(H * 0.33), W, int(H * 0.66)),
                "bottom": (0, int(H * 0.66), W, H),
            }
            scores = {k: _band_busyness(gray, *box) for k, box in bands.items()}
    except Exception:  # noqa: BLE001
        return None

    if product_zone:
        py = float(product_zone[1])
        ph = float(product_zone[2]) if len(product_zone) > 2 else 0.4
        for name, (x0, y0, x1, y1) in bands.items():
            b0, b1 = y0 / H, y1 / H
            if b0 < py + ph and b1 > py:            # the band overlaps the product
                scores[name] = scores.get(name, 0) + 1e6

    best = min(scores, key=scores.get)
    worst = max(scores.values())
    if worst <= 0 or (worst - scores[best]) / worst < float(min_advantage):
        return None
    logger.info("[placement] copy -> %s band (busyness %s)",
                best, {k: round(v, 1) for k, v in scores.items()})
    y = {"top": 0.07, "middle": 0.38, "bottom": 0.62}[best]
    return (0.06, y, 0.88)
