"""Direct, address-pinned HTTP for Campaign publisher captures."""
from contextlib import ExitStack, contextmanager
from http.client import HTTPException, HTTPResponse
from io import BufferedReader, RawIOBase
from ipaddress import IPv4Address, ip_address, ip_network
from queue import Empty, SimpleQueue
import re
import socket
import ssl
from threading import BoundedSemaphore, Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

import idna

_PORTS = {"http": 80, "https": 443}
_DNS_SLOTS = BoundedSemaphore(4)
_FRAMING_ALLOWANCE = 65_536
_IPV4_PROTOCOLS = ip_network("192.0.0.0/24")
_PUBLIC_PROTOCOL_ENDPOINTS = {IPv4Address("192.0.0.9"), IPv4Address("192.0.0.10")}


def _target(url):
    # Parse once before network work. Reject delimiters that clients or
    # publisher servers might interpret as a different authority.
    if re.search(r"[\\\x00-\x20\x7f]", url):
        raise ValueError("invalid source URL")
    parts = urlsplit(url)
    default_port = _PORTS.get(parts.scheme)
    if (default_port is None or not parts.hostname or "#" in url
            or parts.username is not None or parts.password is not None
            or parts.port not in (None, default_port)):
        raise ValueError("invalid source URL")
    host = parts.hostname
    if "[" in parts.netloc:
        if ":" not in host or "%" in host:
            raise ValueError("invalid IPv6 source hostname")
        host = str(ip_address(host))
        authority = f"[{host}]"
    else:
        if not host.isascii():
            host = idna.encode(host, uts46=True).decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            raise ValueError("invalid source hostname")
        authority = host
    if parts.port is not None:
        authority += f":{parts.port}"
    return parts, host, authority, default_port


def valid_source_url(url):
    try:
        _target(url)
    except ValueError:
        return False
    return True


def _timeout(deadline, idle):
    left = deadline - monotonic()
    if left <= 0:
        raise TimeoutError("source deadline expired")
    return min(left, idle)


def _resolve(host, port, deadline):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise TimeoutError("source DNS capacity exhausted")
    replies = SimpleQueue()

    def lookup():
        try:
            replies.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP))
        except OSError as exc:
            replies.put(exc)
        finally:
            _DNS_SLOTS.release()

    # libc resolution cannot be cancelled. Time out the caller and retain
    # the slot until its resolver exits, so blocked DNS cannot grow threads.
    try:
        Thread(target=lookup, daemon=True, name="campaign-dns").start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise
    try:
        result = replies.get(timeout=_timeout(deadline, float("inf")))
    except Empty:
        raise TimeoutError("source DNS deadline expired") from None
    if isinstance(result, OSError):
        raise result
    return result


def _public_address(family, address, port):
    ip = ip_address(address[0])
    if family == socket.AF_INET6 and ip.version == 6:
        if getattr(ip, "scope_id", None) or address[3] != 0 or ip.is_site_local or ip.sixtofour or ip.teredo:
            raise ValueError("refused special-purpose IPv6 source")
        pinned = (str(ip), port, 0, 0)
        ip = ip.ipv4_mapped or ip
    elif family == socket.AF_INET and ip.version == 4:
        pinned = (str(ip), port)
    else:
        raise ValueError("unsupported source address family")
    # Python 3.12's address database marks most of this block global.
    special_protocol = ip in _IPV4_PROTOCOLS and ip not in _PUBLIC_PROTOCOL_ENDPOINTS
    if not ip.is_global or ip.is_reserved or ip.is_multicast or special_protocol:
        raise ValueError("refused non-public source address")
    return family, pinned


def _connect(addresses, deadline, idle):
    last_error = OSError("no public address")
    for family, address in addresses:
        timeout = _timeout(deadline, idle)
        try:
            sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        except OSError as exc:
            last_error = exc
            continue
        try:
            # The host component is numeric and came from the validated
            # answer set. The original hostname never reaches connect().
            sock.settimeout(timeout)
            sock.connect(address)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
    raise last_error


class _ResponseInput(RawIOBase):
    def __init__(self, sock, deadline, idle, wire_limit):
        self.sock = sock
        self.deadline = deadline
        self.idle = idle
        self.left = wire_limit

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_timeout(self.deadline, self.idle))
        size = self.sock.recv_into(memoryview(buffer)[:self.left + 1])
        self.left -= size
        if self.left < 0:
            raise OSError("source response framing limit exceeded")
        return size

    def makefile(self, mode):
        return BufferedReader(self)


@contextmanager
def open_campaign_url(url, *, deadline, socket_timeout, max_bytes, user_agent):
    """One direct GET. Report redirects as HTTP errors for caller policy."""
    with ExitStack() as connections:
        try:
            parts, host, authority, port = _target(url)
            infos = _resolve(host, port, deadline)
            addresses = dict.fromkeys(_public_address(family, addr, port) for family, _, _, _, addr in infos)
            if not re.fullmatch(r"[ -~]+", user_agent):
                raise ValueError("invalid source User-Agent")
            path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=")
            if parts.query:
                path += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=?")
            request = (f"GET {path} HTTP/1.1\r\nHost: {authority}\r\n"
                       f"User-Agent: {user_agent}\r\nAccept: text/html\r\n"
                       "Accept-Encoding: identity\r\nConnection: close\r\n\r\n").encode("ascii")
            sock = connections.enter_context(_connect(addresses, deadline, socket_timeout))
            if parts.scheme == "https":
                sock.settimeout(_timeout(deadline, socket_timeout))
                tls = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                sock = connections.enter_context(tls)
            sock.settimeout(_timeout(deadline, socket_timeout))
            sock.sendall(request)
            reader = _ResponseInput(sock, deadline, socket_timeout, max_bytes + _FRAMING_ALLOWANCE)
            response = connections.enter_context(HTTPResponse(reader, method="GET"))
            response.begin()
        except (OSError, HTTPException, ValueError) as exc:
            raise URLError(exc) from exc
        if not 200 <= response.status < 300:
            raise HTTPError(url, response.status, response.reason, response.headers, None)
        yield response
