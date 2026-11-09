"""One Feishu Docx per completed immutable probe run; shareable view-only.

Published pages contain sanitized troubleshooting content. The exact private
SSE bytes stay in evidence/ and never become an anyone-with-link disclosure.
The per-run ledger is written before creating cloud objects, with no blind
retry after an uncertain write.
"""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lib.v2_repository import _atomic_private
from lib.feishu_sheet import _auth, _request

class ArchiveError(RuntimeError):
    pass

HOST = 'lcn0xo7qynjm.feishu.cn'  # default for this installation; override via private config
REDACT = re.compile(
    r'(?i)bearer\s+[A-Za-z0-9._~+/=-]+'
    r'|(?P<key>["\']?(?:powerkey|api[_-]?key|access[_-]?token|app[_-]?secret|authorization|deviceid|reqid|signature|open_id|user_id|sessionid|phone|mobile|timestamp)["\']?\s*[=:]\s*)(?P<value>["\'][^"\']*["\']|[^\s,;}}]+)',
    re.I)
MAX_PUBLIC_BYTES = 1200000
MAX_BLOCK_CHARS = 1500


def sanitized(value):
    def strip(match):
        return (match.group('key') or '') + '"[REDACTED]"' if match.group('key') else '[REDACTED]'
    clean=re.sub(r'(?i)(authorization\s*[:=]\s*)bearer\s+[^\s,;]+',r'\1[REDACTED]',str(value))
    clean=REDACT.sub(strip,clean)
    clean=re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)','[PHONE_REDACTED]',clean)
    clean=re.sub(r'(?i)\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,200}\.[A-Za-z]{2,24}\b','[EMAIL_REDACTED]',clean)
    return clean


def _block(text):
    return {'block_type':2,'text':{'elements':[{'text_run':{'content':str(text)}}]}}


def _heading(text, level=2):
    field={1:'heading1',2:'heading2',3:'heading3'}[level]
    return {'block_type':level+2,field:{'elements':[{'text_run':{'content':str(text)}}]}}


def _code(text):
    """Native Feishu Docx code block; chunks are consecutive, never truncated."""
    return {'block_type':14,'code':{'elements':[{'text_run':{'content':str(text)}}]}}


def _content_of(block):
    field={2:'text',3:'heading1',4:'heading2',5:'heading3',14:'code'}.get(block['block_type'])
    if not field:
        raise ArchiveError('archive_unrecognized_block_type')
    return block[field]['elements'][0]['text_run']['content']


def source_labels(source,cfg):
    """Use an explicitly configured document title, never invent user titles."""
    settings=cfg.get('feishu_archive') or {}
    info=source.get(source.get('type')) or {}
    if source.get('type')=='tencent':
        from lib.tencent_mcp import document_identity,resolve_token,call_tool
        file_id,sheet_id=document_identity(info)
        title=(settings.get('document_titles') or {}).get(file_id)
        if not isinstance(title,str) or not title.strip():
            title='腾讯文档-'+file_id  # explicit fallback; never claims to know source title
        try:
            metadata=call_tool(resolve_token(cfg.get('tencent_docs_auth')),
                               'sheet.get_sheet_info',{'file_id':file_id})
            sheet=next((x.get('sheet_name') or x.get('title') or x.get('name')
                        for x in metadata.get('sheets',[]) if x.get('sheet_id')==sheet_id),None)
        except Exception:
            sheet=None
        if not isinstance(sheet,str) or not sheet.strip():
            sheet=(settings.get('sheet_titles') or {}).get(sheet_id)
        if not isinstance(sheet,str) or not sheet.strip():
            raise ArchiveError('archive_sheet_title_unavailable')
        return str(title),str(sheet)
    if source.get('type')=='feishu':
        from lib.feishu_sheet import identity
        token,sheet_id=identity(info)
        title=(settings.get('document_titles') or {}).get(token) or token
        try:
            access=_auth(cfg.get('feishu_docs_auth'))
            info_data=_request('GET','/sheets/v2/spreadsheets/'+token+'/metainfo',bearer=access)
            options=info_data.get('sheets') or []
            tab=next((x.get('title') or x.get('sheet_name') or x.get('name')
                      for x in options if (x.get('sheetId') or x.get('sheet_id'))==sheet_id),None)
            if isinstance(info_data.get('title'),str) and info_data['title']:
                title=info_data['title']
        except Exception:
            tab=None
        if not tab:
            tab=(settings.get('sheet_titles') or {}).get(sheet_id)
        if not tab:raise ArchiveError('archive_sheet_title_unavailable')
        return str(title),str(tab)
    return 'api-sse-probe',str(source.get('type') or 'data')


def notification_title(source_label,sheet_label):
    return '{}-{} 丨 拨测报告'.format(source_label,sheet_label)


def archive_title(source,source_label,sheet_label,now=None):
    name='{}-{}'.format(source_label,sheet_label).strip('-')
    date=(now or datetime.now(timezone(timedelta(hours=8)))).strftime('%Y%m%d-%H%M%S')
    return '{} 丨 拨测报告丨{}'.format(name[:75],date)


def render_blocks(store,doc,title,partial=False):
    """Overview → failure/error cases → success cases, with native code blocks."""
    snap=store.snapshot()
    cases={str(c['case_id']):c for c in snap['payload']['cases']}
    evidence={str(row[1]):row for row in store.rows()}
    summary=doc['summary']
    blocks=[
        _heading('一、运行概览',1),
        _block('报告名称：{}\n运行 ID：{}\n拨测总数：{}\n通过：{}  失败：{}  异常：{}\n通过率：{}%'.format(
            title,snap['run_id'],summary['total'],summary['pass'],summary['fail'],
            summary['error'],summary['pass_rate'])),
        _block('说明：共享文档包含脱敏后的完整已接收 SSE 响应；本地 evidence/ 保留无损原始字节，按用例编号与 SHA-256 可核验。'),
        _block('UNKNOWN 自动重新拨测：{} 次；涉及用例：{}。原始请求及新请求分别留在私有 SQLite 重试账本中。'.format(
            len(doc.get('automatic_retry_history') or []),
            ', '.join('#'+str(x['case_id']) for x in (doc.get('automatic_retry_history') or [])) or '无')),
        _heading('二、拨测完整信息',1),
    ]
    if partial:
        unknown_ids=[str(cid) for cid in doc.get('unknown_case_ids',[])]
        pending_ids=[str(cid) for cid in doc.get('pending_case_ids',[])]
        if not unknown_ids and not pending_ids:
            raise ArchiveError('partial_archive_must_have_unresolved_cases')
        # Strong, explicit incomplete marker; UNKNOWN has no real response.
        blocks.insert(2,_block('⚠ 未完成拨测（非正式全量结果）。冻结用例：{}；已保存响应：{}；UNKNOWN：{}；待执行：{}。不能把 UNKNOWN 视为失败、成功或已收到响应。'.format(
            len(doc['results'])+len(unknown_ids)+len(pending_ids),len(doc['results']),
            ', '.join('#'+x for x in unknown_ids) or '无',
            ', '.join('#'+x for x in pending_ids) or '无')))
        idx=next(i for i,b in enumerate(blocks) if _content_of(b)=='二、拨测完整信息')+1
        blocks[idx:idx]=[_heading('未决用例列表（{} 条）'.format(len(unknown_ids)+len(pending_ids)),2)] + [
            _block('用例 #{} · UNKNOWN / 无可核实的响应证据，禁止伪造断言结果'.format(cid))
            for cid in unknown_ids] + [
            _block('用例 #{} · PENDING / 尚未发起请求'.format(cid)) for cid in pending_ids]
    failures=[r for r in doc['results'] if r['status']!='PASS']
    passes=[r for r in doc['results'] if r['status']=='PASS']
    if len(failures)+len(passes)!=len(doc['results']):
        raise ArchiveError('archive_result_count_mismatch')
    for heading,group in [('失败用例列表',failures),('成功用例列表',passes)]:
        blocks.append(_heading('{}（{} 条）'.format(heading,len(group)),2))
        if not group:
            blocks.append(_block('无'))
        for result in group:
            cid=str(result['case_id'])
            if cid not in cases or cid not in evidence:
                raise ArchiveError('archive_evidence_or_case_missing')
            case=cases[cid]
            envelope,raw=store.evidence(evidence[cid])
            blocks.append(_heading('用例 #{} · {}'.format(cid,result['status']),3))
            blocks.append(_block('序号：{}\n测试语料：{}'.format(
                cid,sanitized(case.get('content') or '（空）'))))
            reason=sanitized(result.get('reason') or '未提供原因')
            missing=result.get('missing_keywords') or []
            forbidden=result.get('forbidden_found') or []
            if result['status']!='PASS':
                detail='失败原因：{}'.format(reason)
                if missing:
                    detail+='\n未满足条件：'+sanitized('；'.join(map(str,missing)))
                if forbidden:
                    detail+='\n触发禁止项：'+sanitized('；'.join(map(str,forbidden)))
                blocks.append(_block(detail))
            else:
                blocks.append(_block('判定：PASS'))
            blocks.append(_block('HTTP：{}  |  首业务帧：{}ms  |  总耗时：{}ms\n原始证据 SHA-256：{}'.format(
                result.get('http_status'),result.get('first_token_ms'),
                result.get('duration_ms'),hashlib.sha256(raw).hexdigest())))
            blocks.append(_block('完整响应信息（脱敏；下方为独立代码块）'))
            # Keep every received byte representable: non-UTF8 uses Base64.
            try:
                body=sanitized(raw.decode('utf8'))
            except UnicodeDecodeError:
                import base64
                body='[非 UTF-8 原始字节的 Base64]\n'+base64.b64encode(raw).decode('ascii')
            if not body:
                body='（未接收到响应体，0 字节）'
            for start in range(0,len(body),MAX_BLOCK_CHARS):
                blocks.append(_code(body[start:start+MAX_BLOCK_CHARS]))
    payload=json.dumps(blocks,ensure_ascii=False).encode('utf8')
    if len(payload)>MAX_PUBLIC_BYTES:
        raise ArchiveError('archive_too_large_no_partial_cloud_document')
    return blocks


def _archive_folder(folder_file,access,domain=HOST):
    path=Path(folder_file)
    if path.is_symlink():raise ArchiveError('archive_folder_ledger_symlink')
    if path.exists():
        record=json.loads(path.read_text())
        if not re.fullmatch('[A-Za-z0-9_-]{6,128}',record.get('folder_token','')):
            raise ArchiveError('archive_folder_token_invalid')
        if record.get('domain',domain)!=domain:
            raise ArchiveError('archive_folder_domain_mismatch')
        return record
    # Single initial folder. Unknown request outcomes require inspection;
    # never create more folders by retrying an existing intent.
    intent=path.with_suffix('.intent.json')
    if intent.exists():raise ArchiveError('archive_folder_creation_unknown')
    path.parent.mkdir(parents=True,exist_ok=True)
    _atomic_private(intent,b'{"state":"DISPATCH_INTENT"}\n')
    response=_request('POST','/drive/v1/files/create_folder',
                      {'name':'API拨测报告归档','folder_token':''},bearer=access)
    token=response.get('token')
    if not isinstance(token,str) or not token:
        raise ArchiveError('archive_folder_token_missing')
    record={'folder_token':token,'folder_url':'https://{}/drive/folder/{}'.format(domain,token),'domain':domain}
    _atomic_private(path,(json.dumps(record,ensure_ascii=False)+'\n').encode())
    intent.unlink()
    return record


def _permission(doc_id,access):
    route='/drive/v1/permissions/{}/public?type=docx'.format(doc_id)
    _request('PATCH',route,{'link_share_entity':'anyone_readable','external_access':True},bearer=access)
    info=_request('GET',route,bearer=access).get('permission_public') or {}
    if info.get('link_share_entity')!='anyone_readable':
        raise ArchiveError('archive_view_only_permission_unverified')
    return True


def publish(store,cfg,doc,source_label,sheet_label,partial=False):
    settings=cfg.get('feishu_archive') or {}
    if settings.get('enabled') is not True:return {'status':'DISABLED'}
    domain=settings.get('domain',HOST)
    if not isinstance(domain,str) or not re.fullmatch(r'[a-z0-9-]+\.(?:feishu\.cn|larksuite\.com)',domain):
        raise ArchiveError('archive_domain_invalid')
    if not partial and (store.plan()['unknown'] or store.plan()['pending']):
        return {'status':'BLOCKED_INCOMPLETE'}
    if partial and not (store.plan()['unknown'] or store.plan()['pending']):
        raise ArchiveError('partial_archive_requires_incomplete_run')
    if not cfg.get('feishu_docs_auth'):
        raise ArchiveError('feishu_doc_credentials_required')
    title=archive_title(store.snapshot()['payload']['source'],source_label,sheet_label)
    if partial:
        title += '（未完成）'
    blocks=render_blocks(store,doc,title,partial=partial)  # fail size before cloud creation
    suffix='-partial' if partial else ''
    ledger=store.directory/('feishu-archive'+suffix+'-ledger.json')
    verified=store.directory/('feishu-archive'+suffix+'-verified.json')
    if verified.exists():
        result=json.loads(verified.read_text())
        if result.get('status')!='VERIFIED':raise ArchiveError('archive_invalid_verified_ledger')
        access=_auth(cfg['feishu_docs_auth'])
        actual=_request('GET','/drive/v1/permissions/{}/public?type=docx'.format(result['document_id']),bearer=access)
        if (actual.get('permission_public') or {}).get('link_share_entity')!='anyone_readable':
            raise ArchiveError('archive_read_only_permission_changed')
        return result
    if ledger.exists():
        raise ArchiveError('archive_prior_attempt_requires_manual_reconciliation')
    access=_auth(cfg['feishu_docs_auth'])
    folder_file=Path(cfg.get('reports',{}).get('dir','reports'))/'feishu-archive-folder.json'
    if not folder_file.is_absolute():
        from pathlib import Path as P
        folder_file=P(__file__).resolve().parents[2]/folder_file
    folder=_archive_folder(folder_file,access,domain)
    _atomic_private(ledger,(json.dumps({'status':'DISPATCH_INTENT','title':title},ensure_ascii=False)+'\n').encode())
    created=_request('POST','/docx/v1/documents',{'title':title,'folder_token':folder['folder_token']},bearer=access)
    doc_id=(created.get('document') or {}).get('document_id')
    if not re.fullmatch('[A-Za-z0-9_-]{6,128}',doc_id or ''):
        raise ArchiveError('archive_create_document_id_missing')
    # Persist doc identity at once so failures can be manually repaired.
    pending=store.directory/('feishu-archive'+suffix+'-identity.json')
    _atomic_private(pending,(json.dumps({'doc_id':doc_id,'title':title,'folder_token':folder['folder_token']},ensure_ascii=False)+'\n').encode())
    import time
    for start in range(0,len(blocks),40):
        if start:time.sleep(0.4)
        _request('POST','/docx/v1/documents/{}/blocks/{}/children'.format(doc_id,doc_id),
                 {'children':blocks[start:start+40]},bearer=access)
    content=_request('GET','/docx/v1/documents/{}/raw_content'.format(doc_id),bearer=access)
    received=content.get('content')
    if not isinstance(received,str) or doc['run_id'] not in received or '用例 #' not in received:
        raise ArchiveError('archive_content_readback_failed')
    if sum('用例 #{} · '.format(r['case_id']) in received for r in doc['results'])!=len(doc['results']):
        raise ArchiveError('archive_case_count_readback_mismatch')
    for block in blocks:
        content_block=_content_of(block)
        if content_block not in received:
            raise ArchiveError('archive_full_content_readback_mismatch')
    _permission(doc_id,access)
    result={'status':'VERIFIED','report_scope':'INCOMPLETE' if partial else 'COMPLETE','title':title,'url':'https://{}/docx/{}'.format(domain,doc_id),
            'document_id':doc_id,'folder_url':folder['folder_url'], 'cases':len(doc['results']),
            'view_only_confirmed':True}
    # Keep the dispatch ledger immutable and write a separate completion record.
    _atomic_private(verified,
                    (json.dumps(result,ensure_ascii=False)+'\n').encode())
    return result