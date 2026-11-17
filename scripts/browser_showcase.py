"""Verify the read-only case, actual artifacts, and handoff to the existing workspace."""
import csv
import hashlib
import io
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
case=json.loads((root/'web/examples/case.json').read_text('utf-8'))
for artifact in case['artifacts']:
    payload=(root/'web/examples'/(artifact['kind']+'.csv')).read_bytes()
    assert hashlib.sha256(payload).hexdigest()==artifact['sha256']
    rows=list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
    assert len(rows)==artifact['row_count']
    assert {int(r['customer_id']) for r in rows}==set(artifact['users'])


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


with ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root/'web'))) as server:
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];network=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:network.append(r.url) if '/api/v1/' in r.url else None)
            page.goto(f'http://127.0.0.1:{server.server_port}/?demo=1')
            page.wait_for_selector('#caseSteps button')
            assert page.locator('#workspace').is_hidden()
            assert page.locator('#caseEvidence').inner_text().find('新用户')>=0
            page.screenshot(path=str(root/'docs/项目概览桌面.png'),full_page=True)
            page.locator('#caseSteps button').nth(1).click()
            assert page.locator('#caseEvidence article').count()==3
            page.locator('#caseSteps button').nth(2).click()
            assert 'v1' in page.locator('#caseEvidence').inner_text()
            page.locator('#caseSteps button').nth(3).click()
            assert '17 人' in page.locator('#caseEvidence').inner_text()
            assert '150 人' in page.locator('#caseEvidence').inner_text()
            assert '0 人 / 两份名单交集' in page.locator('#caseEvidence').inner_text()
            with page.expect_download() as result:
                page.get_by_role('link',name='下载本次 pilot.csv').click()
            assert result.value.suggested_filename=='pilot.csv'
            page.screenshot(path=str(root/'docs/案例交付桌面.png'),full_page=True)
            page.click('#caseSupport')
            assert '退款政策是什么' in page.locator('#caseEvidence').inner_text()
            page.locator('#caseSteps button').nth(1).click()
            assert '是否确认' in page.locator('#caseEvidence').inner_text()
            page.locator('#caseSteps button').nth(2).click()
            assert '已记录人工审核' in page.locator('#caseEvidence').inner_text()
            page.screenshot(path=str(root/'docs/客服案例桌面.png'),full_page=True)
            # Reading the case must not silently create tasks or support sessions.
            assert page.evaluate('JSON.parse(localStorage.getItem("flowpilot-public-demo-v1"))?.campaigns.length || 0')==0
            page.set_viewport_size({'width':390,'height':844})
            for mode in ['caseMarketing','caseSupport']:
                page.click('#'+mode)
                for button in page.locator('#caseSteps button').all():
                    button.click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.click('#caseMarketing')
            page.screenshot(path=str(root/'docs/项目概览手机.png'),full_page=True)
            page.locator('[data-view=marketing]').click()
            page.locator('#workspace').wait_for(state='visible')
            page.locator('[data-view=overview]').click()
            page.locator('#projectOverview').wait_for(state='visible')
            assert not errors,errors
            assert not network,network
            browser.close()
            print('PASS: artifact hashes/rows, all case steps, downloads, read-only state, mobile, navigation; no API requests')
    finally:
        server.shutdown();thread.join(timeout=2)
