"""Run the GitHub Pages preview against a plain static server, no API/backend."""
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread

root=Path(__file__).resolve().parents[1]
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
with ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(root/'web'))) as server:
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        script=root/'scripts/browser_e2e.py'
        content=script.read_text('utf-8').replace('http://127.0.0.1:8017',f'http://127.0.0.1:{server.server_port}/?demo=1')
        content=content.replace("root/'工作台预览.png'","root/'在线演示桌面.png'").replace("root/'移动端预览.png'","root/'在线演示手机.png'")
        content=content.replace(' errors=[]'," errors=[]\n network=[]\n page.on('request',lambda req: network.append(req.url) if '/api/v1/' in req.url or 'api.deepseek.com' in req.url else None)")
        content=content.replace(' browser.close()',""" assert page.locator('#testModel').is_disabled()
 assert network==[],network
 print('Static interactive demo passed; no backend or model network requests')
 browser.close()""")
        exec(compile(content,str(script),'exec'),{'__file__':str(script)})
    finally:
        server.shutdown();thread.join(timeout=2)
