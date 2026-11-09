"""Group-readable monitoring: exact 42 failures, source deep link, delivery safety."""
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from lib.feishu_notifier import render_card_v2, NoticeError
from lib.probe_notice_adapter import probe_notice
from lib.probe_group_report import (build_single_probe_card,
                                    source_link_from_config, report_label)
from lib.feishu_notification_router import send_probe_notice
from lib.feishu_transport import FeishuDeliveryError
from test_feishu_notification import Opener,WEBHOOK

LINK="https://docs.qq.com/sheet/EXAMPLEDOC123?tab=demo123"
SOURCE={"type":"tencent","tencent":{"doc_url":LINK+"&token=LEAK_ME",
                                   "sheet_id":"demo123"}}
SETTINGS={"enabled":True,"WEBHOOK_URL":WEBHOOK,"SECRET":"LOCAL_TEST_SECRET",
          "keyword":"飞书图表","notification_style":"v2"}


def summary_165():
    return {"total":165,"pass":123,"fail":37,"error":5,"pass_rate":74.55,
            "average_latency_ms":3554,"wall_duration_ms":4237333,
            "slow_token_threshold_ms":3000}


def failure_cases(n=42):
    return [{"case_id":str(i+1),"scene":"媒资播放控制",
             "status":"FAIL" if i<37 else "ERROR",
             "failure_brief":"业务判断未通过",
             "missing_keywords":['文本回复 包含“大山里的女孩上学”'] if i%2 else ["deeplink"],
             "forbidden_found":[],
             "first_token_ms":1819+i*11,"duration_ms":24642+i*110,
             "content":"SECRET_INPUT_DONT_SEND","deviceId":"SECRET_DEVICE_DONT_SEND",
             "response_excerpt":"SECRET_RESPONSE_DONT_SEND"} for i in range(n)]


class GroupReportTests(unittest.TestCase):
    def test_safe_tencent_link_with_query_credentials_stripped(self):
        self.assertEqual(source_link_from_config(SOURCE), LINK)
        self.assertEqual(source_link_from_config({
            "type":"tencent","tencent":{"doc_url":"https://evil.example/sheet/abc",
                                         "sheet_id":"klr3fi"}}),"")
        self.assertEqual(source_link_from_config({
            "type":"tencent","tencent":{"doc_url":"https://docs.qq.com/sheet/abc",
                                         "sheet_id":"klr3fi);javascript:alert"}}),"")
        with self.assertRaises(NoticeError):
            render_card_v2({"source_url":"https://evil.example","chart_type":"none"})
        with self.assertRaises(NoticeError):
            render_card_v2({"source_url":LINK+"&token=abc","chart_type":"none"})

    def test_five_single_row_metric_tiles_and_source_link_no_chart(self):
        n=probe_notice(summary_165(),failure_cases(),[],
                       title="飞书图表｜监控报告",source_url=LINK,
                       report_label="腾讯文档｜验收模拟 42 条失败，共4页")
        card=render_card_v2(n)["card"]
        self.assertEqual(card["body"]["elements"][0]["tag"],"column_set")
        cols=card["body"]["elements"][0]["columns"]
        self.assertEqual(len(cols),5)
        labels=[c["elements"][0]["text"]["content"] for c in cols]
        self.assertEqual(labels,["总数","通过","失败","异常","通过率"])
        values=[c["elements"][1]["text"]["content"] for c in cols]
        self.assertEqual(values,["165","123","37","5","74.55%"])
        self.assertFalse(any(e["tag"]=="chart" for e in card["body"]["elements"]))
        body=json.dumps(card,ensure_ascii=False)
        self.assertIn("docs.qq.com/sheet/",body)
        self.assertNotIn("LEAK_ME",body)
        self.assertNotIn("本地受限报告",body)
        self.assertNotIn("其余 40 项",body)

    def test_single_card_42_failures_complete_no_missing_and_bounded(self):
        cases=failure_cases()
        notice=probe_notice(summary_165(),cases,[],title="飞书图表｜监控报告",
                            source_url=LINK)
        payload=build_single_probe_card(notice,cases)
        self.assertLess(len(json.dumps(payload,ensure_ascii=False).encode("utf8")),19000)
        text="\n".join(x["content"] for x in payload["card"]["body"]["elements"]
                       if x["tag"]=="markdown")
        for item in cases:
            self.assertEqual(text.count("**#{} ".format(item["case_id"])),1)
        self.assertIn("失败及异常用例（42 条，全部列出）",text)
        self.assertNotIn("SECRET_INPUT_DONT_SEND",text)
        self.assertNotIn("SECRET_RESPONSE_DONT_SEND",text)

    def test_delivers_summary_and_all_42_for_group_members(self):
        opener=Opener()
        status=send_probe_notice(SETTINGS,summary_165(),[],"tencent","FAIL",
                                  opener=opener,failure_cases=failure_cases(),
                                  source_config=SOURCE,sleep_between=lambda _:None)
        self.assertEqual(status,"sent")
        self.assertEqual(len(opener.requests),1)
        card=json.loads(opener.requests[0].data)["card"]
        self.assertEqual(len(card["body"]["elements"][0]["columns"]),5)
        self.assertIn(LINK,json.dumps(card,ensure_ascii=False))
        text="\n".join(e["content"] for e in card["body"]["elements"]
                       if e["tag"]=="markdown")
        self.assertIn("失败及异常用例（42 条，全部列出）",text)
        for i in range(1,43):
            self.assertEqual(text.count("**#{} ".format(i)),1)
        self.assertNotIn("SECRET_INPUT_DONT_SEND",text)
        self.assertNotIn("SECRET_RESPONSE_DONT_SEND",text)
        self.assertLess(len(opener.requests[0].data),20000)

    def test_partial_send_error_is_reported_and_never_auto_retried(self):
        class FailingOpener:
            def __init__(self):
                self.requests=[]
            def open(self,req,timeout):
                self.requests.append(req)
                raise TimeoutError("simulated uncertain result")
                return Opener().open(req,timeout)
        o=FailingOpener()
        with self.assertRaises(FeishuDeliveryError):
            send_probe_notice(SETTINGS,summary_165(),[],"tencent","FAIL",
                              opener=o,failure_cases=failure_cases(),
                              source_config=SOURCE,sleep_between=lambda _:None)
        self.assertEqual(len(o.requests),1)

    def test_165_failures_fit_single_card_and_overlimit_never_truncates(self):
        cases=failure_cases(165)
        notice=probe_notice(summary_165(),cases,[],title="飞书图表｜监控报告")
        card=build_single_probe_card(notice,cases)
        text="\n".join(e["content"] for e in card["card"]["body"]["elements"]
                       if e["tag"]=="markdown")
        for item in cases:
            self.assertEqual(text.count("**#{} ".format(item["case_id"])),1)
        self.assertLess(len(json.dumps(card,ensure_ascii=False).encode("utf8")),19000)
        huge=failure_cases(380)
        n=probe_notice(summary_165(),huge,[])
        with self.assertRaisesRegex(NoticeError,"single_card_failure_list_too_large"):
            build_single_probe_card(n,huge)

    def test_source_error_empty_run_is_not_falsely_labeled_all_pass(self):
        from lib.probe_group_report import build_single_probe_card
        notice=probe_notice({"total":0,"pass":0,"fail":0,"error":0},[],[],
                            title="API拨测｜读取失败")
        card=build_single_probe_card(notice,[])
        text="\n".join(e["content"] for e in card["card"]["body"]["elements"]
                       if e["tag"]=="markdown")
        self.assertIn("请核对执行状态",text)
        self.assertNotIn("本轮全部用例通过",text)

    def test_overlong_or_unsafe_case_id_cannot_silently_truncate(self):
        from lib.probe_group_report import build_single_probe_card
        for cid in ("X"*65, "A|B", "a\nb"):
            cases=failure_cases(1)
            cases[0]["case_id"]=cid
            notice=probe_notice(summary_165(),cases,[])
            with self.assertRaisesRegex(NoticeError,"single_card_case_id_not_safe"):
                build_single_probe_card(notice,cases)

    def test_oversized_group_notice_sends_all_paged_messages(self):
        from lib.probe_group_report import build_single_probe_card
        class CountOpener:
            def __init__(self):self.requests=[]
            def open(self,req,timeout):
                self.requests.append(req)
                return Opener().open(req,timeout)
        o=CountOpener()
        result=send_probe_notice(SETTINGS,summary_165(),[],"tencent","FAIL",
                                 opener=o,failure_cases=failure_cases(380),
                                 sleep_between=lambda seconds:None)
        self.assertEqual(result["status"],"sent")
        self.assertGreater(result["cards_total"],1)
        self.assertEqual(len(o.requests),result["cards_total"])


    def test_paged_report_retains_every_failure_once_and_tracks_page_sequence(self):
        from lib.probe_group_report import build_probe_cards, MAX_SINGLE_CARD_BYTES
        cases=failure_cases(380)
        notice=probe_notice(summary_165(),cases,[],title="飞书图表｜监控报告",
                            source_url=LINK)
        cards=build_probe_cards(notice,cases)
        self.assertGreater(len(cards),1)
        contents=[]
        for number,card in enumerate(cards,1):
            self.assertLessEqual(len(json.dumps(card,ensure_ascii=False,
                                                separators=(",",":")).encode("utf8")),
                                 MAX_SINGLE_CARD_BYTES)
            text="\n".join(e["content"] for e in card["card"]["body"]["elements"]
                           if e["tag"]=="markdown")
            self.assertIn("第 {}/{} 张".format(number,len(cards)),text)
            self.assertIn(LINK,json.dumps(card,ensure_ascii=False))
            self.assertEqual(card["card"]["body"]["elements"][0]["tag"],"column_set")
            contents.append(text)
        all_text="\n".join(contents)
        for item in cases:
            self.assertEqual(all_text.count("**#{} ".format(item["case_id"])),1)
        self.assertNotIn("SECRET_INPUT_DONT_SEND",all_text)
        self.assertNotIn("SECRET_RESPONSE_DONT_SEND",all_text)

        opener=Opener()
        receipt=send_probe_notice(SETTINGS,summary_165(),[],"tencent","FAIL",
                                  opener=opener,failure_cases=cases,
                                  source_config=SOURCE,sleep_between=lambda seconds:None)
        self.assertEqual(receipt["status"],"sent")
        self.assertEqual(receipt["cards_total"],len(cards))
        self.assertEqual(receipt["cards_confirmed"],len(cards))
        self.assertEqual(len(opener.requests),len(cards))
        for req in opener.requests:
            self.assertLess(len(req.data),20000)

    def test_second_page_rejection_or_uncertain_response_stops_without_retry(self):
        from lib.feishu_notification_router import PagedDeliveryError
        cases=failure_cases(700)
        for failure in ("rejected","uncertain"):
            class PageOpener:
                def __init__(self):self.requests=[]
                def open(self,req,timeout):
                    self.requests.append(req)
                    if len(self.requests)==2:
                        if failure=="rejected":
                            return Opener(body=b'{"code":19007,"msg":"Bot Not Enabled"}').open(req,timeout)
                        raise TimeoutError("possible delivery")
                    return Opener().open(req,timeout)
            opener=PageOpener()
            with self.assertRaises(PagedDeliveryError) as context:
                send_probe_notice(SETTINGS,summary_165(),[],"tencent","FAIL",
                                  opener=opener,failure_cases=cases,
                                  sleep_between=lambda seconds:None)
            detail=context.exception.details()
            self.assertGreater(detail["cards_total"],2)
            self.assertEqual(detail["cards_confirmed"],1)
            self.assertEqual(detail["cards_attempted"],2)
            self.assertEqual(detail["failed_page"],2)
            self.assertEqual(detail["failed_page_outcome"],failure)
            self.assertFalse(detail["automatic_retry"])
            self.assertEqual(len(opener.requests),2)

    def test_late_page_invalid_case_id_preflight_sends_zero_messages(self):
        cases=failure_cases(380)
        cases[-1]["case_id"]="INVALID|ID"
        opener=Opener()
        with self.assertRaisesRegex(FeishuDeliveryError,"card_render_failed"):
            send_probe_notice(SETTINGS,summary_165(),[],"tencent","FAIL",
                              opener=opener,failure_cases=cases,
                              sleep_between=lambda seconds:None)
        self.assertEqual(len(opener.requests),0)

if __name__=="__main__":
    unittest.main()