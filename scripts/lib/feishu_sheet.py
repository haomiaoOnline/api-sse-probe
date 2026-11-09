"""Feishu Sheets read-only case source, using official HTTPS APIs.

Only explicit feishu source identities are accepted. No redirects, no remote
messages, no private credentials in errors or report metadata.
"""
import json
import os
import re
import ssl
from urllib.parse import urlsplit, parse_qs, quote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

BASE = 'https://open.feishu.cn/open-apis'
MAX_BYTES = 4 * 1024 * 1024
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{3,128}$')
RANGE = re.compile(r'^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$')

class FeishuSheetError(RuntimeError):
    """Stable error codes only, never expose credentials or provider body."""

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def identity(config):
    if not isinstance(config, dict):
        raise FeishuSheetError('feishu_source_invalid')
    url = str(config.get('doc_url') or '').strip()
    token = str(config.get('spreadsheet_token') or '').strip()
    sheet = str(config.get('sheet_id') or '').strip()
    if url:
        parsed = urlsplit(url)
        match = re.fullmatch(r'/sheets/([A-Za-z0-9_-]+)', parsed.path)
        if parsed.scheme != 'https' or not parsed.hostname or not (
                parsed.hostname == 'feishu.cn' or parsed.hostname.endswith('.feishu.cn') or
                parsed.hostname == 'larksuite.com' or parsed.hostname.endswith('.larksuite.com')) or not match or parsed.port not in (None,443):
            raise FeishuSheetError('invalid_feishu_sheet_url')
        if token and token != match.group(1):
            raise FeishuSheetError('conflicting_spreadsheet_token')
        token = match.group(1)
        tab = (parse_qs(parsed.query).get('sheet') or [''])[0]
        if sheet and tab and sheet != tab:
            raise FeishuSheetError('conflicting_sheet_id')
        sheet = sheet or tab
    if not IDENTIFIER.fullmatch(token):
        raise FeishuSheetError('feishu_spreadsheet_token_missing')
    if sheet and not IDENTIFIER.fullmatch(sheet):
        raise FeishuSheetError('feishu_sheet_id_invalid')
    return token,sheet


def _request(method,path,payload=None,bearer=None,opener=None):
    if not path.startswith('/') or '..' in path or '?' in path and '\n' in path:
        raise FeishuSheetError('feishu_endpoint_invalid')
    body=json.dumps(payload).encode('utf8') if payload is not None else None
    headers={'Content-Type':'application/json; charset=utf-8'}
    if bearer:
        headers['Authorization']='Bearer '+bearer
    req=Request(BASE+path,data=body,headers=headers,method=method)
    opener=opener or build_opener(HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
    try:
        with opener.open(req,timeout=20) as resp:
            if resp.status != 200:
                raise FeishuSheetError('feishu_http_status_'+str(resp.status))
            raw=resp.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:
                raise FeishuSheetError('feishu_response_too_large')
            result=json.loads(raw)
    except HTTPError as exc:
        raise FeishuSheetError('feishu_http_status_'+str(exc.code)) from None
    except (URLError,TimeoutError,ValueError) as exc:
        raise FeishuSheetError('feishu_transport_'+type(exc).__name__) from None
    if not isinstance(result,dict) or type(result.get('code')) is not int:
        raise FeishuSheetError('feishu_invalid_response')
    if result['code']!=0:
        raise FeishuSheetError('feishu_api_code_'+str(result['code']))
    return result.get('data') or {}


def _auth(config,opener=None):
    if not isinstance(config,dict):
        raise FeishuSheetError('feishu_auth_invalid')
    app_id=config.get('app_id') or os.environ.get('FEISHU_APP_ID')
    app_secret=config.get('app_secret') or os.environ.get('FEISHU_APP_SECRET')
    credentials_file=config.get('credentials_file')
    if credentials_file and not (app_id and app_secret):
        # Optional user-owned Feishu app config, never copied to run snapshots.
        from pathlib import Path
        path=Path(credentials_file).expanduser()
        if path.is_symlink() or not path.is_file():
            raise FeishuSheetError('feishu_credentials_file_invalid')
        pairs={}
        for line in path.read_text(encoding='utf8').splitlines():
            delim = '：' if '：' in line else ':' if ':' in line else '=' if '=' in line else None
            if delim:
                key,value=line.split(delim,1)
                pairs[key.strip().lower()]=value.strip()
        app_id = app_id or pairs.get('appid') or pairs.get('app_id')
        app_secret = app_secret or pairs.get('appsecret') or pairs.get('app_secret')
    if not all(isinstance(v,str) and v.strip() for v in (app_id,app_secret)):
        raise FeishuSheetError('feishu_app_credentials_missing')
    # Token endpoint's top-level schema differs from other OpenAPI endpoints.
    body=json.dumps({'app_id':app_id,'app_secret':app_secret}).encode('utf8')
    req=Request(BASE+'/auth/v3/tenant_access_token/internal',data=body,
                headers={'Content-Type':'application/json'},method='POST')
    opener=opener or build_opener(HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
    try:
        with opener.open(req,timeout=20) as resp:
            data=json.loads(resp.read(65537))
    except (HTTPError,URLError,TimeoutError,ValueError) as exc:
        raise FeishuSheetError('feishu_auth_'+type(exc).__name__) from None
    if not isinstance(data,dict) or data.get('code')!=0 or not isinstance(data.get('tenant_access_token'),str):
        raise FeishuSheetError('feishu_auth_rejected')
    return data['tenant_access_token']


def read_values(config,auth=None,opener=None):
    token,sheet=identity(config)
    span=str(config.get('range') or 'A1:I500').upper()
    if not RANGE.fullmatch(span):
        raise FeishuSheetError('feishu_range_invalid')
    access=_auth(auth,opener)
    if not sheet:
        meta=_request('GET','/sheets/v2/spreadsheets/'+token+'/metainfo',bearer=access,opener=opener)
        items=meta.get('sheets') or []
        if len(items)!=1:
            raise FeishuSheetError('feishu_sheet_id_required')
        sheet=(items[0].get('sheetId') or items[0].get('sheet_id') or '')
    if not IDENTIFIER.fullmatch(str(sheet)):
        raise FeishuSheetError('feishu_sheet_id_invalid')
    path='/sheets/v2/spreadsheets/'+token+'/values/'+quote(sheet+'!'+span,safe='!:')
    data=_request('GET',path,bearer=access,opener=opener)
    result=(data.get('valueRange') or {}).get('values')
    if not isinstance(result,list):
        raise FeishuSheetError('feishu_values_missing')
    # The common read_cases/row-mapping code validates headers, columns & rows.
    return result