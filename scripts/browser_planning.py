"""Browser contracts for planning against both static simulation and real Mock API."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path
import os
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright
from extreme_http import launch, port, stop

ROOT=Path(__file__).resolve().parents[1]
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass


def check(browser,url,label):
    context=browser.new_context(viewport={'width':1440,'height':1100})
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url)
    page.locator('.planning-fields>summary').click()
    page.fill('#knownBenefits','')
    page.click('#create')
    page.wait_for_function("() => document.getElementById('phaseBadge').textContent.includes('等待补充')")
    assert page.locator('.candidate').count()==0
    assert page.locator('#planningQuestions').is_visible()
    page.fill('#knownBenefits','积分查询与专属客服；不提供额外优惠')
    page.select_option('#planRegion','east')
    page.fill('#unitCost','0.2')
    page.check('#enableHoldout')
    page.click('#clarifyPlan');page.wait_for_selector('#resumePlan:visible')
    page.click('#resumePlan')
    page.wait_for_function("() => document.querySelectorAll('.candidate [data-action=approve]:enabled').length===3")
    assert page.locator('.strategy-preview').count()==3
    page.locator('.strategy-preview details').first.locator('summary').first.click()
    text=page.locator('.strategy-preview').first.inner_text()
    assert '权益依据' in text and '预算测算' in text and '东区' in text
    page.click('#experiment');page.wait_for_selector('.experiment-arm')
    # Simulate losing tab-local cache: server/local persistent state is the source.
    page.evaluate("() => Object.keys(sessionStorage).filter(k=>k.startsWith('exp-')).forEach(k=>sessionStorage.removeItem(k))")
    page.reload();page.locator('#tasks button').first.click();page.wait_for_selector('.experiment-arm')
    assert page.locator('.candidate [data-action=approve]:enabled').count()==0
    page.get_by_role('button',name='人工选为胜出方案').first.click()
    page.wait_for_function("() => !document.getElementById('finalize').disabled")
    page.click('#finalize')
    page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
    page.locator('.strategy-preview details').first.locator('summary').first.click()
    if label=='backend':page.screenshot(path=str(ROOT/'docs/结构化策略桌面.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if label=='backend':page.screenshot(path=str(ROOT/'docs/结构化策略手机.png'),full_page=True)
    # Preflight rejects expensive plans and leaves the created task editable.
    page.fill('#unitCost','1000');page.fill('#budget','100');page.click('#create')
    page.wait_for_function("() => document.getElementById('notice').textContent.length>0")
    assert page.locator('#resumePlan').is_visible()
    assert page.locator('.candidate').count()==0
    assert not errors,errors
    print(label+': clarification, strategy fields, holdout parameters, experiment restore, export, mobile and budget preflight passed')
    context.close()


def run_checks(checker=check):
    with tempfile.TemporaryDirectory() as work:
        work=Path(work);apiport=port()
        env={**os.environ,'AI_PROVIDER':'mock','DATABASE_URL':'sqlite+aiosqlite:///'+str(work/'test.db'),'ARTIFACT_DIR':str(work/'objects'),'REDIS_URL':'','S3_ENDPOINT_URL':'','ACCESS_TOKEN':'','LLM_AUDIT_PATH':str(work/'no-paid.jsonl')}
        process=launch(env,apiport)
        try:
            is_ready=False
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError("Mock backend exited during startup")
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{apiport}/api/v1/health',timeout=.5) as r:
                        if r.status==200:
                            is_ready=True;break
                except OSError:time.sleep(.1)
            if not is_ready:raise RuntimeError('Mock backend readiness timed out')
            with ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(ROOT/'web'))) as server:
                thread=Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with sync_playwright() as p:
                        browser=p.chromium.launch(headless=True,channel='msedge')
                        checker(browser,f'http://127.0.0.1:{server.server_port}/?demo=1','static')
                        checker(browser,f'http://127.0.0.1:{apiport}/','backend')
                        browser.close()
                finally:server.shutdown();thread.join(timeout=2)
        finally:stop(process)


if __name__=='__main__':
    run_checks()
