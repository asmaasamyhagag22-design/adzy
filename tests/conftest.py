"""Shared test fixtures.

U7 telemetry writes runs/<run_id>/telemetry.jsonl per pipeline run; tests must not
pollute the repo root, so every test gets an isolated TELEMETRY_ROOT under tmp.
"""
import pytest


@pytest.fixture(autouse=True)
def _telemetry_tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_ROOT", str(tmp_path / "telemetry_runs"))
    monkeypatch.delenv("TELEMETRY_RUN_ID", raising=False)


@pytest.fixture(autouse=True)
def _vertex_spend_tmp_ledger(tmp_path, monkeypatch):
    """Isolate the Vertex spend ledger + a generous cap so no test writes to the real
    runs/vertex_spend.jsonl or gets a render refused. Tests that exercise the guard itself pass
    their own ledger/cap and are unaffected."""
    import vertex_spend
    monkeypatch.setattr(vertex_spend, "DEFAULT_LEDGER", tmp_path / "vertex_spend.jsonl")
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "100000")
    # ...and the PER-RUN Veo budget (C1). Without this the shipped $8 default would silently bind
    # for any test that drives a real guard_video. Tests that exercise the ceiling set their own.
    monkeypatch.setenv("VEO_MAX_USD_PER_RUN", "100000")


@pytest.fixture(autouse=True)
def _no_spend_in_tests(monkeypatch):
    """No test may spend real money. Belt to the network guard's braces below.

    Two entrypoints bill on every run — `python -m benchmark.grade_u1` (one live Gemini call per
    labelled URL) and `python -m trends` (paid Serper). Their `main()` calls
    `spend_tripwire.refuse_if_no_spend()`, which already trips on pytest's own
    PYTEST_CURRENT_TEST; setting BASEERA_NO_SPEND=1 here makes the rule EXPLICIT at the place a
    reader looks for it, and keeps holding if pytest ever stops exporting that variable.
    History: 2026-07-15, ~72 billed Gemini calls because a docstring was trusted over a control.
    """
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch):
    """Hermeticity + spend guard (PD-9 offline spirit). No test does real network I/O, so an
    UNMOCKED external call degrades instantly instead of hanging (or spending).

    Why this exists: render_creative_reel's seed-gen path reaches a real Vertex image call
    (poster.oneshot.generate_oneshot_poster over google-genai/httpx). On a machine with ADC that
    call would (a) intermittently hang the whole suite for minutes — an unmocked read with no
    timeout — and (b) leak real spend. This guard makes it fail fast through the existing
    try/except, so the reel tests are deterministic and no test ever spends.

    Two seams, so the SSRF validator (scraper.url_utils.is_safe_public_url, which resolves the
    target host to prove it is public) keeps working under the guard:
      * getaddrinfo — a HOSTNAME resolves to a fixed GLOBAL IP with no real DNS (so is_safe_public_url
        passes for e.g. example.com); an IP LITERAL passes through unchanged (so 169.254.169.254 /
        127.0.0.1 stay correctly rejected). Note: TEST-NET ranges are is_private in Python, so a
        globally-routable address is required here.
      * connect — the actual socket connection is refused for every non-loopback address, so nothing
        is ever really reached (the resolved IP above is never contacted).

    Loopback stays fully open (dashboard/api server tests bind 127.0.0.1). Tests that mock their
    provider never hit these seams. Set PYTEST_ALLOW_NETWORK=1 for a live integration run.
    """
    import errno
    import ipaddress
    import os
    import socket
    if os.environ.get("PYTEST_ALLOW_NETWORK") == "1":
        return
    _real_getaddrinfo = socket.getaddrinfo
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex
    _PUBLIC_IP = "93.184.216.34"  # globally-routable; hostname resolution succeeds without real DNS

    def _is_ip_literal(h: str) -> bool:
        try:
            ipaddress.ip_address(h)
            return True
        except ValueError:
            return False

    def _is_local_host(h: str) -> bool:
        return h in ("localhost", "") or h == "::1" or h.startswith("127.")

    def _is_local_addr(address) -> bool:
        ip = str(address[0] if isinstance(address, (tuple, list)) else address)
        return ip in ("localhost", "0.0.0.0", "::1") or ip.startswith("127.")

    def _guarded_getaddrinfo(host, *args, **kwargs):
        h = str(host)
        if _is_local_host(h) or _is_ip_literal(h):
            return _real_getaddrinfo(host, *args, **kwargs)  # loopback + literals: no DNS network
        port = args[0] if args else (kwargs.get("port") or 0)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_PUBLIC_IP, port or 0))]

    def _guarded_connect(self, address, *a, **k):
        if _is_local_addr(address):
            return _real_connect(self, address, *a, **k)
        raise ConnectionRefusedError(f"[test-guard] external connect blocked: {address!r}. "
                                     "Mock the provider, or set PYTEST_ALLOW_NETWORK=1 for a live run.")

    def _guarded_connect_ex(self, address, *a, **k):
        if _is_local_addr(address):
            return _real_connect_ex(self, address, *a, **k)
        return errno.ECONNREFUSED

    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex, raising=False)


@pytest.fixture(autouse=True)
def _webapp_signup_is_open_in_tests(monkeypatch):
    """The suite exercises a deployment whose signup is deliberately OPEN.

    `api.auth.guard_public_signup` closes account creation to non-loopback callers unless
    `BASEERA_ALLOW_PUBLIC_SIGNUP=1`, because every account can trigger paid generation. Starlette's
    TestClient reports its host as "testclient", not 127.0.0.1, so every web-app test that signs a
    user up would otherwise 403.

    This declares the fixture's CONFIGURATION rather than disabling the guard: the guard's own
    behaviour — closed to remote by default, open only when the operator sets the flag, and a
    request with no client info treated as remote — is asserted directly in
    tests/test_webapp_spend_gate.py, which does not use this fixture's value.
    """
    monkeypatch.setenv("BASEERA_ALLOW_PUBLIC_SIGNUP", "1")
