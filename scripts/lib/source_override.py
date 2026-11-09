"""Safe per-run Tencent Docs source override, never writes private config.

Only the spreadsheet identity is overridden; private API auth, Tencent MCP
token, case column mapping and read bounds remain configured.
"""
import re
from urllib.parse import parse_qsl, urlsplit

from lib.tencent_mcp import document_identity


class SourceOverrideError(ValueError):
    pass


def apply_tencent_source_url(source, url):
    """Return a fresh source with URL-owned document+tab, free of stale IDs.

    Only a trusted docs.qq.com HTTPS sheet URL with exactly one tab selector
    can be selected without more user context. Query tracking data is dropped.
    """
    if not isinstance(source, dict) or not isinstance(url, str):
        raise SourceOverrideError("source_url_invalid")
    url = url.strip()
    if not url or any(ch.isspace() for ch in url):
        raise SourceOverrideError("source_url_invalid")
    try:
        parsed = urlsplit(url)
        match = re.fullmatch(r"/sheet/([A-Za-z0-9_-]+)/?", parsed.path)
        if (parsed.scheme != "https" or parsed.netloc != "docs.qq.com"
                or parsed.fragment or not match):
            raise SourceOverrideError("source_url_not_trusted_tencent_sheet")
        tabs = [value for key, value in parse_qsl(
            parsed.query, keep_blank_values=True) if key == "tab"]
        if len(tabs) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", tabs[0]):
            raise SourceOverrideError("source_url_requires_single_tab")
        file_id, sheet_id = match.group(1), tabs[0]
        canonical = "https://docs.qq.com/sheet/{}?tab={}".format(file_id, sheet_id)
        previous = source.get("tencent") or {}
        if not isinstance(previous, dict):
            raise SourceOverrideError("tencent_settings_invalid")
        tencent = {key: value for key, value in previous.items()
                   if key not in ("file_id", "sheet_id", "sheet_name", "doc_url")}
        tencent.update({"doc_url": canonical, "file_id": file_id, "sheet_id": sheet_id})
        document_identity(tencent)  # Verify the selected identity without network.
        overridden = dict(source)
        overridden.pop("path", None)
        overridden["type"] = "tencent"
        overridden["tencent"] = tencent
        return overridden
    except ValueError as exc:
        if isinstance(exc, SourceOverrideError):
            raise
        raise SourceOverrideError("source_url_invalid") from exc