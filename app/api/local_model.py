"""Model diagnostics accessible only from a loopback, same-origin local UI.
The browser cannot submit keys, endpoints, models, or arbitrary prompts.
"""
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from filelock import FileLock, Timeout as LockTimeout
from app.agents.provider import draft, ModelOutputError
from app.core.config import get_settings
from app.services.budget import BudgetExceeded

router = APIRouter(prefix="/api/v1/local-model", tags=["Local model diagnostics"])
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def local_request(request: Request):
    peer = request.client.host if request.client else ""
    try:
        host = urlsplit('http://' + request.headers.get('host','')).hostname
    except ValueError as exc:
        raise HTTPException(403,'Invalid local host') from exc
    if peer not in LOOPBACK or host not in LOOPBACK:
        raise HTTPException(403,"Model diagnostics are available only through the local loopback interface")
    origin = request.headers.get('origin')
    if origin:
        try:
            supplied = urlsplit(origin)
            expected = urlsplit(str(request.base_url))
        except ValueError as exc:
            raise HTTPException(403,"Invalid request origin") from exc
        if supplied.scheme != expected.scheme or supplied.netloc.lower() != expected.netloc.lower():
            raise HTTPException(403,"Cross-origin model diagnostics are disabled")


def usage_summary():
    cfg=get_settings();path=Path(cfg.llm_audit_path)
    if not path.exists():return {'calls_used':0,'budget_upper_cny':0}
    with FileLock(str(path)+'.lock',timeout=2):
        rows=[json.loads(x) for x in path.read_text('utf-8').splitlines()]
    reserved={r['call_id']:r['reserve_cny'] for r in rows if r['event']=='reserved'}
    settled={r['call_id']:r['charged_upper_cny'] for r in rows if r['event']=='settled'}
    return {'calls_used':len(reserved),'budget_upper_cny':round(sum(settled.get(k,v) for k,v in reserved.items()),6)}


def profile():
    cfg=get_settings()
    providers={'mock':('deterministic-template','local-process',True),
        'deepseek':(cfg.deepseek_model,'cloud-api',bool(cfg.deepseek_api_key)),
        'openai':(cfg.openai_model,'cloud-api',bool(cfg.openai_api_key)),
        'local':(cfg.local_model_name,'local-inference',urlsplit(cfg.local_model_base_url).hostname in LOOPBACK)}
    model,mode,configured=providers.get(cfg.ai_provider,('unsupported','unknown',False))
    return {'provider':cfg.ai_provider,'model':model,'mode':mode,'configured':configured,
        'calls_limit':cfg.llm_max_calls,'budget_limit_cny':cfg.llm_budget_cny,'max_output_tokens':cfg.llm_max_tokens,
        'credentials_in_browser':False,'test_prompt':'仅验证会员积分介绍的结构化输出；不会提交用户表单或客户资料。'}


@router.get('', dependencies=[Depends(local_request)])
async def status():
    result=profile()
    try:result.update(await asyncio.to_thread(usage_summary));result['audit_available']=True
    except (OSError,ValueError,LockTimeout):result.update(audit_available=False)
    return result


@router.post('/test',dependencies=[Depends(local_request)])
async def test(request: Request):
    if request.headers.get('x-local-model-test') != '1':
        raise HTTPException(403,'Explicit local test header is required')
    if await request.body():
        raise HTTPException(422,'This endpoint does not accept keys, URLs, models, or prompts')
    lock=request.app.state.local_probe_lock
    if lock.locked():raise HTTPException(429,'A connection test is already running')
    started=time.perf_counter()
    async with lock:
        try:
            async with request.app.state.model_slots:
                result=await asyncio.wait_for(draft('连接测试：介绍会员积分查询服务，不添加优惠或活动日期。','new',1),
                                               timeout=get_settings().candidate_timeout_seconds)
        except BudgetExceeded as exc:raise HTTPException(429,'Local call or budget cap reached') from exc
        except TimeoutError as exc:raise HTTPException(504,'Model test timed out; usage may still be charged') from exc
        except (httpx.RequestError,ModelOutputError,ValueError,KeyError,IndexError) as exc:
            raise HTTPException(502,'Model connection or structured-output validation failed; check local configuration') from exc
        except (OSError,LockTimeout) as exc:raise HTTPException(503,'Local budget journal unavailable') from exc
    return {'ok':True,**profile(),'elapsed_ms':round((time.perf_counter()-started)*1000),'sample':result}
