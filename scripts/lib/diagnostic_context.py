"""Safe local-report projection of private HTTP request/response context."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_NAMES = (
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "powerkey", "signature", "token", "access_token", "refresh_token",
    "secret", "password", "api_key", "apikey", "client_secret",
)


def is_secret_name(name):
    normalized = str(name or "").strip().lower().replace("-", "_")
    return any(normalized == item.replace("-", "_") or
               normalized.endswith("_" + item.replace("-", "_"))
               for item in _SECRET_NAMES)


def header_pairs(headers):
    if headers is None:
        return []
    raw_items = getattr(headers, "raw_items", None)
    items = raw_items() if callable(raw_items) else (
        headers.items() if hasattr(headers, "items") else headers)
    return [[str(name), str(value)] for name, value in items]


def safe_headers(headers):
    return [[name, "[REDACTED]" if is_secret_name(name) else value]
            for name, value in header_pairs(headers)]


def safe_url(url):
    try:
        parts = urlsplit(str(url or ""))
        query = [(key, "[REDACTED]" if is_secret_name(key) else value)
                 for key, value in parse_qsl(parts.query, keep_blank_values=True)]
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        if parts.port:
            host += ":" + str(parts.port)
        return urlunsplit((parts.scheme, host, parts.path,
                           urlencode(query, doseq=True), ""))
    except (TypeError, ValueError):
        return "[URL unavailable]"


def safe_context(context):
    context = context if isinstance(context, dict) else {}
    request = context.get("request") or {}
    response = context.get("response") or {}
    return {
        "request_method": request.get("method"),
        "request_url": safe_url(request.get("url")),
        "request_headers": safe_headers(request.get("headers") or []),
        "request_body_bytes": request.get("body_bytes"),
        "request_body_sha256": request.get("body_sha256"),
        "device_id": request.get("device_id"),
        "req_id": request.get("req_id"),
        "response_status": response.get("status"),
        "response_headers": safe_headers(response.get("headers") or []),
        "response_content_type": response.get("content_type"),
        "response_headers_received": bool(response.get("headers_received")),
    }