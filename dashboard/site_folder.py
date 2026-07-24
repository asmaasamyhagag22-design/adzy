# -*- coding: utf-8 -*-
"""One folder per site: `outputs/<slug>/` holding everything produced for that brand.

Owner's rule (2026-07-20): "اعمل لأي موقع فولدر فيه كل النتايج ولو طلبت سكراب خدمة معينة تعملها
فايل بنتائجها". `outputs/` had become a flat pile mixing every brand, so handing a client THEIR
results meant picking files out by filename prefix — and anything not obviously named was simply
never found.

The folder is a DELIVERABLE view: files are copied in, the flat originals stay exactly where every
existing reader expects them, so nothing has to be migrated and no path in the codebase breaks.
Each folder carries an `index.json` + `README.md` saying what each file is, because a folder of
`*_result.json` a client can't interpret is not a deliverable either.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# What each artifact IS, in the owner's language. Suffix -> description.
_WHAT = {
    "_profile.json": "الملف الشخصي للبراند — كل حقيقة مستخرجة من الموقع مع مصدرها",
    "_result.json": "نتيجة التحليل الكاملة — المنافسون + SWOT + TOWS + الخطة",
    "_trends.json": "إشارات السوق — البذور، السوق (gl/hl)، وإفصاح الضوضاء",
    "_plan.json": "تقويم المحتوى — المنشورات المقترحة بتواريخها",
    "_plan.audit.json": "أثر التدقيق لتقويم المحتوى — كل ادعاء ومصدره",
    "_media_plan.json": "خطة الميديا — الأهداف والميزانية وحدود الأمان",
    "_owner_inputs.json": "أرقامك اللي أدخلتيها (الهامش/الطاقة/الميزانية)",
    "_service_scrapes.json": "نتائج سكراب الخدمات/المنتجات اللي طلبتيها بالتفصيل",
    "_poster.png": "البوستر المولَّد — جاهز للنشر",
    "_poster_prompt.txt": "برومبت البوستر — اللي وافقتي عليه قبل التوليد",
    "_reel.mp4": "الريل المولَّد",
    "_reel_plan.json": "خطة الريل — وجهة النظر والقوس السردي والمشاهد",
    "_war_room.html": "العرض التقديمي (10 صفحات) — للعميل",
    "_dashboard.html": "الداشبورد الكلاسيكية",
    "_audit.json": "أثر الالتزام — كل ادعاء في البوستر ومصدره",
}


# Below this, a file is re-copied every time rather than compared — correctness over a few ms.
_ALWAYS_COPY_MAX_BYTES = 5 * 1024 * 1024


def site_dir(slug: str, out_dir: str = "outputs") -> Path:
    return Path(out_dir) / slug


def _describe(name: str, slug: str) -> str:
    tail = name[len(slug):] if name.startswith(slug) else name
    for suffix, what in _WHAT.items():
        if tail == suffix or tail.endswith(suffix):
            return what
    return "ملف ناتج عن التشغيل"


def _sources(slug: str, out: Path) -> list[Path]:
    """Every file that belongs to THIS brand. Prefix-matched with a boundary check so
    `brand` never collects `brandx`'s files."""
    found: list[Path] = []
    for p in list(out.glob(f"{slug}_*")) + list((out / "posters").glob(f"{slug}_*")) \
            + list((out / "reels").glob(f"{slug}_*")):
        if p.is_file():
            found.append(p)
    return sorted(found)


def collect_site_folder(slug: str, out_dir: str = "outputs") -> Optional[str]:
    """Gather everything for `slug` into `outputs/<slug>/`. Returns the path, or None when the
    brand has produced nothing yet (no empty folders left behind). Never raises."""
    out = Path(out_dir)
    try:
        sources = _sources(slug, out)
        if not sources:
            return None
        dest = site_dir(slug, out_dir)
        dest.mkdir(parents=True, exist_ok=True)
        entries = []
        for src in sources:
            target = dest / src.name
            try:
                # Small artifacts are ALWAYS re-copied. An mtime comparison looks like a sensible
                # optimisation and silently ships a stale deliverable: `copy2` preserves the source
                # mtime, so a file edited within the same filesystem tick compares as "current" and
                # the client gets the previous version. Only genuinely large media (video) is worth
                # the skip, and there mtime+size together are a safe signal.
                big = src.stat().st_size > _ALWAYS_COPY_MAX_BYTES
                if (big and target.exists()
                        and target.stat().st_mtime == src.stat().st_mtime
                        and target.stat().st_size == src.stat().st_size):
                    pass
                else:
                    shutil.copy2(src, target)
            except Exception:  # noqa: BLE001 — one locked file must not lose the rest
                continue
            entries.append({"file": src.name,
                            "what": _describe(src.name, slug),
                            "bytes": target.stat().st_size if target.exists() else 0})
        index = {"slug": slug,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "files": entries}
        (dest / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        lines = [f"# {slug} — كل نتائج التحليل", "",
                 f"اتولّد: {index['generated_at']}", "",
                 "| الملف | إيه ده |", "|---|---|"]
        lines += [f"| `{e['file']}` | {e['what']} |" for e in entries]
        lines += ["", "> الملفات دي نسخة تسليم. الأصول في `outputs/` زي ما هي."]
        (dest / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(dest)
    except Exception:  # noqa: BLE001 — a deliverable view must never break a run
        return None
