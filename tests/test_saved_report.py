"""Saved JSON report can be shared without rerunning any business cases."""
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from share_report import locate_report, stored_time, main

class SavedReportTests(unittest.TestCase):
    def test_dry_run_counts_all_cases_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            reports=root/"reports"
            reports.mkdir()
            path=reports/"probe-20261102T035921Z-local.json"
            cases=[{"case_id":str(i),"scene":"失败场景","status":"FAIL",
                    "missing_keywords":["deeplink"],"first_token_ms":1000,
                    "duration_ms":2400} for i in range(1,43)]
            report={"version":5,"source_type":"tencent",
                    "summary":{"total":165,"pass":123,"fail":37,"error":5},
                    "results":[{"case_id":str(i)} for i in range(165)],
                    "failure_cases":cases,"slow_token_cases":[]}
            path.write_text(json.dumps(report,ensure_ascii=False),encoding="utf8")
            config=root/"config.local.json"
            config.write_text(json.dumps({"reports":{"dir":"reports"},
                                          "source":{"type":"tencent"},
                                          "feishu":{}}),encoding="utf8")
            import io
            from contextlib import redirect_stdout
            out=io.StringIO()
            with redirect_stdout(out):
                code=main(["--config",str(config),"--report","latest"])
            self.assertEqual(code,0)
            data=json.loads(out.getvalue())
            self.assertEqual(data["total"],165)
            self.assertEqual(data["failed_cases"],42)
            self.assertEqual(data["group_cards"],1)
            self.assertEqual(data["requests_sent"],0)


    def test_historical_large_report_preflights_multiple_cards_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            from contextlib import redirect_stdout
            import io
            root=Path(folder)
            reports=root/"reports"
            reports.mkdir()
            saved=reports/"probe-20261102T035921Z-many.json"
            failures=[{"case_id":str(i),"scene":"长批次播控","status":"FAIL",
                       "missing_keywords":["deeplink"],"first_token_ms":1000,
                       "duration_ms":2400} for i in range(1,701)]
            report={"version":6,"source_type":"tencent",
                    "summary":{"total":700,"pass":0,"fail":700,"error":0},
                    "results":[{"case_id":str(i)} for i in range(1,701)],
                    "failure_cases":failures,"slow_token_cases":[]}
            saved.write_text(json.dumps(report,ensure_ascii=False),encoding="utf8")
            config=root/"config.local.json"
            config.write_text(json.dumps({"reports":{"dir":"reports"},
                                          "source":{"type":"tencent"},
                                          "feishu":{"notification_style":"v2"}}),
                              encoding="utf8")
            out=io.StringIO()
            with patch("share_report.send_probe_notice") as sender:
                with redirect_stdout(out):
                    code=main(["--config",str(config),"--report","latest"])
            self.assertEqual(code,0)
            sender.assert_not_called()
            payload=json.loads(out.getvalue())
            self.assertEqual(payload["failed_cases"],700)
            self.assertGreater(payload["group_cards"],1)
            self.assertEqual(payload["requests_sent"],0)

    def test_report_path_cannot_escape_private_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            reports=Path(folder)/"reports"
            reports.mkdir()
            with self.assertRaisesRegex(ValueError,"report_path_outside_private_dir"):
                locate_report(reports,"../config.local.json")

    def test_report_saved_utc_time_converts_to_original_local_time(self):
        stamp=stored_time(Path("probe-20261102T035921Z-x.json"))
        self.assertEqual(stamp.hour,11)
        self.assertEqual(stamp.minute,59)
        self.assertEqual(stamp.utcoffset(),timedelta(hours=8))

if __name__=="__main__":unittest.main()