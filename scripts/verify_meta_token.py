# -*- coding: utf-8 -*-
"""Answer ONE question before any real pull: does META_ADS_TOKEN actually work?

The Ad Library API fails in several different ways that all look the same from inside the app
(missing token, identity not confirmed, wrong token type, expired token, no API access yet). This
makes a single minimal request and translates whatever comes back into the ONE thing to fix.

    python -m scripts.verify_meta_token
    python -m scripts.verify_meta_token --country EG --query "Nike"

The token is NEVER printed, logged or written anywhere — only its length and the verdict. Free:
the Ad Library API costs nothing, and this asks for a single ad.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_ENDPOINT = "https://graph.facebook.com/v21.0/ads_archive"


# Windows consoles default to cp1252 and raise on Arabic — the verdict below is Arabic, so an
# unforced console would crash before printing the one line the owner needs.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass


def _diagnose(status: int, payload: dict) -> tuple[str, str]:
    """(verdict, what-to-do) from Meta's own error shape — never a guess dressed as a fact."""
    err = (payload or {}).get("error") or {}
    msg = str(err.get("message") or "")
    code = err.get("code")
    sub = err.get("error_subcode")
    low = msg.lower()
    if code == 190 or "access token" in low:
        return ("التوكن نفسه مرفوض",
                "التوكن غلط أو منتهي — ولّدي واحد جديد وحطيه في .env")
    if "not been approved" in low or "ad library api" in low or sub in (2332002, 2332003):
        return ("الحساب لسه ماخدش صلاحية Ad Library API",
                "تأكيد الهوية خطوة واحدة — كمان لازم طلب الوصول لـ Ad Library API نفسه "
                "من facebook.com/ads/library/api")
    if code == 200 or "permission" in low:
        return ("التوكن شغال بس ناقصه صلاحية",
                "التوكن محتاج صلاحية ads_read")
    if code == 4 or code == 17 or "limit" in low:
        return ("وصلتي حد الاستخدام مؤقتاً", "استني شوية وجرّبي تاني — التوكن نفسه سليم")
    return (f"رفض من ميتا (HTTP {status})", msg[:200] or "مفيش رسالة من ميتا")


def _lifetime_note(debug: dict, *, now: float) -> tuple[str, bool]:
    """(human note, is_short_lived) from Graph's /debug_token payload.

    A Graph-Explorer token lives ~1-2 hours. Without this the system works, then silently stops,
    and the failure looks like a bug in the pull rather than an expired credential.
    """
    data = (debug or {}).get("data") or {}
    exp = data.get("expires_at")
    if not exp:
        return ("التوكن ده مالوش تاريخ انتهاء ✓", False)
    left = float(exp) - float(now)
    if left <= 0:
        return ("التوكن ده منتهي بالفعل", True)
    hours = left / 3600.0
    if hours < 1:
        return (f"التوكن ده بينتهي بعد {int(left // 60)} دقيقة — قصير العمر", True)
    if hours < 24:
        return (f"التوكن ده بينتهي بعد {hours:.0f} ساعة — قصير العمر", True)
    return (f"التوكن ده صالح {int(hours // 24)} يوم ✓", False)


def _replace_env_token(env_path, token: str) -> bool:
    """Write the long-lived token into .env IN PLACE — replacing only the META_ADS_TOKEN line and
    leaving every other key byte-identical. Deliberately writes to the file instead of printing:
    a credential should not end up in console scrollback. Refuses an empty value so a failed
    exchange can never wipe a working token."""
    from pathlib import Path as _P
    token = (token or "").strip()
    if not token:
        return False
    p = _P(env_path)
    try:
        lines = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
        out, replaced = [], False
        for ln in lines:
            if ln.strip().startswith("META_ADS_TOKEN=") and not replaced:
                out.append(f"META_ADS_TOKEN={token}")
                replaced = True
            else:
                out.append(ln)
        if not replaced:
            out.append(f"META_ADS_TOKEN={token}")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


def _graph_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _check_lifetime(token: str, *, exchange: bool) -> None:
    """Report the token's remaining life and, with --long-lived, swap it for a 60-day one."""
    import time
    app_id = os.environ.get("META_APP_ID") or ""
    app_secret = os.environ.get("META_APP_SECRET") or ""
    if not (app_id and app_secret):
        print("[i] مش قادر أقيس عمر التوكن (META_APP_ID/SECRET ناقصين)")
        return
    try:
        qs = urllib.parse.urlencode({"input_token": token,
                                     "access_token": f"{app_id}|{app_secret}"})
        note, short = _lifetime_note(
            _graph_json(f"https://graph.facebook.com/debug_token?{qs}"), now=time.time())
    except Exception as e:  # noqa: BLE001
        print(f"[i] مش قادر أقيس عمر التوكن ({type(e).__name__})")
        return
    print(f"[i] {note}")
    if not short:
        return
    if not exchange:
        print("    شغّلي:  python -m scripts.verify_meta_token --long-lived")
        print("    هيبدّله بتوكن 60 يوم ويحطه في .env لوحده (مش هيتطبع في الشاشة).")
        return
    try:
        qs = urllib.parse.urlencode({
            "grant_type": "fb_exchange_token", "client_id": app_id,
            "client_secret": app_secret, "fb_exchange_token": token})
        new = (_graph_json(f"https://graph.facebook.com/v21.0/oauth/access_token?{qs}")
               .get("access_token") or "")
    except Exception as e:  # noqa: BLE001
        print(f"[X] التبديل فشل ({type(e).__name__}) — التوكن القديم زي ما هو")
        return
    if _replace_env_token(".env", new):
        print("[OK] اتبدّل بتوكن طويل العمر واتحفظ في .env ✓ (شغّلي الأداة تاني للتأكيد)")
    else:
        print("[X] مقدرتش أكتب في .env — التوكن القديم زي ما هو")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="EG")
    ap.add_argument("--query", default="Vodafone", help="any brand name — we only need a response")
    ap.add_argument("--long-lived", action="store_true",
                    help="swap a short-lived token for a 60-day one, written straight into .env")
    args = ap.parse_args()

    _load_env()
    token = os.environ.get("META_ADS_TOKEN") or ""
    if not token:
        print("[X] META_ADS_TOKEN مش موجود في .env")
        print("    ضيفي السطر ده في ملف .env:  META_ADS_TOKEN=<التوكن>")
        return 2
    print(f"[i] التوكن موجود ({len(token)} حرف) — ببعت طلب واحد للتأكد…")

    qs = urllib.parse.urlencode({
        "search_terms": args.query, "ad_reached_countries": f'["{args.country}"]',
        "fields": "id,page_name", "limit": 1, "access_token": token,
    })
    try:
        with urllib.request.urlopen(f"{_ENDPOINT}?{qs}", timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        n = len(data.get("data") or [])
        print(f"[OK] التوكن شغال ✓ — ميتا ردّت بـ{n} إعلان للاختبار")
        _check_lifetime(token, exchange=args.long_lived)
        print("     كل تحليل جديد هيسحب إعلانات المنافسين أوتوماتيك (بالسقوف المعمولة).")
        return 0
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        verdict, fix = _diagnose(e.code, payload)
        print(f"[X] {verdict}")
        print(f"    {fix}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[X] مقدرتش أوصل لميتا ({type(e).__name__}) — شوفي النت/البروكسي")
        return 1


if __name__ == "__main__":
    sys.exit(main())
