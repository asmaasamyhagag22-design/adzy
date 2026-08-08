# -*- coding: utf-8 -*-
"""Every control in the dashboard -> its handler -> the route it calls -> does that route exist.

WHY. `frontend/index.html` is one 5,300-line file with no JS test runner, so every existing
"the dashboard works" test is a regex over that file. A regex proves a BUTTON IS PRESENT. It
cannot prove the button's handler exists, that the handler calls a route the server registers,
or that a route the server registers is ever called by anything.

This reads the shipped HTML and the shipped OpenAPI schema and reports four things:

  DEAD BUTTON    an onclick naming a function that is not defined
  DEAD CALL      an api(...) path with no matching route on the server
  ORPHAN ROUTE   a registered route nothing in the dashboard ever calls
  UNREACHED FN   a handler defined but wired to no control

    python -m uvicorn api.main:app --port 8000
    python info/check_wiring.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTML = REPO / "frontend" / "index.html"
BASE = "http://localhost:8000"
LOG = REPO / "logs" / "dashboard_check.log"

# Routes a browser never calls directly, or that exist for machines / docs.
_EXPECTED_ORPHANS = {
    "/api/health", "/api/auth/signup", "/api/auth/login", "/api/auth/me",
    "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect",
}


def _routes() -> set[str]:
    with urllib.request.urlopen(BASE + "/openapi.json", timeout=30) as r:
        return set(json.load(r).get("paths", {}))


def _norm(p: str) -> str:
    """`/api/brands/${dk}/competitors` -> `/api/brands/{}/competitors` so both sides compare.

    A path that ENDS in `/` keeps a trailing `{}`: in this file a route parameter is written either
    as a template hole (`${dk}`) or as string concatenation (`'/api/jobs/' + jobId`), and the
    capture stops at the closing quote. Without this the concatenated form normalised to
    `/api/jobs` and was reported as a DEAD CALL against a route that exists and is called
    correctly seven times — a checker that invents failures is worse than no checker.
    """
    p = p.split("?", 1)[0]
    trailing = p.endswith("/")
    p = p.rstrip("/")
    p = re.sub(r"\$\{[^}]*\}", "{}", p)
    p = re.sub(r"\{[^}]*\}", "{}", p)
    if trailing:
        p += "/{}"
    return p or "/"


def main() -> int:
    if not HTML.is_file():
        print(f"no dashboard at {HTML}")
        return 1
    html = HTML.read_text(encoding="utf-8")
    lines: list[str] = ["\n" + "=" * 78, "DASHBOARD WIRING", "=" * 78]

    def say(s: str) -> None:
        lines.append(s)
        print(s, flush=True)

    # ---- what the page defines and what it wires ------------------------------
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", html))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", html))
    wired = set(re.findall(r"on(?:click|change|input|submit)\s*=\s*[\"']([A-Za-z_$][\w$]*)\s*\(",
                           html))
    listeners = set(re.findall(r"addEventListener\(\s*['\"][a-z]+['\"]\s*,\s*([A-Za-z_$][\w$]*)",
                               html))

    dead_buttons = sorted(n for n in wired if n not in defined)
    say(f"controls wired to a handler : {len(wired)}")
    say(f"handlers defined            : {len(defined)}")
    if dead_buttons:
        for n in dead_buttons:
            say(f"  [FAIL] DEAD BUTTON  onclick={n}() — no such function")
    else:
        say("  [PASS] every wired control resolves to a defined handler")

    # ---- the paths the page calls ---------------------------------------------
    called_raw = set(re.findall(r"api\(\s*['\"][A-Z]+['\"]\s*,\s*[`'\"]([^`'\"]+)", html))
    called_raw |= set(re.findall(r"fetch\(\s*[`'\"](/api/[^`'\"]+)", html))
    called = {_norm(p) for p in called_raw if p.startswith("/api")}

    try:
        registered = {_norm(p) for p in _routes()}
        server_up = True
    except Exception as e:  # noqa: BLE001
        say(f"  [WARN] server not reachable ({type(e).__name__}) — route check skipped")
        registered, server_up = set(), False

    if server_up:
        dead_calls = sorted(c for c in called if c not in registered)
        say(f"\ndistinct API paths the page calls: {len(called)}")
        if dead_calls:
            for c in dead_calls:
                say(f"  [FAIL] DEAD CALL    {c} — the page calls it, the server has no such route")
        else:
            say("  [PASS] every path the dashboard calls exists on the server")

        # An orphan is decided on the RAW TEXT, not on the normalised call set: the page builds
        # paths by concatenation as often as by interpolation, and matching the literal prefix is
        # the only form that survives both. `_norm` is right for comparing one path to one route;
        # it is the wrong instrument for "does this string appear anywhere in the file".
        def _reached(route: str) -> bool:
            prefix = route.split("{")[0].rstrip("/")
            return bool(prefix) and prefix in html

        orphans = sorted(r for r in registered
                         if not _reached(r) and r not in {_norm(x) for x in _EXPECTED_ORPHANS}
                         and r.startswith("/api"))
        say(f"\nregistered routes: {len(registered)}")
        if orphans:
            say("  [WARN] routes the dashboard never calls (built and unreachable from the UI):")
            for r in orphans:
                say(f"         {r}")
        else:
            say("  [PASS] no orphan routes")

    # ---- handlers nothing points at -------------------------------------------
    # Only `function name(...)` declarations. The `const x = (` pattern I first used also matched
    # every arrow-function parameter in the file (`v => …`, `p => …`), which reported 19 "unreached
    # handlers" that were all one-letter locals. A checker's own false positives cost more than the
    # thing it checks for.
    declared = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", html))
    unreached = sorted(
        n for n in declared
        if n not in wired and n not in listeners
        and len(re.findall(rf"\b{re.escape(n)}\s*\(", html)) <= 1
        and not n.startswith("_"))
    if unreached:
        say(f"\n  [WARN] {len(unreached)} function(s) declared and never called:")
        for n in unreached[:20]:
            say(f"         {n}()")
    else:
        say("\n  [PASS] every declared function is called somewhere")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    fails = len(dead_buttons) + (len(dead_calls) if server_up else 0)
    print(f"\n-> {LOG}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
