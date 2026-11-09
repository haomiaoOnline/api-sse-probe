"""Private JSON-v5 and Markdown file output adapter.

This adapter alone is allowed to receive the full private report. Other
adapters should use public_projection with explicit remote authorization.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.report_document import render_markdown


def _write_private(path, text):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as file:
            file.write(text)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


class LocalFileOutput:
    kind = "file"
    privacy = "private"

    def publish(self, document, context):
        settings = context.reports
        out = Path(settings.get("dir","reports"))
        if not out.is_absolute():
            out = Path(context.root) / out
        if out.is_symlink():
            raise OSError("report_directory_symlink_refused")
        out.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.name != "nt":
            os.chmod(out,0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stamp += "-" + uuid.uuid4().hex[:8]
        jsonfile = out / ("probe-" + stamp + ".json")
        mdfile = out / ("probe-" + stamp + ".md")
        _write_private(jsonfile, json.dumps(document,ensure_ascii=False,indent=2)+"\n")
        try:
            _write_private(mdfile,render_markdown(document))
        except Exception:
            jsonfile.unlink(missing_ok=True)
            raise
        return {"type":self.kind,"status":"saved",
                "json_report":str(jsonfile),"markdown_report":str(mdfile)}