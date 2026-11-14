"""Offline real-HTTP burst and kill/restart test. Forces mock, no paid calls."""
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def launch(env,p,slow=False):
    code='import uvicorn; uvicorn.run("app.main:app", host="127.0.0.1", port='+str(p)+',log_level="error")'
    if slow:
        code='''import asyncio
from app.agents.marketing import marketing_graph
async def blocked(state):
    await asyncio.sleep(300)
marketing_graph.ainvoke=blocked
'''+code
    return subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def stop(p):
    if p.poll() is None:
        subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) if os.name == 'nt' else p.kill()
        p.wait(timeout=10)


async def ready(client,p):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if p.poll() is not None:raise RuntimeError('Server exited')
        try:
            if (await client.get('/api/v1/health')).status_code==200:return
        except httpx.TransportError:pass
        await asyncio.sleep(.05)
    raise TimeoutError('Server startup timed out')


async def wait_status(c,path,status):
    async with asyncio.timeout(15):
        while True:
            r=await c.get(path)
            if r.json()['status']==status:return
            await asyncio.sleep(.03)


async def main():
    with tempfile.TemporaryDirectory() as work:
        env={**os.environ,'DATABASE_URL':'sqlite+aiosqlite:///'+str(Path(work)/'http.db'),
             'ARTIFACT_DIR':str(Path(work)/'files'),'AI_PROVIDER':'mock','REDIS_URL':'','S3_ENDPOINT_URL':'',
             'ACCESS_TOKEN':'','LLM_AUDIT_PATH':str(Path(work)/'must-not-exist.jsonl')}
        number=port();process=launch(env,number)
        result={'provider':'mock','paid_calls':0,'transport':'real TCP/HTTP, uvicorn, Windows, SQLite','scenarios':{}}
        try:
            async with httpx.AsyncClient(trust_env=False,base_url=f'http://127.0.0.1:{number}',timeout=30,
                    limits=httpx.Limits(max_connections=32,max_keepalive_connections=32)) as c:
                await ready(c,process)
                row=(await c.post('/api/v1/campaigns',json={'brief':'本地网络工况会员介绍','budget':1000})).json()
                path='/api/v1/campaigns/'+row['id']
                rs=await asyncio.gather(*(c.post(path+'/generate') for _ in range(50)))
                assert sum(r.status_code==202 for r in rs)==1
                assert all(r.status_code in (202,409,429) for r in rs)
                result['scenarios']['generate_50']={str(k):sum(r.status_code==k for r in rs) for k in (202,409,429)}
                await wait_status(c,path,'WAITING_APPROVAL')
                candidate=(await c.get(path+'/candidates')).json()[0]['id']
                rs=await asyncio.gather(*(c.post(path+'/approve',json={'candidate_id':candidate}) for _ in range(50)))
                assert sum(r.status_code==200 for r in rs)==1
                assert all(r.status_code in (200,409) for r in rs)
                result['scenarios']['approve_50']={'accepted':1,'conflicts':49}
                rs=await asyncio.gather(*(c.post(path+'/pilot') for _ in range(50)))
                assert all(r.status_code==200 for r in rs)
                assert len({r.json()['id'] for r in rs})==1
                a=rs[0].json();blob=(await c.get('/api/v1/artifacts/'+a['id']+'/download')).content
                assert hashlib.sha256(blob).hexdigest()==a['sha256']
                result['scenarios']['export_50']={'success':50,'unique_artifacts':1,'sha256_valid':True}
                async def stream_once(i):
                    async with c.stream('GET',path+'/stream') as response:
                        assert response.status_code==200
                        async for line in response.aiter_lines():
                            if line.startswith('id:'):return
                await asyncio.wait_for(asyncio.gather(*(stream_once(i) for i in range(40))),15)
                result['scenarios']['sse_disconnect_40']='40 connections received event then disconnected'
                times=[];statuses=[]
                async def read(i):
                    t=time.perf_counter();r=await c.get(path if i%2 else '/api/v1/health')
                    times.append((time.perf_counter()-t)*1000);statuses.append(r.status_code)
                start=time.perf_counter();await asyncio.gather(*(read(i) for i in range(500)));elapsed=time.perf_counter()-start
                assert set(statuses)=={200}
                ordered=sorted(times)
                result['scenarios']['read_burst_500']={'success':500,'max_connections':32,'wall_seconds':round(elapsed,3),
                    'client_p50_ms':round(ordered[249],2),'client_p95_ms':round(ordered[474],2),
                    'note':'Includes client connection-pool waiting; finite local burst, not production QPS capacity.'}
        finally:stop(process)
        # Start a worker that remains blocked, kill the actual OS process, restart normally.
        number=port();process=launch(env,number,True)
        try:
            async with httpx.AsyncClient(trust_env=False,base_url=f'http://127.0.0.1:{number}',timeout=5) as c:
                await ready(c,process)
                row=(await c.post('/api/v1/campaigns',json={'brief':'进程强杀后的恢复验证','budget':1000})).json()
                path='/api/v1/campaigns/'+row['id'];await c.post(path+'/generate')
                async with asyncio.timeout(5):
                    while not any(x['name']=='generation.started' for x in (await c.get(path+'/events')).json()):
                        await asyncio.sleep(.03)
        finally:stop(process)
        process=launch(env,number)
        try:
            async with httpx.AsyncClient(trust_env=False,base_url=f'http://127.0.0.1:{number}',timeout=5) as c:
                await ready(c,process);await wait_status(c,path,'WAITING_APPROVAL')
                cs=(await c.get(path+'/candidates')).json()
                assert len(cs)==3 and len({x['variant'] for x in cs})==3
                result['scenarios']['process_kill_restart']={'status':'WAITING_APPROVAL','unique_variants':3}
        finally:stop(process)
        assert not Path(env['LLM_AUDIT_PATH']).exists()
        (ROOT/'docs/extreme-http-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
