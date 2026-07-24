# -*- coding: utf-8 -*-
"""Dynamic pages must never be cached.

Owner (2026-07-20): "الميزات مش بتظهر" — the served HTML already contained every new feature, so the
stale copy was her browser's (and, that time, a server process older than the code). `no-store` on
every dynamic response is what stops the first cause for good; this test keeps it that way, since a
single `_send` regression would silently re-introduce a whole class of "it didn't ship" reports.

Hermetic: the handler's header emission is exercised against a fake socket — no port is bound."""
from __future__ import annotations

import io

from dashboard.server import _Handler


class _FakeSock(io.BytesIO):
    def makefile(self, *a, **kw):
        return self

    def sendall(self, b):
        self.write(b)


def _headers_for(body: bytes = b"<html/>", ctype: str = "text/html; charset=utf-8") -> str:
    h = _Handler.__new__(_Handler)                  # no socket setup: we only drive _send
    h.wfile = io.BytesIO()
    sent: list[str] = []
    h.send_response = lambda code: sent.append(f"status:{code}")
    h.send_header = lambda k, v: sent.append(f"{k}: {v}")
    h.end_headers = lambda: None
    h._send(200, body, ctype)
    return "\n".join(sent)


def test_dynamic_html_is_never_cached():
    out = _headers_for()
    assert "Cache-Control: no-store" in out
    assert "Content-Type: text/html; charset=utf-8" in out


def test_every_response_type_carries_the_header():
    # JSON endpoints (owner inputs, HITL refine) must not be cached either — a cached refine
    # response would show her the OLD prompt after she edited it.
    assert "Cache-Control: no-store" in _headers_for(b"{}", "application/json; charset=utf-8")
