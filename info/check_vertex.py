# -*- coding: utf-8 -*-
"""هل ڤيرتكس شغّال على الجهاز ده؟ — فحص واحد، أقل من سنت.

القاعدة المسجَّلة في `info/STATE.md`: **الحساب النشط + ADC + `GOOGLE_CLOUD_PROJECT` لازم التلاتة
على نفس المشروع.** لو واحد اختلف، الرد بيبقى 403 وشكله زي إيقاف فوترة وهو مش كده — وده ضيّع وقت
مرتين. الملف ده بيطبع التلاتة جنب بعض **وبعدين** بيعمل نداء حقيقي رخيص، عشان الفرق يبان.

    python info/check_vertex.py
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _gcloud(*args: str) -> str:
    try:
        p = subprocess.run(["gcloud", *args], capture_output=True, text=True, timeout=90,
                           shell=(os.name == "nt"))
        return (p.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"<gcloud unavailable: {type(exc).__name__}>"


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass

    env_project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    account = _gcloud("config", "get-value", "account")
    cfg_project = _gcloud("config", "get-value", "project")

    adc_project = ""
    adc_account = ""
    try:
        import json
        adc = Path(os.path.expandvars(r"%APPDATA%\gcloud\application_default_credentials.json"))
        if not adc.is_file():
            adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        if adc.is_file():
            d = json.loads(adc.read_text(encoding="utf-8"))
            adc_project = str(d.get("quota_project_id") or "")
            adc_account = str(d.get("account") or "")
        else:
            adc_project = "<no ADC file — run: gcloud auth application-default login>"
    except Exception as exc:  # noqa: BLE001
        adc_project = f"<unreadable: {type(exc).__name__}>"

    print("\n=== التطابق التلاثي (لازم يبقوا نفس المشروع) ===\n")
    print(f"  الحساب النشط        : {account or '<فاضي>'}")
    print(f"  مشروع gcloud        : {cfg_project or '<فاضي>'}")
    print(f"  ADC quota project   : {adc_project or '<فاضي>'}"
          + (f"   (حساب ADC: {adc_account})" if adc_account else ""))
    print(f"  .env                : {env_project or '<فاضي>'}")

    same = env_project and cfg_project == env_project and adc_project == env_project
    print("\n  " + ("✅ التلاتة متطابقين" if same else
                    "⚠️ فيه اختلاف — ده بالظبط اللي بيطلّع 403 شكله زي وقف فوترة"))

    print("\n=== نداء حقيقي (أرخص موديل، أقل من سنت) ===\n")
    try:
        from pydantic import BaseModel

        from business_profile.llm import default_caller

        class _R(BaseModel):
            ok: str = ""

        caller = default_caller(strong=False)
        if caller is None:
            print("  ❌ مافيش caller أصلاً (creds/SDK ناقصين)")
            return 2
        resp, usage = caller("Reply with ok=yes.", "ping", _R, group_name="check_vertex")
        print(f"  ✅ LIVE — رد فعلاً · موديل={getattr(usage, 'model', '?')} "
              f"· ${getattr(usage, 'cost_usd', 0) or 0:.6f}")
        print("\n  كل حاجة شغالة: البروفايل، الـSWOT، الاستراتيجية، البوستر، الريل.")
        return 0
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print(f"  ❌ BLOCKED — {type(exc).__name__}")
        if "dunning" in msg.lower():
            print("     السبب: **فوترة موقوفة** على المشروع ده (مش صلاحيات).")
        elif "PERMISSION_DENIED" in msg or "403" in msg:
            print("     السبب: **الحساب مالوش صلاحية** على المشروع اللي في .env — شوف التطابق فوق.")
        else:
            print(f"     {msg[:220]}")
        print("\n  الحل (بترتيب):")
        print("     gcloud auth login")
        print(f"     gcloud config set project {env_project or '<PROJECT>'}")
        print("     gcloud auth application-default login")
        print(f"     gcloud auth application-default set-quota-project {env_project or '<PROJECT>'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
