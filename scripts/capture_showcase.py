"""Record a completed synthetic browser case, including its actual CSV outputs."""
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


with ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root/'web'))) as server:
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/?demo=1#marketing')
            page.click('[data-scenario="member"]')
            page.click('#create')
            page.wait_for_function("() => document.querySelectorAll('.candidate [data-action=approve]:enabled').length===3")
            page.locator('.candidate [data-action=approve]').first.click()
            page.click('#pilot')
            page.wait_for_function("() => !document.getElementById('finalize').disabled")
            page.click('#finalize')
            page.wait_for_function("() => document.getElementById('status').textContent.includes('全量已交付')")
            case = page.evaluate('''async () => {
              const c=(await api('/campaigns'))[0];
              const candidates=await api('/campaigns/'+c.id+'/candidates');
              const events=await api('/campaigns/'+c.id+'/events');
              const artifacts=await api('/campaigns/'+c.id+'/artifacts');
              const files=[];
              for(const a of artifacts) files.push({kind:a.kind,text:await (await fetch('/api/v1/artifacts/'+a.id+'/download')).text()});
              const s=await api('/support/sessions','POST');
              const session=s.session_id;
              await api('/support/sessions/'+session+'/messages','POST',{content:'退款政策是什么'});
              const policy=await api('/support/sessions/'+session+'/messages');
              await api('/support/sessions/'+session+'/messages','POST',{content:'我要退款'});
              const pending=await api('/support/sessions/'+session+'/messages');
              await api('/support/sessions/'+session+'/confirm?approved=true','POST');
              const confirmed=await api('/support/sessions/'+session+'/messages');
              return {source:'Recorded browser-template simulation; not a backend/model run or business result',campaign:c,candidates,events,artifacts,files,support:{policy,pending,confirmed}};
            }''')
            pilot = next(a for a in case['artifacts'] if a['kind']=='pilot')
            full = next(a for a in case['artifacts'] if a['kind']=='full')
            assert set(pilot['users']).isdisjoint(full['users'])
            assert pilot['row_count'] + full['row_count'] == 167  # new segment; none withdrawn in this fixture
            out = root/'web/examples'
            out.mkdir(exist_ok=True)
            for file in case.pop('files'):
                # Response.text() strips the UTF-8 BOM used by the CSV export.
                (out/(file['kind']+'.csv')).write_bytes(('\ufeff'+file['text'].lstrip('\ufeff')).encode('utf-8'))
            (out/'case.json').write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding='utf-8')
            browser.close()
            print(f"Recorded case: {len(case['candidates'])} candidates; {pilot['row_count']} pilot + {full['row_count']} full; zero overlap")
    finally:
        server.shutdown()
        thread.join(timeout=2)

