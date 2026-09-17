"""Private, run-bound MCP Streamable HTTP JSON transport for the Harness.

Authentication is the existing first-party worker credential plus fenced run
lease; this is not a public OAuth MCP endpoint for arbitrary external clients.
"""
import json
from typing import Any
from fastapi import Body,Depends,Request,Response
from fastapi.responses import JSONResponse
from .db import get_db
from .config import settings
from .errors import DomainError
from . import tool_gateway as tools

VERSION='2025-06-18'


def error(rid,code,message,status=200):
    return JSONResponse({'jsonrpc':'2.0','id':rid,'error':{'code':code,'message':message}},status_code=status)


def install_mcp(app,worker_auth,fence,execute_step):
    def transport(request:Request):
        worker_auth(request)
        if request.headers.get('origin') not in {None,settings().origin}:
            raise DomainError('ORIGIN_DENIED','请求来源不受信任',403)
        if request.headers.get('mcp-protocol-version',VERSION)!=VERSION:
            raise DomainError('MCP_VERSION_UNSUPPORTED','不支持的工具协议版本',400)

    path='/internal/runs/{run_id}/mcp'

    @app.get(path,dependencies=[Depends(transport)])
    def get_stream():
        return Response(status_code=405,headers={'Allow':'POST'})

    @app.post(path,dependencies=[Depends(transport)])
    def rpc(run_id:str,request:Request,data:Any=Body(...),db=Depends(get_db)):
        accept=request.headers.get('accept','')
        if 'application/json' not in accept or 'text/event-stream' not in accept:
            return error(None,-32600,'须声明 JSON 和事件流响应类型',406)
        try:
            epoch=int(request.headers['x-mold-run-epoch'])
            if epoch<1:raise ValueError()
        except (KeyError,ValueError):return error(None,-32600,'缺少有效的任务租约',400)
        run,user=fence(db,run_id,epoch)
        if not isinstance(data,dict) or data.get('jsonrpc')!='2.0' or not isinstance(data.get('method'),str):
            return error(None,-32600,'无效的工具协议请求')
        rid=data.get('id');method=data['method'];params=data.get('params',{})
        if 'id' in data and (type(rid) not in (int,str)):
            return error(None,-32600,'请求编号无效')
        if not isinstance(params,dict):return error(rid,-32602,'请求参数须为对象')
        if 'id' not in data:
            if method!='notifications/initialized':return Response(status_code=202)
            db.commit();return Response(status_code=202)
        if method=='initialize':
            if not isinstance(params.get('protocolVersion'),str) or not isinstance(params.get('capabilities'),dict) or not isinstance(params.get('clientInfo'),dict):
                return error(rid,-32602,'初始化参数不完整')
            result={'protocolVersion':VERSION,'capabilities':{'tools':{'listChanged':False}},
                    'serverInfo':{'name':'mold-business-tools','version':'0.1.0'},
                    'instructions':'仅使用当前任务所有者授权的能力；记录和协作反馈不等于审批结果。'}
        elif method=='ping':result={}
        elif method=='tools/list':
            if params.get('cursor'):return error(rid,-32602,'无效的工具分页标识')
            result={'tools':[{'name':k,'description':tools.TOOLS[k]['description'],
                'inputSchema':tools.tool_schema(k)['function']['parameters'],
                'annotations':{'readOnlyHint':not k.startswith('prepare_'),'destructiveHint':False,'idempotentHint':True}}
                for k in tools.available_tools(db,user)]}
        elif method=='tools/call':
            name=params.get('name');arguments=params.get('arguments',{});meta=params.get('_meta',{})
            if not isinstance(name,str) or not isinstance(arguments,dict) or not isinstance(meta,dict):
                return error(rid,-32602,'工具调用参数无效')
            seq=meta.get('mold/sequence')
            if type(seq) is not int or not 0<=seq<30:return error(rid,-32602,'缺少有效的执行步骤编号')
            if name not in tools.available_tools(db,user):return error(rid,-32602,'工具不存在或当前不可用')
            try:
                value=execute_step(db,run_id,{'epoch':epoch,'sequence':seq,'key':name,'arguments':arguments})
                result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],
                        'structuredContent':value,'isError':False}
            except DomainError as exc:
                db.rollback()
                tool_error={'tool_error':{'code':exc.code,'message':exc.message}}
                result={'content':[{'type':'text','text':json.dumps(tool_error,ensure_ascii=False)}],
                        'structuredContent':tool_error,'errorCode':exc.code,'isError':True}
        else:return error(rid,-32601,'不支持的工具协议方法')
        db.commit()
        return {'jsonrpc':'2.0','id':rid,'result':result}
