"""Reusable, provider-specific Feishu webhook transport (no business model).

Shared by legacy Markdown cards and experimental JSON 2.0 cards.
Safely signs, validates and sends one payload; no implicit retry.
"""
import base64
import hashlib
import hmac
import json
import re
import ssl
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

class FeishuDeliveryError(RuntimeError):
    pass

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def _required(value, label):
    if isinstance(value, dict):
        import os
        value = os.environ.get(str(value.get("env") or ""), "")
    if not isinstance(value, str) or not value.strip():
        raise FeishuDeliveryError(label + "_missing")
    return value.strip()

def _webhook(raw):
    value = _required(raw, "webhook_url")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise FeishuDeliveryError("invalid_webhook_url") from exc
    if (url.scheme != "https" or url.hostname not in
            {"open.feishu.cn", "open.larksuite.com"} or port not in (None, 443)
            or url.username or url.password or url.fragment or url.query
            or not re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]+", url.path)):
        raise FeishuDeliveryError("invalid_webhook_url")
    return value

def generate_sign(secret, timestamp):
    """HMAC_SHA256(key=seconds + newline + secret, data=empty), base64."""
    key = (str(timestamp) + "\n" + secret).encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")

def validate_feishu_settings(settings):
    """Validate the configured delivery target without sending a message."""
    if not isinstance(settings, dict):
        raise FeishuDeliveryError("feishu_config_invalid")
    if settings.get("enabled", True) is False:
        return
    webhook = _webhook(settings.get("WEBHOOK_URL") or settings.get("webhook_url", ""))
    secret = _required(settings.get("SECRET") or settings.get("secret", ""), "secret")
    keyword = settings.get("keyword", "API拨测")
    if not isinstance(keyword, str) or len(keyword) > 100:
        raise FeishuDeliveryError("keyword_invalid")
    try:
        timeout = float(settings.get("timeout_seconds", 10))
    except (TypeError, ValueError) as exc:
        raise FeishuDeliveryError("notification_timeout_invalid") from exc
    if not 0 < timeout <= 60:
        raise FeishuDeliveryError("notification_timeout_invalid")
    return webhook, secret, timeout

def send_card_payload(settings, payload, opener=None):
    """Post a pre-rendered interactive card for any Skill.

    Authentication, TLS, timeout, payload-size and webhook response checks
    use the exact same path as the legacy notification.
    """
    if not isinstance(settings, dict):
        raise FeishuDeliveryError("feishu_config_invalid")
    if settings.get("enabled", True) is False:
        return "disabled"
    webhook, secret, timeout = validate_feishu_settings(settings)
    if not isinstance(payload, dict) or payload.get("msg_type")!="interactive" or not isinstance(payload.get("card"),dict):
        raise FeishuDeliveryError("card_payload_invalid")
    # Caller owns only card content; don't trust an existing signature.
    data_obj = {"msg_type":"interactive", "card":payload["card"]}
    timestamp = str(int(time.time()))
    data_obj["timestamp"] = timestamp
    data_obj["sign"] = generate_sign(secret, timestamp)
    data = json.dumps(data_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > 20000:
        raise FeishuDeliveryError("notification_payload_too_large")
    req = Request(webhook, data=data, headers={"Content-Type": "application/json"},
                  method="POST")
    if opener is None:
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                raise FeishuDeliveryError("notification_http_error")
            blob = resp.read(65537)
            if len(blob) > 65536:
                raise FeishuDeliveryError("notification_response_too_large")
            result = json.loads(blob.decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError, ssl.SSLError,
            ValueError, UnicodeError) as exc:
        raise FeishuDeliveryError("notification_transport_or_response_error") from exc
    if not isinstance(result, dict):
        raise FeishuDeliveryError("notification_response_invalid")
    code = result.get("code", result.get("StatusCode"))
    if code != 0:
        raise FeishuDeliveryError("feishu_rejected_" + (
            str(code) if isinstance(code, int) else "unknown"))
    return "sent"