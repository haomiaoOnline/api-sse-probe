"""CLI for any Skill: render generic Card 2.0, optionally send to test group.

OFFLINE by default. Never sends from --render or --input alone.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from lib.feishu_notifier import render_card_v2
from lib.feishu_transport import send_card_payload

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True,help="JSON notice in generic schema")
    parser.add_argument("--output",default="artifacts/feishu-v2-preview/custom.card2.json")
    parser.add_argument("--send-test",action="store_true",help="Opt in to a test-group delivery")
    parser.add_argument("--settings",help="Private JSON with feishu_test section")
    args=parser.parse_args(argv)
    notice=json.loads(Path(args.input).read_text(encoding="utf8"))
    test=None
    if args.send_test:
        if not args.settings:
            parser.error("test group private settings required")
        config=json.loads(Path(args.settings).read_text(encoding="utf8"))
        test=config.get("feishu_test",{})
        production=config.get("feishu",{})
        if test.get("confirmed_test_group") is not True:
            parser.error("feishu_test.confirmed_test_group must be true")
        target=test.get("WEBHOOK_URL") or test.get("webhook_url")
        prod=production.get("WEBHOOK_URL") or production.get("webhook_url")
        if not target or (prod and target==prod):
            parser.error("test webhook missing or equal to production webhook")
        keyword=test.get("keyword")
        if not isinstance(keyword,str) or not keyword.strip():
            parser.error("feishu_test.keyword is required")
        # The group robot may enforce a content keyword. Explicitly put it
        # into the title, rather than merely validating a config field.
        if keyword not in str(notice.get("title","")):
            notice["title"]=keyword+"｜"+str(notice.get("title","通知"))
    card=render_card_v2(notice)
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(card,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    result={"status":"rendered","output":str(output),"network_calls":0}
    if args.send_test:
        receipt=send_card_payload(test,card)
        result.update(status=receipt,network_calls=1)
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())