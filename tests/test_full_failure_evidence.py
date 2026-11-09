"""Verify full failure evidence is private and not bound by the report excerpt limit."""
import base64
import copy
import io
import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib.failed_sse import LatestFailureCapture
from lib.diagnostic_context import safe_context
from lib.report_document import build_report_document, render_markdown, public_projection
from test_probe import API, CASE, FakeResponse, response_lines


class HeaderResponse(FakeResponse):
    def __init__(self, fragments):
        super().__init__(fragments)
        headers = Message()
        headers.add_header('Content-Type', 'text/event-stream; charset=utf-8')
        headers.add_header('Set-Cookie', 'private-session=one')
        headers.add_header('Set-Cookie', 'private-session=two')
        self.headers = headers


class HeaderOpener:
    def __init__(self, fragments):
        self.fragments = fragments
    def open(self, request, timeout):
        return HeaderResponse(self.fragments)


class FullFailureEvidenceTests(unittest.TestCase):
    def test_more_than_12000_chars_preserved_with_duplicate_headers(self):
        case = copy.deepcopy(CASE)
        case['expected_keywords'] = ['这里没有的判断项']
        reply = '音' * 16000 + '完整尾部'
        frame = {'choices':[{'delta':{'content':{'data':{'text':reply}}}}]}
        fragments = response_lines(frame, {'choices':[{'delta':{'content':{'data':{'text':'结束'}}}}]})
        raw = b''.join(fragments)
        raw_stream = io.BytesIO()
        metadata = {}
        result = probe.run_case(case, API, opener=HeaderOpener(fragments),
                                raw_capture=raw_stream, capture_metadata=metadata)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['reason'], 'assertions_failed')
        self.assertEqual(raw_stream.getvalue(), raw)
        self.assertGreater(len(raw), 12000)
        self.assertLessEqual(len(result['response_excerpt']), 12000)
        cookies = [v for k,v in metadata['response']['headers'] if k.lower() == 'set-cookie']
        self.assertEqual(cookies, ['private-session=one','private-session=two'])
        public_context = safe_context(metadata)
        public_cookies = [v for k,v in public_context['response_headers'] if k.lower() == 'set-cookie']
        self.assertEqual(public_cookies, ['[REDACTED]','[REDACTED]'])

        with tempfile.TemporaryDirectory() as folder:
            with LatestFailureCapture(folder, API, {'type':'csv', 'path':'tests/cases.csv'}) as snap:
                evidence = snap.add(result, raw, metadata)
                self.assertEqual(evidence['received_bytes'],len(raw))
                snap.finish(1)
                target = snap.commit()
            payload = json.loads(target.read_text(encoding='utf8'))
            stored = payload['failed_cases'][0]
            self.assertEqual(base64.b64decode(stored['raw_sse_base64']), raw)
            self.assertEqual(stored['response']['headers'], metadata['response']['headers'])
            self.assertEqual(stored['sha256'], evidence['sha256'])
            self.assertEqual(stored['request']['body_json']['deviceId'], case['deviceId'])
            self.assertNotIn('raw_sse_base64', json.dumps(result))

    def test_markdown_failure_detail_embeds_full_sse_and_only_requested_fields(self):
        case = copy.deepcopy(CASE)
        case['case_id'] = 'FULL001'
        case['scene'] = '完整响应诊断场景'
        case['content'] = 'PRIVATE_TEST_UTTERANCE'
        case['expected_keywords'] = ['响应中必须存在的词']
        long_text = 'A' * 16000 + 'TAIL_MARKER_AFTER_EXCERPT'
        frames = response_lines({'choices':[{'delta':{'content':{'data':{'text':long_text}}}}]})
        fragments = frames + [b': FULL_RAW_SSE_TRAILER\n\n']
        capture = io.BytesIO()
        result = probe.run_case(case, API, opener=HeaderOpener(fragments),
                                raw_capture=capture, capture_metadata={})
        raw = capture.getvalue()
        self.assertEqual(result['status'], 'FAIL')
        self.assertGreater(len(raw), 12000)
        self.assertNotIn('TAIL_MARKER_AFTER_EXCERPT', result['response_excerpt'])

        with tempfile.TemporaryDirectory() as folder:
            with LatestFailureCapture(folder, API, {'type':'csv','path':'tests/cases.csv'}) as snap:
                result['raw_evidence'] = snap.add(result, raw, {
                    'response': {'status':200,'content_type':'text/event-stream'}
                })
                result['raw_sse_text'] = raw.decode('utf8')
                result['raw_sse_encoding'] = 'UTF-8'
                snap.finish(1)
                snap.commit()

        document = build_report_document('csv', {'reports':{}}, [result], [case], 10)
        markdown = render_markdown(document)
        failure = document['failure_cases'][0]
        self.assertEqual(document['version'], 6)
        self.assertEqual(failure['raw_sse_text'], raw.decode('utf8'))
        self.assertIn('### 场景：完整响应诊断场景', markdown)
        self.assertIn('PRIVATE_TEST_UTTERANCE', markdown)
        self.assertIn('响应中必须存在的词', markdown)
        self.assertIn('TAIL_MARKER_AFTER_EXCERPT', markdown)
        self.assertIn('FULL_RAW_SSE_TRAILER', markdown)
        self.assertIn('接收字节：{}'.format(len(raw)), markdown)
        self.assertNotIn('PRIVATE_DEVICE_ID', markdown)
        self.assertNotIn('reqId', markdown)
        self.assertNotIn('请求头', markdown)
        self.assertNotIn('response_excerpt', markdown)
        self.assertNotIn('慢 Token 用例', markdown)
        shared = json.dumps(public_projection(document), ensure_ascii=False)
        self.assertNotIn('PRIVATE_TEST_UTTERANCE', shared)
        self.assertNotIn('TAIL_MARKER_AFTER_EXCERPT', shared)

    def test_music_sse_example_matches_fields_across_many_frames(self):
        deeplink='midmusic://migumusic?action=%7B%22type%22%3A%22JUMP_DETAIL_PAGE%22%7D'
        def frame(data, type_='3'):
            return {'choices':[{'delta':{'content':{'data':data, 'type':type_}}}],
                    'model':'灵犀（家庭）智能体','reqId':'sample-request'}
        fragments=response_lines(
            frame({'text':'好嘞，江南是林俊杰的歌～'}, '1'),
            frame({'text':''}, '1'),
            frame({'questions':['江南歌词哪句好听','还有哪些歌']}),
            frame({'text':'找到啦，林俊杰的《江南》'}, '1'),
            frame({'code':4133,'jsonBody':'{"eventId":13,"params":{"action":1}}'}),
            frame({'deeplink':deeplink,'issueMode':1}),
            frame({'sdk':{'package':'com.czly.tabletmusic','name':'咪咕音乐'}}),
            frame({'text':'已为您播放林俊杰的《江南》'}, '1'))
        case=copy.deepcopy(CASE)
        case['expected_keywords']=[
            'deeplink字段以midmusic://migumusic开头',
            'sdk的package是com.czly.tabletmusic',
            '文本回复包含“林俊杰”',
            '衍生问题',
        ]
        capture=io.BytesIO()
        result=probe.run_case(case,API,opener=HeaderOpener(fragments),raw_capture=capture)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['missing_keywords'],[])
        self.assertTrue(result['done_received'])
        self.assertEqual(capture.getvalue(),b''.join(fragments))


    def test_error_event_and_invalid_json_keep_trailing_response(self):
        for chunks, reason in (
            ([b"event: error\ndata: blocked\n\n",
              b": downstream heartbeat\n\n",
              b"data: [DONE]\n\n"], "sse_error_event"),
            ([b"data: {invalid json}\n\n",
              b": trailer\n\n", b"data: [DONE]\n\n"], "invalid_sse_json"),
            ([b"data: {\"error\":\"upstream\"}\n\n",
              b": final diagnostic\n\n", b"data: [DONE]\n\n"], "upstream_business_error"),
        ):
            with self.subTest(reason=reason):
                capture=io.BytesIO()
                result=probe.run_case(CASE, API, opener=HeaderOpener(chunks),
                                      raw_capture=capture)
                self.assertEqual(result['reason'],reason)
                self.assertEqual(capture.getvalue(),b"".join(chunks))
                self.assertTrue(result['done_received'])

    def test_http_status_and_content_type_errors_keep_received_body(self):
        class StatusResponse(HeaderResponse):
            def __init__(self, chunks, status, content_type):
                super().__init__(chunks)
                self.status=status
                self.headers.replace_header('Content-Type',content_type)
        class StatusOpener:
            def __init__(self, chunks, status, content_type):
                self.chunks=chunks;self.status=status;self.content_type=content_type
            def open(self, req, timeout):
                return StatusResponse(self.chunks,self.status,self.content_type)
        for status, content_type, reason in (
            (503,'text/event-stream','unexpected_http_status'),
            (200,'application/json','unexpected_content_type'),
        ):
            with self.subTest(status=status,content_type=content_type):
                body=[b'{"error":"backend busy"}\n',b'after-status-error']
                capture=io.BytesIO()
                result=probe.run_case(CASE,API,
                                      opener=StatusOpener(body,status,content_type),
                                      raw_capture=capture)
                self.assertEqual(result['reason'],reason)
                self.assertEqual(capture.getvalue(),b''.join(body))
                self.assertTrue(result['http_headers_received'])
                self.assertEqual(result['http_status'],status)

if __name__ == '__main__':
    unittest.main()