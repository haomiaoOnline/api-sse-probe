"""Deliver complete Feishu Card 2.0 probe reports, splitting only when needed.

All payloads are validated before delivery. Each page is attempted at most
once; an uncertain webhook result stops subsequent pages without retrying.
"""
import time

from lib import feishu
from lib.feishu_transport import send_card_payload, validate_feishu_settings
from lib.feishu_notifier import NoticeError
from lib.probe_notice_adapter import probe_notice
from lib.probe_group_report import build_probe_cards, source_link_from_config, report_label


class PagedDeliveryError(feishu.FeishuDeliveryError):
    """A multi-card send stopped after the specified page; no automatic retry."""

    def __init__(self, reason, cards_total, cards_sent, failed_page, attempted=None):
        self.reason = str(reason)
        self.cards_total = cards_total
        self.cards_sent = cards_sent
        self.failed_page = failed_page
        self.cards_attempted = failed_page if attempted is None else attempted
        self.outcome = ("not_attempted" if self.reason == "page_spacing_failed" else
                        "rejected" if self.reason.startswith("feishu_rejected_") else
                        "uncertain")
        super().__init__("paged_delivery_" + self.reason)

    def details(self):
        return {
            "status": "partial" if self.cards_sent else "failed",
            "cards_total": self.cards_total,
            "cards_confirmed": self.cards_sent,
            "cards_attempted": self.cards_attempted,
            "failed_page": self.failed_page,
            "failed_page_outcome": self.outcome,
            "reason": self.reason,
            "automatic_retry": False,
        }


def validate_probe_notification(settings):
    """Reject an unusable enabled notification target before business POSTs."""
    validate_feishu_settings(settings)
    if settings.get("enabled", True) is False:
        return
    style = settings.get("notification_style", "v2")
    if style not in ("v2", "auto", "legacy"):
        raise feishu.FeishuDeliveryError("notification_style_invalid")
    if style == "legacy":
        raise feishu.FeishuDeliveryError("paged_cards_require_v2")


def prepare_probe_cards(settings, summary, source, failure_cases=None,
                        slow_token_cases=None, source_config=None,
                        run_time=None, report_note="", archive_url="", card_title=""):
    """Pure preflight used by both live runs and historical report replay."""
    if not isinstance(settings, dict):
        raise feishu.FeishuDeliveryError("feishu_config_invalid")
    style = settings.get("notification_style", "v2")
    if style not in ("v2", "auto", "legacy"):
        raise feishu.FeishuDeliveryError("notification_style_invalid")
    if style == "legacy":
        raise feishu.FeishuDeliveryError("paged_cards_require_v2")

    failures = failure_cases if failure_cases is not None else []
    label = str(settings.get('keyword') or 'API拨测') + ' · ' + report_label(source, run_time)
    if report_note:
        label += " · " + str(report_note)[:32]
    try:
        notice = probe_notice(
            summary, failures, slow_token_cases or [],
            title=card_title or (str(settings.get("keyword") or "API拨测") + "｜监控报告"),
            source_url=source_link_from_config(source_config or {}),
            report_label=label + " · 共 {} 条失败/异常".format(len(failures)))
        cards=build_probe_cards(notice, failures)
        if card_title:
            for number,card in enumerate(cards,1):
                full_title=card_title + (' · {}/{}'.format(number,len(cards)) if len(cards)>1 else '')
                if len(full_title)>90 or '\n' in full_title or '\r' in full_title:
                    raise feishu.FeishuDeliveryError('archive_card_title_invalid_or_too_long')
                card['card']['header']['title']['content']=full_title
        if archive_url:
            from urllib.parse import urlsplit
            import re,json
            parsed=urlsplit(archive_url)
            if (parsed.scheme!='https' or parsed.query or parsed.fragment or parsed.port not in (None,443)
                or not parsed.hostname or not (parsed.hostname=='feishu.cn' or parsed.hostname.endswith('.feishu.cn')
                or parsed.hostname=='larksuite.com' or parsed.hostname.endswith('.larksuite.com'))
                or not re.fullmatch(r'/docx/[A-Za-z0-9_-]+',parsed.path)):
                raise feishu.FeishuDeliveryError('archive_link_invalid')
            for card in cards:
                card['card']['body']['elements'].insert(0,{'tag':'markdown',
                    'content':'**本次拨测记录：**[查看完整云文档]('+archive_url+')','margin':'0px'})
                if len(json.dumps(card,ensure_ascii=False,separators=(',',':')).encode('utf8'))>18000:
                    raise feishu.FeishuDeliveryError('archive_link_card_too_large')
        return cards
    except (NoticeError, ValueError, TypeError, KeyError) as exc:
        msg = str(exc)
        reason = ("card_capacity_exceeded" if msg in (
            "failure_item_exceeds_card_budget", "page_over_card_budget",
            "single_card_failure_list_too_large") else "card_render_failed")
        raise feishu.FeishuDeliveryError(reason) from exc


def send_probe_notice(settings, summary, results, source, outcome, opener=None,
                      failure_cases=None, slow_token_cases=None,
                      source_config=None, run_time=None, sleep_between=None,
                      report_note="", archive_url="", card_title=""):
    if not isinstance(settings, dict):
        raise feishu.FeishuDeliveryError("feishu_config_invalid")
    if settings.get("enabled", True) is False:
        return "disabled"
    cards = prepare_probe_cards(settings, summary, source, failure_cases,
                                slow_token_cases, source_config, run_time, report_note,
                                archive_url, card_title)
    confirmed = 0
    for index, payload in enumerate(cards, 1):
        if index > 1:
            # Respect rate limits, but do not retry unknown or failed messages.
            try:
                (sleep_between or time.sleep)(1)
            except Exception as exc:
                raise PagedDeliveryError("page_spacing_failed",
                                         len(cards), confirmed, index,
                                         attempted=confirmed) from exc
        try:
            send_card_payload(settings, payload, opener=opener)
        except feishu.FeishuDeliveryError as exc:
            if len(cards) == 1:
                raise
            raise PagedDeliveryError(str(exc), len(cards), confirmed, index) from exc
        confirmed += 1
    if len(cards) == 1:
        return "sent"  # Retain legacy single-card return contract.
    return {"status": "sent", "cards_total": len(cards),
            "cards_confirmed": confirmed, "cards_attempted": confirmed}