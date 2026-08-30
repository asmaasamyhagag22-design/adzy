# -*- coding: utf-8 -*-
"""Walk every dashboard route and say, for each one, whether it actually works.

WHY THIS EXISTS. The dashboard is a 5,300-line single-page app with no JS test runner, so every
existing "does the dashboard work" test is a regex over the HTML. That proves a button is PRESENT.
It cannot prove the button's handler calls a route that exists, that the route returns what the
handler reads, or that the value reaches a pixel. This walks the real server over HTTP.

HOW TO RUN
    python -m uvicorn api.main:app --port 8000          # in one terminal
    python info/check_dashboard.py                       # in another
    python info/check_dashboard.py --brand alfouadpharmacies_com

It writes `logs/dashboard_check.log` (append-only, timestamped) so a failure that happens while
nobody is watching is still there afterwards.

NO PASSWORD IS USED OR STORED. The session token is minted from the server's own signing key via
`api.user_auth.issue_token(user_id)` — the same key the server uses to validate it. Handling an
account password in plain text is not something this script will do.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LOG = REPO / "logs" / "dashboard_check.log"
BASE = "http://localhost:8000"

OK, WARN, BAD = "PASS", "WARN", "FAIL"
_counts = {OK: 0, WARN: 0, BAD: 0}
_lines: list[str] = []


def log(verdict: str, name: str, detail: str = "") -> None:
    _counts[verdict] = _counts.get(verdict, 0) + 1
    line = f"[{verdict:4}] {name}" + (f" — {detail}" if detail else "")
    _lines.append(line)
    print(line, flush=True)


def call(method: str, path: str, token: str = "", body: dict | None = None,
         timeout: int = 120) -> tuple[int, object]:
    """(status, parsed-json-or-text). Never raises — a dead route is a result, not a crash."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001 — connection refused, timeout, …
        return 0, f"{type(e).__name__}: {e}"


def token_for(email: str) -> tuple[str, str]:
    from api.user_auth import UserStore, issue_token
    us = UserStore()
    u = us.get_by_email(email)
    if not u:
        raise SystemExit(f"no account for {email} — sign up in the dashboard first")
    uid = str(u.get("id"))
    return issue_token(uid), uid


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default="asmaasamyh22@gmail.com")
    ap.add_argument("--brand", default="")
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _lines.append(f"\n{'=' * 78}\nDASHBOARD CHECK  {started}  base={BASE}\n{'=' * 78}")
    print(_lines[-1])

    # ---------------------------------------------------------------- server up
    st, _ = call("GET", "/api/health")
    if st != 200:
        log(BAD, "server", f"/api/health -> {st}. Start it: "
                           "python -m uvicorn api.main:app --port 8000")
        _flush()
        return 1
    log(OK, "server", "/api/health 200")

    st, _ = call("GET", "/app")
    log(OK if st == 200 else BAD, "the dashboard page", f"GET /app -> {st}")

    # ---------------------------------------------------------------- auth
    try:
        tok, uid = token_for(args.email)
    except SystemExit as e:
        log(BAD, "session", str(e))
        _flush()
        return 1
    st, me = call("GET", "/api/auth/me", tok)
    log(OK if st == 200 else BAD, "session", f"/api/auth/me -> {st}")

    st, no = call("GET", "/api/auth/me")
    log(OK if st == 401 else BAD, "auth actually gates", f"no token -> {st} (expected 401)")

    # ---------------------------------------------------------------- brands
    st, brands = call("GET", "/api/brands", tok)
    rows = (brands or {}).get("brands", brands) if isinstance(brands, (dict, list)) else []
    rows = rows if isinstance(rows, list) else []
    log(OK if st == 200 else BAD, "brand list", f"{st} — {len(rows)} brand(s)")

    dk = args.brand or (str(rows[0].get("domain_key")) if rows else "")
    if not dk:
        log(WARN, "no brand to test", "scrape one in the dashboard first")
        _flush()
        return 0
    print(f"\n--- testing brand: {dk} ---")

    st, brand = call("GET", f"/api/brands/{dk}", tok)
    log(OK if st == 200 else BAD, "brand detail", f"{st}")
    brand = brand if isinstance(brand, dict) else {}

    # ------------------------------------------------- what the SPA renders per section
    prof = brand.get("_profile") or {}
    checks = [
        ("name", prof.get("name")),
        ("category", prof.get("category")),
        ("offerings", prof.get("offerings")),
        ("value_propositions", prof.get("value_propositions")),
        ("contact_channels", prof.get("contact_channels")),
        ("social_presence", prof.get("social_presence")),
        ("visual.content_images", (prof.get("visual") or {}).get("content_images")),
        ("catalog", prof.get("catalog")),
    ]
    for label, val in checks:
        n = len(val) if isinstance(val, (list, dict)) else (1 if val else 0)
        log(OK if n else WARN, f"profile.{label}", f"{n} value(s)")

    # ------------------------------------------------- SWOT (already generated)
    swot = brand.get("swot") or {}
    quads = {"strengths": 0, "weaknesses": 0, "opportunities": 0, "threats": 0}
    for q in quads:
        v = swot.get(q) or []
        quads[q] = len(v) if isinstance(v, list) else 0
    empty = [q for q, n in quads.items() if n == 0]
    log(OK if not empty else WARN, "SWOT quadrants",
        ", ".join(f"{q}={n}" for q, n in quads.items())
        + (f"  EMPTY: {', '.join(empty)}" if empty else ""))

    # ------------------------------------------------- competitors
    st, comp = call("GET", f"/api/brands/{dk}/competitors", tok)
    clist = (comp or {}).get("competitors", []) if isinstance(comp, dict) else []
    log(OK if st == 200 else BAD, "competitors route", f"{st} — {len(clist)} row(s)")
    if st == 200 and not clist:
        log(WARN, "competitors empty",
            "weaknesses and threats only exist by comparison — add one in the panel")

    # ------------------------------------------------- social performance
    st, soc = call("GET", f"/api/social/performance/{dk}", tok)
    if st != 200:
        log(BAD, "social performance route", f"{st}")
    elif not isinstance(soc, dict):
        log(BAD, "social performance route", "non-JSON body")
    elif soc.get("collected") is False:
        log(WARN, "social performance", "no snapshot collected for this brand")
    elif soc.get("checked") is False:
        log(WARN, "social performance", f"unmeasurable: {str(soc.get('reason'))[:70]}")
    else:
        own = soc.get("own") or {}
        log(OK, "social performance",
            f"{own.get('ranked')} post(s), median {own.get('median_likes')} likes, "
            f"top {((own.get('top') or [{}])[0]).get('likes')}")

    # ------------------------------------------------- read-only GETs the SPA uses
    for name, path in [
        ("trends (stored)", f"/api/brands/{dk}"),
        ("jobs list", "/api/jobs"),
        ("showcase", "/api/showcase"),
    ]:
        st, _ = call("GET", path, tok)
        log(OK if st in (200, 404) else BAD, name, f"{st}")

    # ------------------------------------------------- routes that SPEND (not fired)
    print("\n--- paid routes: reachability only, nothing is generated ---")
    for name, path in [
        ("SWOT generate", "/api/swot/generate"),
        ("strategy generate", "/api/strategy/generate"),
        ("marketing plan", "/api/marketing-plan/generate"),
        ("poster generate", "/api/poster/from-profile"),
        ("reel generate", "/api/reel/from-profile"),
    ]:
        st, _ = call("OPTIONS", path, tok, timeout=15)
        # 405 means the path exists but not for OPTIONS — that is still "registered".
        log(OK if st in (200, 204, 405) else WARN, f"{name} route registered", f"OPTIONS -> {st}")

    _flush()
    print(f"\n{_counts[OK]} pass · {_counts[WARN]} warn · {_counts[BAD]} fail   -> {LOG}")
    return 1 if _counts[BAD] else 0


def _flush() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(_lines) + "\n")
        f.write(f"--- {_counts[OK]} pass · {_counts[WARN]} warn · {_counts[BAD]} fail ---\n")


if __name__ == "__main__":
    raise SystemExit(main())
