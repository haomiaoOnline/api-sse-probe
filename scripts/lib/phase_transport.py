"""Stage-aware, deadline-bounded HTTPS POST transport (stdlib only).

Never follows redirects, never disables TLS validation. Distinct budgets for
DNS/TCP/TLS setup, response headers and streamed SSE. The watchdog aborts
blocked reads at the *absolute* total deadline, including slow trickle reads.
An injected urllib opener remains a test-only/compatibility route.
"""
import contextlib
import http.client
import os
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass


class PhaseTimeout(TimeoutError):
    def __init__(self, phase, kind):
        self.phase = phase
        self.kind = kind
        super().__init__(kind)


class PhaseTransportError(OSError):
    def __init__(self, phase, kind):
        self.phase = phase
        self.kind = kind
        super().__init__(kind)


class PhaseTracker:
    def __init__(self, started, total):
        self.started = started
        self.deadline = started + total
        self.phase = "configuration"
        self.phase_since = started
        self.timings = {}
        self.headers_received = False
        self.response_bytes = 0

    def enter(self, name):
        now = time.monotonic()
        if self.phase and self.phase_since:
            key = self.phase + "_ms"
            self.timings[key] = self.timings.get(key, 0) + int(
                (now-self.phase_since)*1000)
        self.phase = name
        self.phase_since = now
        if now >= self.deadline:
            raise PhaseTimeout(name, "total_deadline_exceeded")

    def remaining(self, phase_budget):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise PhaseTimeout(self.phase, "total_deadline_exceeded")
        return min(float(phase_budget), seconds)

    def finalize(self):
        if self.phase and self.phase_since:
            key=self.phase+"_ms"
            self.timings[key] = self.timings.get(key,0)+int(
                max(0,time.monotonic()-self.phase_since)*1000)
            self.phase_since = None


def _resolve(host, port, tracker, connect_limit):
    tracker.enter("dns")
    # Native socket.getaddrinfo can be indefinitely blocking on some systems.
    # A bounded daemon lookup prevents the business call from waiting forever;
    # there is no supported cancellation of a stuck libc resolver thread.
    box = {}
    done = threading.Event()

    def resolve():
        try:
            box["addresses"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=resolve, daemon=True, name="sse-dns-resolve")
    thread.start()
    if not done.wait(tracker.remaining(connect_limit)):
        raise PhaseTimeout("dns", "dns_timeout")
    if "error" in box:
        raise PhaseTransportError("dns", type(box["error"]).__name__) from box["error"]
    return box["addresses"]


def _tcp(host, port, addresses, tracker, connect_limit):
    tracker.enter("tcp_connect")
    timeout_end = tracker.phase_since + connect_limit
    last = None
    for family, socktype, proto, _, addr in addresses:
        left = min(tracker.deadline, timeout_end) - time.monotonic()
        if left <= 0:
            raise PhaseTimeout("tcp_connect", "tcp_connect_timeout")
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(left)
            sock.connect(addr)
            return sock
        except (socket.timeout, TimeoutError) as exc:
            sock.close()
            raise PhaseTimeout("tcp_connect", "tcp_connect_timeout") from exc
        except OSError as exc:
            sock.close()
            last = exc
    raise PhaseTransportError("tcp_connect", type(last).__name__ if last else
                              "connect_failed") from last


@contextlib.contextmanager
def _interrupt_at(sock, deadline):
    """Abort one blocking phase even if a peer trickles bytes indefinitely."""
    fired = threading.Event()

    def abort():
        fired.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseTimeout("deadline", "total_deadline_exceeded")
    timer = threading.Timer(remaining, abort)
    timer.daemon = True
    timer.start()
    try:
        yield fired
    finally:
        timer.cancel()


class StagedResponse:
    def __init__(self, response, connection, tracker, idle):
        self._response = response
        self._conn = connection
        self._tracker = tracker
        self._idle = idle
        self.status = response.status
        self.headers = response.headers
        self.fp = response.fp
        self._sock = connection.sock or getattr(
            getattr(response.fp,"raw",None),"_sock",None)
        self._deadline_timer = None
        # A SINGLE watchdog per response, not a new thread per SSE chunk.
        # Guarantees a hard total wall-clock limit even for continual trickle.
        if self._sock is not None:
            def abort():
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            remaining = max(0.001,tracker.deadline-time.monotonic())
            self._deadline_timer=threading.Timer(remaining,abort)
            self._deadline_timer.daemon=True
            self._deadline_timer.start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._deadline_timer is not None:
            self._deadline_timer.cancel()
        try:
            self._response.close()
        finally:
            self._conn.close()

    def _read(self, method, n):
        tracker = self._tracker
        tracker.enter("sse_read")
        timeout = tracker.remaining(self._idle)
        sock = self._sock
        if sock is not None:
            sock.settimeout(timeout)
        try:
            data = method(n)
        except (socket.timeout, TimeoutError) as exc:
            kind = ("total_deadline_exceeded" if time.monotonic() >= tracker.deadline
                    else "stream_idle_timeout")
            raise PhaseTimeout("sse_read", kind) from exc
        except (ConnectionError, OSError) as exc:
            if time.monotonic() >= tracker.deadline:
                raise PhaseTimeout("sse_read", "total_deadline_exceeded") from exc
            raise
        if time.monotonic() >= tracker.deadline:
            raise PhaseTimeout("sse_read", "total_deadline_exceeded")
        tracker.response_bytes += len(data)
        return data

    def read1(self, n):
        reader=getattr(self._response,"read1",None)
        if not callable(reader):
            reader=self._response.readline
        return self._read(reader,n)

    def readline(self, n):
        return self._read(self._response.readline,n)


def open_staged(req, tracker, connect_timeout, response_header_timeout, idle_timeout):
    """Return a response context manager. Caller must not pass redirects."""
    parts = urlsplit(req.full_url)
    if parts.username or parts.password or parts.fragment:
        raise ValueError("invalid_api_url")
    if parts.scheme not in ("https","http"):
        raise ValueError("unsupported_api_scheme")
    if parts.scheme == "http" and not (
            os.environ.get("API_SSE_PROBE_ALLOW_HTTP_LOCALHOST")=="1" and
            parts.hostname in ("127.0.0.1","localhost")):
        raise ValueError("http_forbidden")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme=="https" else 80)
    # Direct transport is deliberate. Never silently bypass configured HTTPS
    # proxy, since that could violate an organization's network policy.
    if parts.scheme=="https" and getproxies().get("https") and not proxy_bypass(host):
        raise ValueError("https_proxy_requires_explicit_adapter")

    addresses = _resolve(host,port,tracker,connect_timeout)
    sock = _tcp(host,port,addresses,tracker,connect_timeout)
    conn = None
    try:
        if parts.scheme=="https":
            tracker.enter("tls_handshake")
            context=ssl.create_default_context()
            sock.settimeout(tracker.remaining(connect_timeout))
            tls = context.wrap_socket(sock, server_hostname=host,
                                      do_handshake_on_connect=False)
            sock = tls
            with _interrupt_at(sock, min(tracker.deadline,
                                         tracker.phase_since+connect_timeout)):
                try:
                    sock.do_handshake()
                except (socket.timeout, TimeoutError) as exc:
                    raise PhaseTimeout("tls_handshake", "tls_handshake_timeout") from exc
        conn = http.client.HTTPSConnection(host,port) if parts.scheme=="https" else http.client.HTTPConnection(host,port)
        conn.sock=sock
        tracker.enter("request_send")
        sock.settimeout(tracker.remaining(connect_timeout))
        path = parts.path or "/"
        if parts.query:
            path += "?"+parts.query
        headers = dict(req.header_items())
        headers.setdefault("Content-Type","application/json; charset=utf-8")
        with _interrupt_at(sock, min(tracker.deadline,tracker.phase_since+connect_timeout)):
            try:
                conn.request("POST",path,body=req.data,headers=headers)
            except (socket.timeout, TimeoutError) as exc:
                raise PhaseTimeout("request_send", "request_send_timeout") from exc
        tracker.enter("response_headers")
        timeout = tracker.remaining(response_header_timeout)
        sock.settimeout(timeout)
        with _interrupt_at(sock, min(tracker.deadline,
                                      tracker.phase_since+response_header_timeout)):
            try:
                response = conn.getresponse()
            except (socket.timeout,TimeoutError) as exc:
                kind = ("total_deadline_exceeded" if time.monotonic() >= tracker.deadline
                        else "response_header_timeout")
                raise PhaseTimeout("response_headers",kind) from exc
            except (OSError, http.client.HTTPException) as exc:
                if time.monotonic() >= min(tracker.deadline,
                                            tracker.phase_since+response_header_timeout):
                    raise PhaseTimeout("response_headers","response_header_timeout") from exc
                raise
        if time.monotonic() >= tracker.deadline:
            raise PhaseTimeout("response_headers","total_deadline_exceeded")
        tracker.headers_received=True
        tracker.enter("sse_read")
        return StagedResponse(response,conn,tracker,idle_timeout)
    except Exception:
        if conn is not None:
            conn.close()
        else:
            sock.close()
        raise