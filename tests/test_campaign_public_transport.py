"""Exercise the normal Campaign loader against a controlled HTTP peer."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from gzip import compress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
from threading import Event, Thread
from time import monotonic, sleep
from urllib.error import HTTPError, URLError

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

import pytest

from strategy.campaign_editions import capture_edition_source
from strategy.campaign_package import load_package_page
from strategy.campaign_research import ResearchGap, load_public_page


PUBLIC = "93.184.216.34"
HOST = "publisher.example"


@contextmanager
def publisher(respond=None, *, tls_context=None, ipv6=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Host"), self.headers.get("Accept-Encoding")))
            if respond:
                respond(self)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>Publisher text</p>")

        def log_message(self, *_args):
            pass

    class PublisherServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ipv6 else socket.AF_INET

    with PublisherServer(("::1" if ipv6 else "127.0.0.1", 0), Handler) as server:
        if tls_context:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        worker.start()
        try:
            yield server.server_port, requests
        finally:
            server.shutdown()
            worker.join()


@pytest.fixture(autouse=True)
def no_host_proxy(monkeypatch):
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


def fake_network(monkeypatch, port, answers=None, *, hostname=HOST):
    resolved, dialed = [], []
    real_resolve, real_connect = socket.getaddrinfo, socket.socket.connect

    def resolve(host, service, *args, **kwargs):
        if host != hostname:
            return real_resolve(host, service, *args, **kwargs)
        resolved.append(host)
        addresses = answers(len(resolved)) if answers else [PUBLIC]
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
             "", (ip, service, 0, 0) if ":" in ip else (ip, service))
            for ip in addresses
        ]

    def connect(sock, address):
        dialed.append(address[0])
        return real_connect(sock, ("::1" if sock.family == socket.AF_INET6 else "127.0.0.1", port))

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    return resolved, dialed


def test_campaign_dials_validated_answer_without_resolving_hostname_again(monkeypatch):
    with publisher() as (port, requests):
        resolved, dialed = fake_network(
            monkeypatch, port, lambda count: [PUBLIC if count == 1 else "127.0.0.1"])
        page = load_package_page(f"http://{HOST}/details", 1.0)

    assert page.body == b"<p>Publisher text</p>"
    assert resolved == [HOST]
    assert dialed == [PUBLIC]
    assert requests == [("/details", HOST, "identity")]


def test_campaign_ignores_environment_proxy(monkeypatch):
    with publisher() as (port, requests):
        _, dialed = fake_network(monkeypatch, port)
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{port}")
        load_package_page(f"http://{HOST}/details", 1.0)

    assert dialed == [PUBLIC]
    assert requests == [("/details", HOST, "identity")]


@pytest.mark.parametrize("address", [
    "127.0.0.1", "169.254.169.254", "100.64.0.1", "192.0.0.70",
    "::1", "::ffff:127.0.0.1", "fc00::1", "fe80::1", "fec0::1", "2002:7f00:1::1", "64:ff9b::7f00:1", "64:ff9b:1::a9fe:a9fe",
])
def test_mixed_dns_answers_are_rejected_before_connect(monkeypatch, address):
    _, dialed = fake_network(monkeypatch, 0, lambda count: [PUBLIC, address])
    with pytest.raises(URLError):
        load_package_page(f"http://{HOST}/details", 1.0)
    assert dialed == []


def test_ipv6_address_is_pinned(monkeypatch):
    if not socket.has_ipv6:
        pytest.skip("IPv6 unavailable")
    public_v6 = "2001:4860:4860::8888"
    with publisher(ipv6=True) as (port, requests):
        resolved, dialed = fake_network(monkeypatch, port, lambda count: [public_v6])
        load_package_page(f"http://{HOST}/details", 1.0)
    assert resolved == [HOST]
    assert dialed == [public_v6]
    assert requests == [("/details", HOST, "identity")]


@pytest.fixture
def client_trusted_publisher_tls(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(key, algorithm=None)
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_file, key_file)
    names = []
    server_context.sni_callback = lambda _sock, hostname, _context: names.append(hostname)
    return server_context, names


def test_tls_verifies_publisher_hostname_and_sends_sni(monkeypatch, client_trusted_publisher_tls):
    tls, names = client_trusted_publisher_tls
    with publisher(tls_context=tls) as (port, requests):
        with monkeypatch.context() as network:
            _, dialed = fake_network(network, port)
            page = load_package_page(f"https://{HOST}/details", 1.0)
            assert page.body == b"<p>Publisher text</p>"
            assert dialed == [PUBLIC]
        with monkeypatch.context() as network:
            _, dialed = fake_network(network, port, hostname="mismatch.example")
            with pytest.raises(URLError) as caught:
                load_package_page("https://mismatch.example/details", 1.0)
            assert dialed == [PUBLIC]
    assert isinstance(caught.value.reason, ssl.SSLCertVerificationError)
    assert names == [HOST, "mismatch.example"]
    assert requests == [("/details", HOST, "identity")]


def test_generic_fetch_does_not_follow_redirect(monkeypatch):
    def redirect(handler):
        handler.send_response(302)
        handler.send_header("Location", "http://127.0.0.1/secret")
        handler.end_headers()

    with publisher(redirect) as (port, requests):
        _, dialed = fake_network(monkeypatch, port)
        with pytest.raises(HTTPError) as caught:
            load_package_page(f"http://{HOST}/details", 1.0)
    assert caught.value.code == 302
    assert dialed == [PUBLIC]
    assert requests == [("/details", HOST, "identity")]


def test_edition_redirect_revalidates_changed_dns_before_second_dial(monkeypatch):
    def redirect(handler):
        handler.send_response(302)
        handler.send_header("Location", "/details")
        handler.end_headers()

    with publisher(redirect) as (port, requests):
        resolved, dialed = fake_network(
            monkeypatch, port, lambda count: [PUBLIC if count == 1 else "127.0.0.1"])
        trace = []
        entry = f"http://{HOST}/start"
        result = capture_edition_source(
            entry, entry, {"field": "title", "quote": "Original", "scope": "topic"},
            load_package_page, lambda: datetime.now(timezone.utc), trace, monotonic() + 1.0, "BRAND_ENTRY")
    assert result is None
    assert resolved == [HOST, HOST]
    assert dialed == [PUBLIC]
    assert requests == [("/start", HOST, "identity")]
    assert [step["outcome"] for step in trace] == ["SOURCE_REDIRECT", "SOURCE_UNAVAILABLE"]


@pytest.mark.parametrize(("status", "range_header"), [(206, True), (200, True), (226, False)])
def test_partial_publisher_response_cannot_be_used_as_evidence(monkeypatch, status, range_header):
    fragment = b"<p>Example feature works on every device.</p>"

    def partial(handler):
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html")
        if range_header:
            handler.send_header("Content-Range", f"bytes 0-{len(fragment) - 1}/800")
        handler.send_header("Content-Length", str(len(fragment)))
        handler.end_headers()
        handler.wfile.write(fragment)

    with publisher(partial) as (port, requests):
        fake_network(monkeypatch, port)
        with pytest.raises(ResearchGap, match="SOURCE_PARTIAL_RESPONSE"):
            load_package_page(f"http://{HOST}/details", 1.0)
    assert requests == [("/details", HOST, "identity")]


@pytest.mark.parametrize(("headers", "body", "reason"), [
    ({"Content-Encoding": "gzip"}, compress(b"x" * 1_000_000), "UNSUPPORTED_CONTENT_ENCODING"),
    ({}, b"x" * 100, "PAGE_TOO_LARGE"),
    ({"Content-Length": "100"}, b"short", "PAGE_TOO_LARGE"),
    ({"Content-Length": "22"}, b"short", "SOURCE_TRUNCATED"),
    ({"Transfer-Encoding": "gzip"}, b"short", "UNSUPPORTED_TRANSFER_ENCODING"),
    ({"Transfer-Encoding": "chunked", "Content-Length": "3"}, b"0\r\n\r\n", "INVALID_SOURCE_LENGTH"),
    ({"Transfer-Encoding": "chunked"}, b"20\r\nshort", "SOURCE_TRUNCATED"),
])
def test_response_body_and_framing_are_bounded(monkeypatch, headers, body, reason):
    def reply(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        for key, value in headers.items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(body)

    with publisher(reply) as (port, _):
        fake_network(monkeypatch, port)
        with pytest.raises(ResearchGap, match=reason):
            load_public_page(f"http://{HOST}/page", 1.0, max_bytes=40)


def test_plain_chunked_html_still_works(monkeypatch):
    def reply(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler.wfile.write(b"3\r\nabc\r\n3\r\ndef\r\n0\r\n\r\n")

    with publisher(reply) as (port, _):
        fake_network(monkeypatch, port)
        assert load_public_page(f"http://{HOST}/page", 1.0, max_bytes=6).body == b"abcdef"


@pytest.mark.parametrize(("prefix", "suffix"), [
    (b"", b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n", b"<body>" + b"x" * 60),
])
def test_trickling_headers_and_body_use_absolute_deadline(monkeypatch, prefix, suffix):
    def reply(handler):
        try:
            handler.connection.sendall(prefix)
            for byte in suffix:
                sleep(.025)
                handler.connection.sendall(bytes([byte]))
        except OSError:
            pass

    with publisher(reply) as (port, _):
        fake_network(monkeypatch, port)
        started = monotonic()
        with pytest.raises((TimeoutError, URLError)) as caught:
            load_public_page(f"http://{HOST}/page", .15, socket_timeout=.1)
        elapsed = monotonic() - started
    error = caught.value.reason if isinstance(caught.value, URLError) else caught.value
    assert isinstance(error, TimeoutError)
    assert .10 <= elapsed < .65


def test_dns_resolution_uses_absolute_deadline_and_retains_outstanding_slots(monkeypatch):
    allow_completion = Event()
    outstanding = []

    def resolve(*_args, **_kwargs):
        outstanding.append(True)
        allow_completion.wait(3.0)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    try:
        started = monotonic()
        for _ in range(6):
            with pytest.raises(URLError) as caught:
                load_package_page(f"http://{HOST}/page", .035)
            assert isinstance(caught.value.reason, TimeoutError)
        assert len(outstanding) == 4
        assert monotonic() - started < 1.0
    finally:
        allow_completion.set()


@pytest.mark.parametrize("locations", [
    ("http://[broken/details",),
    ("/details", "/other"),
])
def test_edition_does_not_follow_ambiguous_or_malformed_redirect(monkeypatch, locations):
    def redirect(handler):
        handler.send_response(302)
        for location in locations:
            handler.send_header("Location", location)
        handler.end_headers()

    with publisher(redirect) as (port, requests):
        _, dialed = fake_network(monkeypatch, port)
        trace = []
        entry = f"http://{HOST}/start"
        result = capture_edition_source(
            entry, entry, {"field": "title", "quote": "Original", "scope": "topic"},
            load_package_page, lambda: datetime.now(timezone.utc), trace, monotonic() + 1.0, "BRAND_ENTRY")
    assert result is None
    assert len(trace) == 1
    assert dialed == [PUBLIC]
    assert requests == [("/start", HOST, "identity")]


def test_chunk_framing_cannot_exceed_wire_allowance_for_tiny_body(monkeypatch):
    def reply(handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler.wfile.write((b"1;" + b"x" * 45_000 + b"\r\na\r\n") * 2 + b"0\r\n\r\n")

    with publisher(reply) as (port, _):
        fake_network(monkeypatch, port)
        with pytest.raises(OSError, match="framing limit"):
            load_public_page(f"http://{HOST}/page", 1.0, max_bytes=40)
