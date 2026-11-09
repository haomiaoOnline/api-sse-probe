"""Real localhost HTTP, staged timing and deadline regression tests.

No external business API calls, no Feishu. Test server reads the whole POST,
then delays response headers or SSE chunks to reproduce historic ambiguity.
"""
import copy
import io
import json
import os
import socket
import threading
import time
import unittest
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch

from test_probe import CASE, API, SUCCESS, response_lines
import probe

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self,*args):pass

    def do_POST(self):
        self.server.received.append(self.path)
        size=int(self.headers.get("Content-Length","0"))
        self.rfile.read(size)
        if self.path=="/wait-header":
            time.sleep(0.36)
        if self.path=="/trickle-header":
            # send HTTP status line slowly: a socket timeout alone cannot
            # enforce a whole-header deadline under trickle traffic
            for c in b"HTTP/1.1 200 OK\r\n":
                try:
                    self.wfile.write(bytes([c]));self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):return
                time.sleep(0.04)
            self.wfile.write(b"Content-Type: text/event-stream\r\n\r\n")
        else:
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream")
            self.send_header("Connection","close")
            self.end_headers()
        try:
            if self.path=="/wait-stream":
                time.sleep(0.36)
            if self.path=="/partial-stream":
                self.wfile.write(b":pulse\n\ndata: {\"incomplete\":")
                self.wfile.flush()
                time.sleep(0.65)
                return
            if self.path=="/trickle-stream":
                for chunk in response_lines(SUCCESS):
                    for off in range(0,len(chunk),4):
                        self.wfile.write(chunk[off:off+4]);self.wfile.flush()
                        time.sleep(0.032)
                return
            for chunk in response_lines(SUCCESS):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

class PhaseTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),_Handler)
        cls.server.received=[]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, route, **timeout_overrides):
        cfg=copy.deepcopy(API)
        cfg["url"]="http://127.0.0.1:{}{}".format(self.server.server_port,route)
        cfg.update({"connect_timeout_seconds":0.12,
                    "response_header_timeout_seconds":0.12,
                    "idle_timeout_seconds":0.12,
                    "total_timeout_seconds":1.2})
        cfg.update(timeout_overrides)
        with patch.dict(os.environ,{"API_SSE_PROBE_ALLOW_HTTP_LOCALHOST":"1"}):
            return probe.run_case(CASE,cfg)

    def test_delayed_headers_reproduce_pre_response_timeout_even_post_received(self):
        before=len(self.server.received)
        result=self.request("/wait-header")
        self.assertEqual(result["reason"],"timeout")
        self.assertEqual(result["error_phase"],"response_headers")
        self.assertEqual(result["timeout_kind"],"response_header_timeout")
        self.assertIsNone(result["http_status"])
        self.assertFalse(result["http_headers_received"])
        self.assertEqual(result["response_bytes_received"],0)
        self.assertEqual(result["json_frame_count"],0)
        self.assertGreaterEqual(len(self.server.received),before+1)
        self.assertIn("response_headers_ms",result["stage_timings_ms"])
        self.assertIn("PhaseTimeout",result["exception_chain"])
        self.assertNotIn("redacted-test",json.dumps(result))
        ok=self.request("/wait-header",response_header_timeout_seconds=0.7,
                        idle_timeout_seconds=0.7)
        self.assertEqual(ok["status"],"PASS")
        self.assertTrue(ok["http_headers_received"])
        self.assertGreater(ok["response_bytes_received"],0)

    def test_delayed_sse_body_uses_idle_timeout_not_connect_timeout(self):
        ok=self.request("/wait-stream",idle_timeout_seconds=0.6)
        self.assertEqual(ok["status"],"PASS")
        self.assertTrue(ok["done_received"])
        self.assertGreater(ok["response_bytes_received"],0)
        failed=self.request("/wait-stream",idle_timeout_seconds=0.1)
        self.assertEqual(failed["reason"],"timeout")
        self.assertEqual(failed["error_phase"],"sse_read")
        self.assertTrue(failed["http_headers_received"])

    def test_total_deadline_clamps_blocked_read_not_just_before_read(self):
        began=time.monotonic()
        result=self.request("/wait-stream",idle_timeout_seconds=1,
                            response_header_timeout_seconds=1,
                            total_timeout_seconds=0.2)
        elapsed=time.monotonic()-began
        self.assertEqual(result["reason"],"timeout")
        self.assertEqual(result["error_phase"],"sse_read")
        self.assertEqual(result["timeout_kind"],"total_deadline_exceeded")
        self.assertLess(elapsed,0.34)
        self.assertTrue(result["http_headers_received"])

    def test_trickling_headers_cannot_exceed_response_header_deadline(self):
        began=time.monotonic()
        r=self.request("/trickle-header",response_header_timeout_seconds=0.18,
                       idle_timeout_seconds=0.6,total_timeout_seconds=1.5)
        self.assertEqual(r["reason"],"timeout")
        self.assertEqual(r["error_phase"],"response_headers")
        self.assertLess(time.monotonic()-began,0.36)

    def test_partial_stream_bytes_preserved_and_labeled(self):
        captured=io.BytesIO()
        cfg=copy.deepcopy(API)
        cfg.update({"url":"http://127.0.0.1:{}/partial-stream".format(self.server.server_port),
                    "connect_timeout_seconds":0.15,
                    "response_header_timeout_seconds":0.3,
                    "idle_timeout_seconds":0.14,
                    "total_timeout_seconds":1})
        with patch.dict(os.environ,{"API_SSE_PROBE_ALLOW_HTTP_LOCALHOST":"1"}):
            r=probe.run_case(CASE,cfg,raw_capture=captured)
        self.assertEqual(r["error_phase"],"sse_read")
        self.assertEqual(r["reason"],"timeout")
        self.assertTrue(r["http_headers_received"])
        self.assertEqual(r["response_bytes_received"],len(captured.getvalue()))
        self.assertIn(b"incomplete",captured.getvalue())
        self.assertFalse(r["done_received"])

    def test_tls_handshake_timeout_has_distinct_phase_and_cause_type(self):
        # A plain local TCP peer intentionally holds the TLS ClientHello
        # without handshake response. No certificates or external network.
        server=socket.socket()
        server.bind(("127.0.0.1",0))
        server.listen(1)
        port=server.getsockname()[1]
        def stalled_peer():
            try:
                accepted,_=server.accept()
                with accepted:
                    time.sleep(0.5)
            except OSError:
                pass
            finally:
                server.close()
        thread=threading.Thread(target=stalled_peer,daemon=True)
        thread.start()
        api=copy.deepcopy(API)
        api.update({"url":"https://127.0.0.1:{}/probe".format(port),
                    "connect_timeout_seconds":0.14,
                    "response_header_timeout_seconds":0.3,
                    "idle_timeout_seconds":0.3,
                    "total_timeout_seconds":1})
        started=time.monotonic()
        result=probe.run_case(CASE,api)
        self.assertEqual(result["error_phase"],"tls_handshake")
        self.assertIn(result["reason"],("timeout","tls_error"))
        self.assertFalse(result["http_headers_received"])
        self.assertEqual(result["response_bytes_received"],0)
        self.assertLess(time.monotonic()-started,0.42)
        thread.join(timeout=1)

    def test_total_deadline_even_under_stream_trickle(self):
        began=time.monotonic()
        r=self.request("/trickle-stream",idle_timeout_seconds=0.35,
                       total_timeout_seconds=0.28)
        self.assertEqual(r["reason"],"timeout")
        self.assertEqual(r["timeout_kind"],"total_deadline_exceeded")
        self.assertEqual(r["error_phase"],"sse_read")
        self.assertLess(time.monotonic()-began,0.43)
        self.assertGreater(r["response_bytes_received"],0)


if __name__ == "__main__":
    unittest.main()