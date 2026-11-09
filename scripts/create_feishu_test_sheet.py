#!/usr/bin/env python3
"""Explicit, isolated Feishu Sheets fixture provisioning; NEVER probes SSE.

Creates a new folder and spreadsheet in the app cloud drive. Requires Feishu
Cloud Drive and Sheets app permissions. Safe demo device IDs are placeholders.
"""
import argparse
import json
import sys
from datetime import datetime,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from lib.feishu_sheet import _auth,_request,read_values,FeishuSheetError

HEAD=['序号','场景','类别','语料','判断依据（公共）','特殊判断依据','研发负责人','deviceId','响应结果']
ROWS=[HEAD,
      ['1','音乐','验证','测试播放歌曲','HTTP 200；文本回复','回复中包含“音乐”','测试','FEISHU_SAMPLE_DEVICE_NOT_REAL',''],
      ['2','提醒','验证','测试设置提醒','HTTP 200；文本回复','title是“灵犀定时任务”','测试','FEISHU_SAMPLE_DEVICE_NOT_REAL','']]


def provision(auth,folder_name,opener=None):
    access=_auth(auth,opener=opener)
    folder=_request('POST','/drive/v1/files/create_folder',
                    {'name':folder_name,'folder_token':''},bearer=access,opener=opener)
    folder_token=folder.get('token')
    if not folder_token:
        raise FeishuSheetError('feishu_folder_create_missing_token')
    spreadsheet=_request('POST','/sheets/v3/spreadsheets',
                        {'title':'api-sse-probe-九列兼容用例测试','folder_token':folder_token},
                        bearer=access,opener=opener).get('spreadsheet') or {}
    table_token=spreadsheet.get('spreadsheet_token')
    if not table_token:
        raise FeishuSheetError('feishu_spreadsheet_create_missing_token')
    meta=_request('GET','/sheets/v2/spreadsheets/'+table_token+'/metainfo',bearer=access,opener=opener)
    sheets=meta.get('sheets') or []
    if not sheets:
        raise FeishuSheetError('feishu_spreadsheet_metadata_empty')
    sheet_id=sheets[0].get('sheetId') or sheets[0].get('sheet_id')
    if not sheet_id:
        raise FeishuSheetError('feishu_sheet_id_missing')
    zone=sheet_id+'!A1:I'+str(len(ROWS))
    _request('PUT','/sheets/v2/spreadsheets/'+table_token+'/values',
             {'valueRange':{'range':zone,'values':ROWS}},bearer=access,opener=opener)
    config={'doc_url':spreadsheet.get('url') or '',
            'spreadsheet_token':table_token,'sheet_id':sheet_id,
            'range':'A1:I20','header_row':1,
            'columns':{'case_id':'序号','scene':'场景','category':'类别',
                       'content':'语料','expected_keywords':'判断依据（公共）',
                       'special_keywords':'特殊判断依据','owner':'研发负责人',
                       'deviceId':'deviceId'}}
    actual=read_values(config,auth=auth,opener=opener)
    if actual[:len(ROWS)]!=ROWS:
        raise FeishuSheetError('feishu_sheet_fixture_readback_mismatch')
    return {'folder_token':folder_token,'folder_url':folder.get('url'),
            'spreadsheet_url':spreadsheet.get('url'),
            'spreadsheet_token':table_token,'sheet_id':sheet_id,
            'readback_verified':True,'schema':'nine_columns',
            'business_posts':0,'feishu_group_messages':0,
            'source_example':{'type':'feishu','feishu':config}}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Create isolated Feishu folder + nine-column test sheet')
    parser.add_argument('--credentials-file',required=True,
                        help='Private Feishu app credentials file; never distributed')
    parser.add_argument('--folder-name',default='api-sse-probe-测试-'+datetime.now(timezone.utc).strftime('%Y%m%d'))
    args=parser.parse_args(argv)
    try:
        result=provision({'credentials_file':args.credentials_file},args.folder_name)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (FeishuSheetError,ValueError,OSError) as exc:
        print(json.dumps({'status':'failed','error_code':str(exc) if isinstance(exc,FeishuSheetError) else type(exc).__name__},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())