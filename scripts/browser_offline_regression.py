"""Offline browser regression. Requires playwright + local Edge in this interpreter."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import httpx

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'AI_PROVIDER':'mock','DATABASE_URL':'sqlite+aiosqlite:///'+str(Path(temp)/'browser.db'),
         'ARTIFACT_DIR':str(Path(temp)/'files'),'REDIS_URL':'','S3_ENDPOINT_URL':'','ACCESS_TOKEN':''}
    python=root/'.venv'/'Scripts'/'python.exe' if os.name=='nt' else root/'.venv/bin/python'
    process=subprocess.Popen([str(python),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],
                              cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+20
        with httpx.Client(trust_env=False,timeout=1) as client:
            while True:
                try:
                    if client.get(f'http://127.0.0.1:{port}/api/v1/health').status_code==200:break
                except httpx.TransportError:pass
                if time.monotonic()>deadline:raise TimeoutError('Browser test startup')
                time.sleep(.05)
        script=root/'scripts/browser_e2e.py'
        content=script.read_text('utf-8').replace('127.0.0.1:8017',f'127.0.0.1:{port}')
        content=content.replace(' browser.close()', ''' page.set_viewport_size({'width':1440,'height':1100})
 page.fill('#brief','<img src=x onerror="window.xssProbe=1"> 会员权益介绍')
 page.click('#create')
 try:
  page.wait_for_function("document.querySelectorAll('.candidate').length===3 && document.getElementById('status').textContent.includes('等待人工审批')",timeout=10000)
 except Exception:
  print('XSS diagnostic:',page.locator('#status').inner_text(),page.locator('#notice').inner_text(),page.locator('#candidates').inner_text())
  raise
 assert page.locator('img').count()==0
 assert page.evaluate('window.xssProbe !== 1')
 assert not errors,errors
 print('XSS-like model/user text rendered as plain text; mock only')
 browser.close()''')
        exec(compile(content,str(script),'exec'),{'__file__':str(script)})
    finally:
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:process.kill()
        process.wait(timeout=10)
