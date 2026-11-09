#!/usr/bin/env python3
"""交互式私有配置向导 + 离线体检。不联网，不打印凭据/受限 URL。"""
import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = {
    "case_id": "序号", "scene": "场景", "category": "类别",
    "content": "语料", "expected_keywords": "判断依据（公共）",
    "special_keywords": "特殊判断依据", "owner": "研发负责人",
    "deviceId": "deviceId",
}


def config_path(name):
    if (Path(name).name != name or Path(name).is_absolute() or not
            (name == "config.local.json" or
             re.fullmatch(r"config\.[a-zA-Z0-9_-]+\.local\.json", name))):
        raise ValueError("private_config_filename_invalid")
    return ROOT / name


def present(value):
    if isinstance(value, dict) and isinstance(value.get("env"), str):
        return bool(os.environ.get(value["env"], "").strip())
    return isinstance(value, str) and bool(value.strip())


def secret(prompt):
    value = getpass.getpass(prompt + "（不会回显）: ").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("required_secret_missing")
    return value


def answer(prompt, default=""):
    value = input(prompt + (" [" + default + "]" if default else "") + ": ").strip() or default
    if not value or "\n" in value or "\r" in value:
        raise ValueError("required_field_missing")
    return value


def private_create(path, data):
    if path.exists() or path.is_symlink():
        raise ValueError("private_config_exists_no_overwrite")
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as writer:
            fd = -1
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
    except BaseException:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)
        raise


def source_config(kind, url):
    opts = {"doc_url": url, "range": "A1:I500", "header_row": 1,
            "columns": dict(COLUMNS)}
    if kind == "tencent":
        from lib.tencent_mcp import document_identity
        document_identity(opts)
    else:
        from lib.feishu_sheet import identity
        identity(opts)
    return {"type": kind, kind: opts}


def configure(kind, name):
    target = config_path(name)
    if target.exists() or target.is_symlink():
        raise ValueError("private_config_exists_no_overwrite")
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf8"))
    url = secret("已授权的 HTTPS JSON→SSE API URL")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("api_url_requires_https")
    data["api"]["url"] = url
    data["api"]["headers"] = {
        "powerkey": secret("API powerkey"),
        "signature": secret("API signature（本程序不会自动计算）"),
    }
    if kind in ("csv", "json"):
        path = answer("本地用例路径", "assets/sample_cases.csv" if kind == "csv" else "")
        data["source"] = {"type": kind, "path": path}
    elif kind in ("tencent", "feishu"):
        url = secret("已授权的" + ("腾讯文档" if kind == "tencent" else "飞书表格") + "链接（包含工作表参数）")
        data["source"] = source_config(kind, url)
        if kind == "tencent":
            data["tencent_docs_auth"] = {"token": secret("腾讯文档个人 MCP Token")}
        else:
            data["feishu_docs_auth"] = {
                "app_id": secret("飞书应用 App ID"),
                "app_secret": secret("飞书应用 App Secret"),
            }
    else:
        raise ValueError("source_type_invalid")
    if kind != "feishu":
        data.pop("feishu_docs_auth", None)
    choice = answer("是否启用飞书群通知? y/N", "N").lower()
    if choice not in ("y", "n", "yes", "no"):
        raise ValueError("notification_choice_invalid")
    notify = choice in ("y", "yes")
    data["feishu"] = {"enabled": notify, "keyword": "API拨测", "notification_style": "v2"}
    if notify:
        data["feishu"].update({
            "WEBHOOK_URL": secret("群机器人 Webhook"),
            "SECRET": secret("群机器人签名 Secret"),
            "keyword": answer("群机器人关键词", "API拨测"),
        })
    data["feishu_archive"] = {"enabled": False}
    private_create(target, data)
    return target


def doctor(name="config.local.json"):
    """纯离线检查，返回代码，不包含敏感值或 URL。"""
    try:
        path = config_path(name)
    except ValueError:
        return {"status": "INCOMPLETE", "issues": ["private_config_filename_invalid"]}
    if path.is_symlink() or not path.is_file():
        return {"status": "INCOMPLETE", "issues": ["private_config_missing"]}
    try:
        config = json.loads(path.read_text(encoding="utf8"))
        if not isinstance(config, dict):
            raise ValueError()
    except (OSError, ValueError, UnicodeError):
        return {"status": "INCOMPLETE", "issues": ["invalid_json"]}
    issues = []
    if os.name != "nt" and path.stat().st_mode & 0o077:
        issues.append("private_file_permissions")
    api = config.get("api") or {}
    parts = urlsplit(str(api.get("url") or ""))
    if parts.scheme != "https" or not parts.hostname or parts.hostname in (
            "example.com", "test.example.com") or parts.username or parts.password:
        issues.append("authorized_https_api_missing")
    for key in ("powerkey", "signature"):
        if not present((api.get("headers") or {}).get(key)):
            issues.append(key + "_missing")
    source = config.get("source") or {}
    kind = source.get("type")
    if kind in ("csv", "json"):
        location = source.get("path")
        f = Path(location) if isinstance(location, str) and location else None
        if f is not None and not f.is_absolute():
            f = ROOT / f
        if f is None or not f.is_file():
            issues.append("case_file_missing")
    elif kind == "tencent":
        try:
            from lib.tencent_mcp import document_identity
            document_identity(source.get("tencent"))
        except (TypeError, ValueError, RuntimeError):
            issues.append("tencent_sheet_invalid")
        token = (config.get("tencent_docs_auth") or {}).get("token") or os.environ.get("TENCENT_DOCS_TOKEN", "")
        if not present(token):
            issues.append("tencent_token_missing")
    elif kind == "feishu":
        try:
            from lib.feishu_sheet import identity
            identity(source.get("feishu"))
        except (TypeError, ValueError, RuntimeError):
            issues.append("feishu_sheet_invalid")
        auth = config.get("feishu_docs_auth") or {}
        if not (auth.get("credentials_file") or
                ((auth.get("app_id") or os.environ.get("FEISHU_APP_ID")) and
                 (auth.get("app_secret") or os.environ.get("FEISHU_APP_SECRET")))):
            issues.append("feishu_app_auth_missing")
    else:
        issues.append("source_invalid")
    notification = config.get("feishu") or {}
    if notification.get("enabled", False):
        for key, alias in (("WEBHOOK_URL", "webhook_url"), ("SECRET", "secret")):
            if not present(notification.get(key) or notification.get(alias)):
                issues.append("notification_" + key.lower() + "_missing")
    return {"status": "READY_OFFLINE" if not issues else "INCOMPLETE",
            "source": kind if kind in ("csv", "json", "tencent", "feishu") else "unknown",
            "notification": "enabled" if notification.get("enabled", False) else "disabled",
            "issues": issues, "network_requests": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线安全配置向导（不发送真实请求）")
    parser.add_argument("action", choices=("init", "doctor"))
    parser.add_argument("--source", choices=("csv", "json", "tencent", "feishu"), default="csv")
    parser.add_argument("--config", default="config.local.json")
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            if not sys.stdin.isatty():
                print('{"status":"NEEDS_TERMINAL","hint":"请在本机交互式终端输入，不要把密钥发到聊天"}', file=sys.stderr)
                return 2
            configure(args.source, args.config)
        result = doctor(args.config)
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        reason = str(exc) if str(exc) in (
            "private_config_exists_no_overwrite", "private_config_filename_invalid",
            "api_url_requires_https", "required_secret_missing", "required_field_missing",
            "notification_choice_invalid", "source_type_invalid") else type(exc).__name__
        print(json.dumps({"status": "ERROR", "reason": reason}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "READY_OFFLINE" else 1


if __name__ == "__main__":
    sys.exit(main())