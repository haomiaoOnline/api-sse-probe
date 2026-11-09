"""Experimental Card 2.0 renderer/router; no live network."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from lib import feishu
from lib.feishu_notifier import render_card_v2,render_markdown_v2,normalize_notice,NoticeError
from lib.feishu_notification_router import send_probe_notice
from lib.probe_notice_adapter import probe_notice
from test_feishu_notification import Opener,WEBHOOK

BASE={"enabled":True,"webhook_url":WEBHOOK,"secret":"DUMMY_KEY","keyword":"API拨测"}
SUMMARY={"total":4,"pass":2,"fail":1,"error":1,"pass_rate":50.0,
         "average_latency_ms":1850,"wall_duration_ms":12340,
         "slow_token_threshold_ms":3000}
FAILURES=[{"case_id":"17","scene":"媒资播放控制","failure_brief":"断言未通过",
           "missing_keywords":["deeplink"],"first_token_ms":1819,
           "content":"SECRET_UTTERANCE","response_excerpt":"SECRET_RESPONSE_TEXT"}]
RESULTS=[{"case_id":str(i),"status":"FAIL" if i==17 else "PASS",
          "duration_ms":1000} for i in range(20)]

class FeishuChartSpikeTests(unittest.TestCase):
    def sample(self,name="probe"):
        return json.loads((ROOT/"examples/feishu_notifications"/("sample_"+name+".json"))
                          .read_text(encoding="utf8"))

    def test_card2_shape_and_vchart(self):
        payload=render_card_v2(self.sample())
        self.assertEqual(payload["msg_type"],"interactive")
        card=payload["card"]
        self.assertEqual(card["schema"],"2.0")
        self.assertEqual(card["config"]["update_multi"],True)
        self.assertNotIn("elements",card)
        elements=card["body"]["elements"]
        chart=[x for x in elements if x["tag"]=="chart"]
        self.assertEqual(len(chart),1)
        spec=chart[0]["chart_spec"]
        self.assertEqual(spec["type"],"pie")
        self.assertEqual(spec["categoryField"],"category")
        self.assertEqual(spec["valueField"],"count")
        self.assertEqual(spec["innerRadius"],0.58)
        self.assertEqual(spec["indicator"]["title"]["text"],"96.36%")
        self.assertEqual(len(spec["data"][0]["values"]),3)

    def test_compact_group_chat_layout_limits_chart_and_density(self):
        from lib.feishu_notifier import COMPACT_TOKENS
        payload=render_card_v2(self.sample())
        card=payload["card"]
        self.assertEqual(card["config"]["width_mode"],"compact")
        self.assertEqual(card["body"]["vertical_spacing"],"4px")
        self.assertEqual(card["body"]["padding"],"8px 10px 8px 10px")
        elements=card["body"]["elements"]
        self.assertLessEqual(len(elements),5)
        chart=next(e for e in elements if e["tag"]=="chart")
        self.assertEqual(chart["height"],"112px")
        self.assertEqual(chart["margin"],"0px")
        self.assertFalse(chart["preview"])
        self.assertLessEqual(int(chart["height"][:-2]),120)
        self.assertEqual(COMPACT_TOKENS["max_issues"],2)
        self.assertEqual(elements[0]["tag"],"column_set")
        self.assertEqual(elements[0]["flex_mode"],"none")
        self.assertEqual(len(elements[0]["columns"]),3)
        self.assertTrue(any("通过率" in c["elements"][0]["text"]["content"]
                            for c in elements[0]["columns"]))
        self.assertTrue(any("媒资播放控制" in x["content"] for x in elements if x["tag"]=="markdown"))

    def test_three_real_kpi_tiles_have_semantic_styles_and_fixed_density(self):
        from lib.feishu_notifier import COMPACT_TOKENS
        for kind,expected in (("probe", ("用例","通过率","平均首帧")),
                              ("rss", ("新增","信源","失败信源"))):
            card=render_card_v2(self.sample(kind))["card"]
            row=card["body"]["elements"][0]
            self.assertEqual(row["tag"],"column_set")
            self.assertEqual(row["flex_mode"],"none")
            self.assertEqual(row["horizontal_spacing"],"4px")
            self.assertEqual(len(row["columns"]),3)
            self.assertEqual([col["elements"][0]["text"]["content"]
                              for col in row["columns"]],list(expected))
            for column in row["columns"]:
                self.assertEqual(column["padding"],COMPACT_TOKENS["kpi_padding"])
                self.assertEqual(column["width"],"weighted")
                self.assertEqual(column["weight"],1)
                self.assertIn(column["background_style"],("blue-50","green-50","orange-50","red-50","grey-50"))
                self.assertEqual(column["vertical_spacing"],"0px")
                self.assertEqual(len(column["elements"]),2)
                label,value=column["elements"]
                self.assertEqual(label["text"]["text_size"],"notation")
                self.assertEqual(value["text"]["text_size"],"heading-4")
                self.assertEqual(label["text"]["text_align"],"center")
                self.assertEqual(value["text"]["text_align"],"center")
            self.assertLess(len(json.dumps(card,ensure_ascii=False).encode("utf8")),6500)
            self.assertEqual(next(e for e in card["body"]["elements"]
                                  if e["tag"]=="chart")["height"],"112px")

    def test_kpi_values_are_bounded_and_secret_redacted(self):
        notice={"title":"UI test","metrics":[
            {"label":"token=PRIVATE","value":"signature=HIDDEN"},
            {"label":"通过率","value":"99.2%"},
            {"label":"冗长的测试标签" * 10,"value":"X"*50}]}
        card=render_card_v2(notice)["card"]
        row=card["body"]["elements"][0]
        col=row["columns"]
        text=json.dumps(row,ensure_ascii=False)
        self.assertNotIn("PRIVATE",text)
        self.assertNotIn("HIDDEN",text)
        self.assertLessEqual(len(col[2]["elements"][0]["text"]["content"]),9)
        self.assertLessEqual(len(col[2]["elements"][1]["text"]["content"]),13)

    def test_compact_issues_show_top_two_and_remaining_count(self):
        n=self.sample()
        n["issues"]=[{"id":str(i),"name":"场景"+str(i),"detail":"异常描述"+"很长"*55}
                     for i in range(165)]
        payload=render_card_v2(n)
        elements=payload["card"]["body"]["elements"]
        md="\\n".join(x["content"] for x in elements if x["tag"]=="markdown")
        self.assertIn("异常 165 项",md)
        self.assertIn("其余 163 项见报告",md)
        self.assertIn("场景0",md)
        self.assertIn("场景1",md)
        self.assertNotIn("场景2",md)
        self.assertLess(len(json.dumps(payload,ensure_ascii=False).encode("utf8")),4500)
        self.assertEqual(sum(x["tag"]=="chart" for x in elements),1)

    def test_compact_chart_keeps_exact_values_in_text_for_small_bars(self):
        card=render_card_v2(self.sample())["card"]
        summary=next(x["content"] for x in card["body"]["elements"] if x["tag"]=="markdown")
        self.assertIn("PASS 159",summary)
        self.assertIn("FAIL 4",summary)
        self.assertIn("ERROR 2",summary)
        chart=next(x for x in card["body"]["elements"] if x["tag"]=="chart")
        values=chart["chart_spec"]["data"][0]["values"]
        self.assertEqual(sum(x["count"] for x in values),165)
        self.assertTrue(any(x["count"]==2 for x in values))

    def test_compact_chart_free_fallback_retains_distribution_and_failure(self):
        n=self.sample()
        card=render_markdown_v2(n)["card"]
        self.assertTrue(all(x["tag"]!="chart" for x in card["body"]["elements"]))
        self.assertEqual(card["body"]["elements"][0]["tag"],"column_set")
        content="\\n".join(x["content"] for x in card["body"]["elements"]
                             if x["tag"]=="markdown")
        self.assertIn("PASS 159",content)
        self.assertIn("媒资播放控制",content)
        self.assertIn("异常 2 项",content)

    def test_two_distinct_skill_models_reuse_renderer(self):
        rss=render_card_v2(self.sample("rss"))
        api=render_card_v2(self.sample("probe"))
        self.assertEqual(rss["card"]["schema"],api["card"]["schema"])
        self.assertNotEqual(rss["card"]["header"]["title"]["content"],
                            api["card"]["header"]["title"]["content"])
        self.assertEqual(sum(x["tag"]=="chart" for x in rss["card"]["body"]["elements"]),1)
        rss_spec=next(x["chart_spec"] for x in rss["card"]["body"]["elements"]
                      if x["tag"]=="chart")
        api_spec=next(x["chart_spec"] for x in api["card"]["body"]["elements"]
                      if x["tag"]=="chart")
        self.assertEqual(rss_spec["type"],"bar")
        self.assertEqual(api_spec["type"],"pie")


    def test_chart_type_is_explicit_and_validated(self):
        n=self.sample("rss")
        n["chart_type"]="donut"
        n["chart_center"]="60%"
        spec=next(x["chart_spec"] for x in render_card_v2(n)["card"]["body"]["elements"]
                  if x["tag"]=="chart")
        self.assertEqual(spec["type"],"pie")
        self.assertEqual(spec["indicator"]["title"]["text"],"60%")
        n["chart_type"]="invalid"
        with self.assertRaisesRegex(NoticeError,"chart_type_invalid"):
            render_card_v2(n)
        n["chart_type"]="bar"
        n["chart_center"]=42
        with self.assertRaisesRegex(NoticeError,"chart_center_invalid"):
            render_card_v2(n)

    def test_actual_probe_adapter_selects_compact_five_tiles_not_chart(self):
        notice=probe_notice(SUMMARY,FAILURES,[],"拨测")
        self.assertEqual(notice["chart_type"],"none")
        self.assertEqual([m["label"] for m in notice["metrics"]],
                         ["总数","通过","失败","异常","通过率"])
        elements=render_card_v2(notice)["card"]["body"]["elements"]
        self.assertEqual(len(elements[0]["columns"]),5)
        self.assertFalse(any(e["tag"]=="chart" for e in elements))
        self.assertNotIn("本地受限报告",json.dumps(elements,ensure_ascii=False))


    def test_without_distribution_uses_markdown_only(self):
        payload=render_card_v2({"title":"空报告","metrics":[{"label":"数","value":"0"}]})
        self.assertTrue(all(x["tag"]!="chart" for x in payload["card"]["body"]["elements"]))
        self.assertEqual(payload["card"]["body"]["elements"][0]["tag"],"column_set")
        fallback = render_markdown_v2(self.sample())["card"]["body"]["elements"]
        self.assertTrue(all(x["tag"]!="chart" for x in fallback))
        self.assertEqual(fallback[0]["tag"],"column_set")

    def test_oversized_issue_list_caps_payload(self):
        notice=self.sample()
        notice["issues"]=[{"id":str(i),"name":"问题","detail":"异常描述" * 80} for i in range(800)]
        payload=render_card_v2(notice)
        body=json.dumps(payload,ensure_ascii=False).encode("utf8")
        self.assertLess(len(body),14000)
        text="\n".join(x["content"] for x in payload["card"]["body"]["elements"]
                       if x["tag"]=="markdown")
        self.assertIn("展示 ",text)

    def test_input_validation_and_secret_redaction(self):
        with self.assertRaises(NoticeError):
            render_card_v2({"title":"test","distribution":[{"label":"FAIL","value":float("nan")}]})
        with self.assertRaises(NoticeError):
            render_card_v2({"title":"test","distribution":[{"label":"FAIL","value":True}]})
        r=render_card_v2({"title":"token=LEAK_SECRET","metrics":[
            {"label":"URL","value":"signature=PASSWORD"}]})
        content=json.dumps(r,ensure_ascii=False)
        self.assertNotIn("LEAK_SECRET",content)
        self.assertNotIn("PASSWORD",content)

    def test_probe_adapter_drops_sensitive_contents(self):
        notice=probe_notice(SUMMARY,FAILURES,[],"拨测")
        data=json.dumps(notice,ensure_ascii=False)
        self.assertIn("deeplink",data)
        self.assertNotIn("SECRET_UTTERANCE",data)
        self.assertNotIn("SECRET_RESPONSE_TEXT",data)
        self.assertEqual(len(notice["issues"]),1)

    def test_default_style_still_sends_original_card(self):
        opener=Opener()
        result=send_probe_notice(BASE,SUMMARY,RESULTS,"tencent","FAIL",
                                 opener=opener,failure_cases=FAILURES)
        self.assertEqual(result,"sent")
        payload=json.loads(opener.requests[0].data)
        self.assertEqual(payload["card"]["schema"],"2.0")
        self.assertIn("deeplink",json.dumps(payload,ensure_ascii=False))
        self.assertEqual(len(opener.requests),1)

    def test_v2_style_sends_summary_and_full_failure_card(self):
        opener=Opener()
        r=send_probe_notice(dict(BASE,notification_style="v2"),
                            SUMMARY,RESULTS,"tencent","FAIL",
                            opener=opener,failure_cases=FAILURES,
                            sleep_between=lambda x:None)
        self.assertEqual(r,"sent")
        self.assertEqual(len(opener.requests),1)
        p=json.loads(opener.requests[0].data)
        self.assertEqual(p["card"]["schema"],"2.0")
        self.assertEqual(p["card"]["header"]["title"]["content"],"API拨测｜监控报告")
        self.assertNotIn("图表实验",json.dumps(p,ensure_ascii=False))
        tiles=p["card"]["body"]["elements"][0]
        self.assertEqual(tiles["tag"],"column_set")
        self.assertEqual(len(tiles["columns"]),5)
        self.assertFalse(any(x["tag"]=="chart" for x in p["card"]["body"]["elements"]))
        self.assertIn("失败及异常用例（1 条，全部列出）",json.dumps(p,ensure_ascii=False))
        self.assertIn("deeplink",json.dumps(p,ensure_ascii=False))
        self.assertEqual(len(p["timestamp"]),10)
        self.assertIn("sign",p)
        self.assertNotIn("SECRET_RESPONSE_TEXT",json.dumps(p))
        self.assertNotIn("SECRET_UTTERANCE",json.dumps(p))


    def test_auto_downgrades_only_explicit_schema_rejection(self):
        class MultiOpener:
            def __init__(self): self.requests=[]
            def open(self,req,timeout):
                self.requests.append(req)
                return Opener(body=b'{"code":230099}').open(req,timeout) if len(self.requests)==1 else Opener().open(req,timeout)
        op=MultiOpener()
        with self.assertRaisesRegex(feishu.FeishuDeliveryError,"feishu_rejected_230099"):
            send_probe_notice(dict(BASE,notification_style="auto"),
                              SUMMARY,RESULTS,"tencent","FAIL",
                              opener=op,failure_cases=FAILURES)
        self.assertEqual(len(op.requests),1)
        self.assertEqual(json.loads(op.requests[0].data)["card"]["schema"],"2.0")

    def test_uncertain_transport_cannot_retry(self):
        class FailOpener:
            def __init__(self):self.requests=[]
            def open(self,req,timeout):
                self.requests.append(req)
                raise TimeoutError("network uncertain")
        op=FailOpener()
        with self.assertRaises(feishu.FeishuDeliveryError):
            send_probe_notice(dict(BASE,notification_style="auto"),
                              SUMMARY,RESULTS,"tencent","FAIL",
                              opener=op,failure_cases=FAILURES)
        self.assertEqual(len(op.requests),1)

    def test_invalid_style_fails_closed(self):
        with self.assertRaisesRegex(feishu.FeishuDeliveryError,"notification_style_invalid"):
            send_probe_notice(dict(BASE,notification_style="unknown"),
                              SUMMARY,RESULTS,"tencent","FAIL")

    def test_generic_cli_default_offline_and_test_gate(self):
        import tempfile
        from feishu_notice_cli import main
        sample=ROOT/"examples/feishu_notifications/sample_rss.json"
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/"card.json"
            self.assertEqual(main(["--input",str(sample),"--output",str(output)]),0)
            self.assertEqual(json.loads(output.read_text(encoding="utf8"))["card"]["schema"],"2.0")
            # No explicit test-group credentials: cannot send.
            with self.assertRaises(SystemExit) as ex:
                main(["--input",str(sample),"--output",str(output),"--send-test"])
            self.assertEqual(ex.exception.code,2)
            settings=Path(d)/"private.json"
            settings.write_text(json.dumps({"feishu":{"WEBHOOK_URL":WEBHOOK},
                         "feishu_test":{"confirmed_test_group":True,
                          "WEBHOOK_URL":WEBHOOK,"SECRET":"DUMMY_KEY"}}),encoding="utf8")
            with self.assertRaises(SystemExit) as ex:
                main(["--input",str(sample),"--output",str(output),
                      "--send-test","--settings",str(settings)])
            self.assertEqual(ex.exception.code,2)

    def test_cli_inserts_test_keyword_and_never_sends_without_opt_in(self):
        import tempfile
        from unittest.mock import patch
        from feishu_notice_cli import main
        sample=ROOT/"examples/feishu_notifications/sample_rss.json"
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/"rss.card.json"
            settings=Path(d)/"test.json"
            settings.write_text(json.dumps({
                "feishu":{"WEBHOOK_URL":WEBHOOK},
                "feishu_test":{
                    "enabled":True,"confirmed_test_group":True,
                    "WEBHOOK_URL":"https://open.feishu.cn/open-apis/bot/v2/hook/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "SECRET":"TEST_PRIVATE_SECRET","keyword":"飞书图表"}
            }),encoding="utf8")
            with patch("feishu_notice_cli.send_card_payload",return_value="sent") as mock_send:
                self.assertEqual(main(["--input",str(sample),"--output",str(output),
                                       "--settings",str(settings),"--send-test"]),0)
            self.assertEqual(mock_send.call_count,1)
            card=json.loads(output.read_text(encoding="utf8"))
            self.assertTrue(card["card"]["header"]["title"]["content"].startswith("飞书图表"))
            self.assertIn("RSS",card["card"]["header"]["title"]["content"])
            self.assertEqual(sum(e.get("tag")=="chart" for e in card["card"]["body"]["elements"]),1)
            # Do not accidentally persist the test Webhook or secret in chart output.
            serialized=json.dumps(card,ensure_ascii=False)
            self.assertNotIn("TEST_PRIVATE_SECRET",serialized)
            self.assertNotIn("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",serialized)

    def test_local_preview_generates_two_files_no_network(self):
        import tempfile
        from preview_feishu_cards import main
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["--sample","probe","--out-dir",d]),0)
            card=json.loads((Path(d)/"probe.card2.json").read_text(encoding="utf8"))
            html=(Path(d)/"probe.preview.html").read_text(encoding="utf8")
            self.assertEqual(card["card"]["schema"],"2.0")
            self.assertIn("静态离线近似预览",html)
            self.assertIn("PASS",html)
            self.assertIn('class="donut"',html)
            self.assertIn("96.36%",html)

if __name__=="__main__":
    unittest.main()