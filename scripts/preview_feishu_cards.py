"""Offline Card 2.0 JSON + approximate HTML visualization; sends NOTHING.

python3 scripts/preview_feishu_cards.py --sample probe
python3 scripts/preview_feishu_cards.py --sample rss
"""
import argparse
import html
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from lib.feishu_notifier import normalize_notice, render_card_v2

def build_html(notice):
    """Standalone approximation of compact card spacing, not Feishu client UI."""
    from lib.feishu_notifier import COMPACT_TOKENS
    n=normalize_notice(notice)
    esc=lambda v:html.escape(str(v),quote=True)
    max_value=max((x["value"] for x in n["distribution"]),default=1) or 1
    metrics="".join(
        '<div class="metric tile{}"><div class="metric-label">{}</div>'
        '<div class="metric-value">{}</div></div>'.format(
            i, esc(m["label"][:COMPACT_TOKENS["kpi_label_chars"]]),
            esc(m["value"][:COMPACT_TOKENS["kpi_value_chars"]]))
        for i,m in enumerate(n["metrics"][:5 if n["chart_type"]=="none" else 3]))
    dist="".join('<div class="barrow"><span>{}</span><div class="track">'
                 '<div class="bar" style="width:{:.1f}%"></div></div>'
                 '<b>{}</b></div>'.format(
                     esc(x["label"]),min(100,100*x["value"]/max_value),esc(x["value"]))
                 for x in n["distribution"])
    if n["chart_type"] == "donut" and n["distribution"]:
        colors=("#2BA875", "#EDAA45", "#E35C60")
        total=sum(x["value"] for x in n["distribution"])
        cursor=0.0
        stops=[]
        if total:
            for index, item in enumerate(n["distribution"]):
                end=cursor+100.0*item["value"]/total
                stops.append("{} {:.2f}% {:.2f}%".format(
                    colors[index % len(colors)],cursor,end))
                cursor=end
        else:
            stops.append("#E6EBF0 0% 100%")
        donut_center=esc(n["chart_center"] or "{} 项".format(int(total)))
        dist=('<div class="donut" style="background:conic-gradient({})">'
              '<div class="donut-hole">{}</div></div>').format(
                ", ".join(stops),donut_center)
    top=n["issues"][:COMPACT_TOKENS["max_issues"]]
    issues="".join('<div class="issue"><b>✕ {} {}</b>：{}</div>'.format(
        esc(x["id"]),esc(x["name"]),esc(x["detail"][:COMPACT_TOKENS["issue_detail_chars"]]))
        for x in top)
    more=('其余 {} 项见报告'.format(len(n["issues"])-len(top))
          if len(n["issues"])>len(top) else "")
    other="　·　".join(
        '{} {}'.format(esc(m["label"]),esc(m["value"])) for m in n["metrics"][3:])
    foot=(" · ".join(x for x in (other,esc(n["footer"][:85])) if x))[:160]
    url_html = ('<div class="source"><a href="{}">打开腾讯文档 · 监控用例来源</a></div>'
                .format(esc(n["source_url"])) if n.get("source_url") else "")
    chart_html = ('<div class="chart">{}</div>'.format(dist)
                  if n["chart_type"] != "none" else "")
    issue_html = ('<div class="issues"><h2>异常 {} 项</h2>{}<div class="more">{}</div></div>'
                  .format(len(n["issues"]), issues, esc(more))
                  if n["issues"] else "")
    return """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>飞书群卡片 · 紧凑版离线示意</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f2f4f7;color:#202b38;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:460px;margin:22px auto;padding:0 12px}
.tip{font-size:12px;color:#758194;margin-bottom:10px}
.card{background:white;border-radius:11px;box-shadow:0 4px 16px #25304711;overflow:hidden}
header{padding:11px 13px;background:#e8edf8;font-size:15px;font-weight:650}
.content{padding:9px 11px 10px}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;align-items:stretch}
.metrics.five{grid-template-columns:repeat(5,minmax(0,1fr));gap:3px}
.metrics.five .metric{padding:4px 1px;min-height:40px}
.metrics.five .metric-value{font-size:13px}
.source{font-size:11px;margin:8px 0 3px}
.source a{color:#2265c9;text-decoration:none}
.metric{min-width:0;min-height:43px;border-radius:5px;padding:4px 5px;text-align:center;overflow:hidden}
.tile0{background:#f0f4ff}.tile1{background:#e4fae1}.tile2{background:#f5f6f7}
.metric-label{font-size:11px;line-height:15px;white-space:nowrap;color:#646a73}
.metric-value{font-size:16px;line-height:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:650;color:#1f2329}
.tile0 .metric-value{color:#0442d2}.tile1 .metric-value{color:#0b6017}
.distribution{margin:7px 0 1px;display:flex;flex-wrap:wrap;gap:9px;font-size:11px;color:#5d687a}
.chart{height:112px;overflow:hidden;padding:3px 7px 1px;background:#fafbfd;margin:5px 0 4px;border-radius:5px}
.barrow{display:flex;align-items:center;gap:9px;margin:4px 0;font-size:11px}
.barrow span{width:38px;color:#647187}.barrow b{width:30px;text-align:right}
.track{height:9px;border-radius:3px;background:#e7edf6;flex:1;overflow:hidden}
.bar{height:100%;background:#6185d5}
.donut{width:108px;height:108px;border-radius:50%;margin:0 auto;display:flex;align-items:center;justify-content:center}
.donut-hole{background:#fff;width:62%;height:62%;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:#27374b}

.issues{border-top:1px solid #eef1f4;padding-top:8px;margin-top:7px}
.issues h2{margin:0 0 4px;font-size:12px;color:#3b4c63}
.issue{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:3px 0}
.issue b{font-weight:550;color:#c04f4f}
.more{font-size:11px;color:#687588}
footer{font-size:11px;color:#7b8591;margin-top:7px}
@media(max-width:380px){main{margin:10px auto;padding:0 7px}.metrics{gap:3px}.metric{padding:3px 2px}.metric-value{font-size:15px}.content{padding:8px}}
</style></head><body><main><p class="tip">静态离线近似预览，不代表飞书客户端真实渲染。未连接任何服务。</p>
<div class="card"><header>__TITLE__</header><div class="content">
<div class="metrics __METRIC_CLASS__">__METRICS__</div>
<div class="distribution">__DIST_LABELS__</div>
__SOURCE__
__CHART_HTML__
__ISSUES_HTML__
<footer>__FOOT__</footer></div></div></main></body></html>""".replace(
        "__TITLE__",esc(n["title"][:COMPACT_TOKENS["title_chars"]])).replace(
        "__METRICS__",metrics).replace(
        "__METRIC_CLASS__","five" if n["chart_type"]=="none" else "").replace(
        "__DIST_LABELS__"," ".join("{} {}".format(esc(x["label"]),esc(x["value"]))
                                 for x in n["distribution"])).replace(
        "__SOURCE__",url_html).replace("__CHART_HTML__",chart_html).replace(
        "__ISSUES_HTML__",issue_html).replace("__FOOT__",foot)

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--sample",choices=("probe","rss","probe_group"),default="probe")
    p.add_argument("--out-dir",default="artifacts/feishu-v2-preview")
    args=p.parse_args(argv)
    source=ROOT/"examples"/"feishu_notifications"/("sample_"+args.sample+".json")
    notice=json.loads(source.read_text(encoding="utf8"))
    envelope=render_card_v2(notice)
    folder=Path(args.out_dir)
    if not folder.is_absolute():folder=ROOT/folder
    folder.mkdir(parents=True,exist_ok=True)
    jsonfile=folder/(args.sample+".card2.json")
    htmlfile=folder/(args.sample+".preview.html")
    jsonfile.write_text(json.dumps(envelope,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    htmlfile.write_text(build_html(notice),encoding="utf8")
    print(json.dumps({"sample":args.sample,"card_json":str(jsonfile),
                      "html_preview":str(htmlfile),
                      "chart_components":sum(x["tag"]=="chart" for x in envelope["card"]["body"]["elements"]),
                      "network_calls":0},ensure_ascii=False))
    return 0

if __name__=="__main__":raise SystemExit(main())