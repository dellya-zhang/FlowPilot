"""Three actual HTTP service processes + independent budget contenders. Mock only."""
import asyncio
import json
import os
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx
from extreme_http import port,launch,stop,ready,wait_status,ROOT


async def main():
    result={'provider':'mock','paid_calls':0,'independent_service_processes':3,'scenarios':{}}
    with tempfile.TemporaryDirectory() as work:
        work=Path(work)
        env={**os.environ,'DATABASE_URL':'sqlite+aiosqlite:///'+str(work/'shared.db'),
            'ARTIFACT_DIR':str(work/'objects'),'AI_PROVIDER':'mock','REDIS_URL':'','S3_ENDPOINT_URL':'',
            'ACCESS_TOKEN':'','LEASE_SECONDS':'2','RECOVERY_POLL_SECONDS':'.2',
            'LLM_AUDIT_PATH':str(work/'no-real-model.jsonl')}
        ports=[port() for _ in range(3)];processes=[launch(env,p,slow=i==0) for i,p in enumerate(ports)]
        clients=[httpx.AsyncClient(trust_env=False,base_url=f'http://127.0.0.1:{p}',timeout=20) for p in ports]
        try:
            await asyncio.gather(*(ready(c,p) for c,p in zip(clients,processes)))
            with closing(sqlite3.connect(work/'shared.db')) as db:assert db.execute('select count(*) from customers').fetchone()[0]==500
            result['scenarios']['simultaneous_bootstrap']='three servers ready, exactly 500 seeded customers'
            r=await clients[0].post('/api/v1/campaigns',json={'brief':'多进程崩溃接管验证','budget':1000})
            path='/api/v1/campaigns/'+r.json()['id'];assert (await clients[0].post(path+'/generate')).status_code==202
            async with asyncio.timeout(10):
                while True:
                    ev=(await clients[1].get(path+'/events')).json()
                    if any(x['name']=='generation.started' for x in ev):break
                    await asyncio.sleep(.05)
            await asyncio.sleep(2.5) # Exceeds TTL: ownership must survive through renewal.
            ev=(await clients[1].get(path+'/events')).json()
            assert sum(x['name']=='generation.started' for x in ev)==1
            assert (await clients[1].get(path)).json()['status']=='GENERATING'
            result['scenarios']['active_lease_renewal']='one owner after waiting longer than lease TTL'
            killed_at=time.monotonic();stop(processes[0])
            await wait_status(clients[1],path,'WAITING_APPROVAL')
            ev=(await clients[1].get(path+'/events')).json();cs=(await clients[2].get(path+'/candidates')).json()
            starts=[x for x in ev if x['name']=='generation.started']
            assert len(starts)==2 and len({x['payload']['owner'] for x in starts})==2
            assert len(cs)==3 and len({x['variant'] for x in cs})==3
            assert sum(x['name']=='generation.finished' for x in ev)==1
            result['scenarios']['kill_and_takeover']={'started_owners':2,'completed_executions':1,'unique_candidates':3,
                'recovery_seconds':round(time.monotonic()-killed_at,3)}
            c=clients[1];r=await c.post('/api/v1/campaigns',json={'brief':'跨进程请求竞争验证','budget':1000});path='/api/v1/campaigns/'+r.json()['id']
            rs=await asyncio.gather(*(clients[1+i%2].post(path+'/generate') for i in range(60)))
            assert sum(r.status_code==202 for r in rs)==1
            assert all(r.status_code in (202,409,429) for r in rs)
            await wait_status(c,path,'WAITING_APPROVAL');cs=(await c.get(path+'/candidates')).json()
            rs=await asyncio.gather(*(clients[1+i%2].post(path+'/approve',json={'candidate_id':cs[i%3]['id']}) for i in range(60)))
            assert sum(r.status_code==200 for r in rs)==1
            assert all(r.status_code in (200,409) for r in rs)
            rs=await asyncio.gather(*(clients[1+i%2].post(path+'/pilot') for i in range(60)))
            assert all(r.status_code==200 for r in rs),[r.status_code for r in rs]
            assert len({r.json()['id'] for r in rs})==1
            result['scenarios']['cross_process_requests']={'generate_requests':60,'accepted_generation':1,
                'approve_requests':60,'accepted_approval':1,'export_requests':60,'unique_artifacts':1}
            assert not Path(env['LLM_AUDIT_PATH']).exists()
        finally:
            for c in clients:await c.aclose()
            for p in processes:stop(p)
        # Real OS processes race for two slots in a shared journal. No network used.
        gate=work/'gate';journal=work/'budget.jsonl'
        code='''import os,time,json,sys
from pathlib import Path
from app.services.budget import reserve_call,settle_call,BudgetExceeded
while not Path(sys.argv[1]).exists():time.sleep(.01)
try:
 call_id=reserve_call(sys.argv[2],.1,"mock",2,10)
 settle_call(sys.argv[2],{'event':'settled','call_id':call_id,'charged_upper_cny':.01})
 print('accepted',flush=True)
except BudgetExceeded:print('rejected',flush=True)
'''
        contenders=[subprocess.Popen([sys.executable,'-c',code,str(gate),str(journal)],cwd=ROOT,env=env,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(8)]
        try:
            gate.write_text('go')
            outcomes=[]
            for p in contenders:
                out,err=await asyncio.to_thread(p.communicate,timeout=20)
                assert p.returncode==0,err
                outcomes.append(out.strip())
            assert outcomes.count('accepted')==2 and outcomes.count('rejected')==6,outcomes
            rows=[json.loads(x) for x in journal.read_text('utf-8').splitlines()]
            assert len(rows)==4
            result['scenarios']['shared_budget_processes']={'processes':8,'accepted':2,'rejected':6,'journal_events':4}
        finally:
            for p in contenders:stop(p)
    (ROOT/'docs/multiprocess-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
