# -*- coding: utf-8 -*-
"""The token verifier must name the ONE thing to fix — and never leak the token.

Meta's Ad Library API fails in ways that look identical from inside the app (no token, identity not
confirmed, no API access, wrong scope, expired). Guessing between them wastes the owner's time, so
the verdict comes from Meta's own error code/subcode. Hermetic — no network."""
from __future__ import annotations

from scripts.verify_meta_token import _diagnose


def test_rejected_token_is_named_as_such():
    v, fix = _diagnose(400, {"error": {"code": 190, "message": "Invalid OAuth access token"}})
    assert "التوكن" in v and ("جديد" in fix or "منتهي" in fix)


def test_missing_api_access_is_distinguished_from_a_bad_token():
    v, fix = _diagnose(400, {"error": {
        "code": 10, "error_subcode": 2332002,
        "message": "Your application has not been approved for the Ad Library API"}})
    assert "Ad Library" in v
    assert "تأكيد الهوية خطوة واحدة" in fix          # identity done != access granted


def test_missing_scope_is_its_own_verdict():
    v, fix = _diagnose(403, {"error": {"code": 200, "message": "Permissions error"}})
    assert "صلاحية" in v and "ads_read" in fix


def test_rate_limit_does_not_blame_the_token():
    v, fix = _diagnose(400, {"error": {"code": 4, "message": "Application request limit reached"}})
    assert "حد الاستخدام" in v and "سليم" in fix


def test_unknown_errors_are_reported_verbatim_not_invented():
    v, fix = _diagnose(500, {"error": {"code": 999, "message": "Something odd happened"}})
    assert "500" in v and "Something odd happened" in fix


def test_the_token_value_is_never_printed():
    """The credential may be WRITTEN to .env (that is the point of --long-lived) but must never
    reach stdout, where it would live on in console scrollback. So the check is specifically about
    print(), not about the token appearing in the file at all."""
    import inspect
    import re
    import scripts.verify_meta_token as m
    printed = re.findall(r"print\((.*)", inspect.getsource(m))
    for call in printed:
        assert not re.search(r"\btoken\b(?!\s*=)", call.replace("len(token)", "")), call
    assert "len(token)" in inspect.getsource(m)      # length only, as proof of presence
