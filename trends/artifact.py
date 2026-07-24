# -*- coding: utf-8 -*-
"""The trends artifact: `outputs/<slug>_trends.json`.

Owner rule (2026-07-20): every dataset the system produces gets its OWN file in `outputs/`, so she
can open it, diff it between runs, and hand it to a client without exporting the whole analysis
blob. The trends used to live only inside `<slug>_analysis.json`.

The `_meta` block is what makes the pull auditable rather than a list of links: WHICH seed keywords
were searched, in WHICH market (gl/hl — and an explicit note when the evidence never named one, so
an empty market is never read as "global"), how many items survived, and the MEASURED noise
disclosure that must travel with any single-snapshot SERP result.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence


def _plain(item: Any) -> Any:
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return dataclasses.asdict(item)
    if hasattr(item, "model_dump"):
        try:
            return item.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            pass
    return item


def write_trends_file(slug: str, items: Sequence[Any], *, out_dir: str = "outputs",
                      keywords: Optional[Sequence[str]] = None, gl: str = "",
                      hl: str = "", backfilled_from: str = "") -> Optional[str]:
    """Write the brand's trend pull to its own file. Returns the path, or None on any failure
    (an artifact is a convenience — it must never break an analysis run).

    ``backfilled_from`` marks a file rebuilt from an older saved analysis: that run DID search a
    market, we simply never recorded which. Saying "no market was resolved" there would be a true
    sentence answering a different question (the D-8 failure class), so the note says the market
    wasn't RECORDED — not that it was absent.
    """
    try:
        from trends.engine import NOISE_DISCLOSURE
        rows = [_plain(i) for i in (items or [])]
        kws = [str(k) for k in (keywords or []) if str(k).strip()]
        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seed_keywords": kws,
            "gl": gl or "",
            "hl": hl or "",
            "market_note": (
                f"سوق البحث: {gl}/{hl}" if gl else
                ("سوق البحث وقت السحب غير مسجّل في هذا الملف (اتبنى من تحليل محفوظ أقدم) — "
                 "أعيدي السحب لو محتاجة السوق موثّق" if backfilled_from else
                 "سوق البحث غير محدد — الأدلة ما ذكرتش بلد، فالنتيجة عامة مش محلية")),
            "count": len(rows),
            "disclosure": NOISE_DISCLOSURE,
        }
        if backfilled_from:
            meta["backfilled_from"] = backfilled_from
        if not rows:
            meta["empty_reason"] = ("مفيش إشارات على-الموضوع طلعت من البحث "
                                    + ("(مفيش كلمات بذرة اتولدت من البروفايل)" if not kws
                                       else "(البذور موجودة لكن كل النتايج اتفلترت كضوضاء/براند نفسه)"))
        path = Path(out_dir) / f"{slug}_trends.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"items": rows, "_meta": meta}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return str(path)
    except Exception:  # noqa: BLE001
        return None
