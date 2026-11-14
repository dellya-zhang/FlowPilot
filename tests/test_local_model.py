"""Local model diagnostics must not become a public key/endpoint proxy."""
import json
import httpx
import pytest
from app.main import app, lifespan
from app.core.config import get_settings


@pytest.mark.asyncio
async def test_local_profile_never_exposes_credentials(monkeypatch,tmp_path):
    cfg=get_settings()
    for key,value in {'ai_provider':'deepseek','deepseek_api_key':'real-looking-secret-do-not-expose',
                      'llm_audit_path':str(tmp_path/'usage.jsonl')}.items():monkeypatch.setattr(cfg,key,value)
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://127.0.0.1:8000') as c:
            r=await c.get('/api/v1/local-model')
            assert r.status_code==200
            assert r.headers['Cache-Control']=='no-store' and r.headers['X-Frame-Options']=='DENY'
            assert r.json()['configured'] and not r.json()['credentials_in_browser']
            assert 'real-looking-secret' not in r.text and str(tmp_path) not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize('peer,host,origin',[
    ('203.0.113.5','127.0.0.1:8000',None),('127.0.0.1','evil.example',None),
    ('127.0.0.1','127.0.0.1:8000','https://evil.example'),
    ('127.0.0.1','127.0.0.1:8000','null'),
    ('127.0.0.1','127.0.0.1:8000','http://localhost:3000'),
    ('127.0.0.1','[',None),('127.0.0.1','127.0.0.1:8000','http://[')])
async def test_reject_remote_rebinding_and_cross_origin(peer,host,origin):
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(peer,1234)),base_url='http://127.0.0.1:8000') as c:
            headers={'host':host}
            if origin:headers['origin']=origin
            assert (await c.get('/api/v1/local-model',headers=headers)).status_code==403
            headers['X-Local-Model-Test']='1'
            assert (await c.post('/api/v1/local-model/test',headers=headers)).status_code==403


@pytest.mark.asyncio
async def test_local_probe_requires_explicit_bodyless_request():
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as c:
            assert (await c.post('/api/v1/local-model/test')).status_code==403
            headers={'X-Local-Model-Test':'1','Origin':'http://127.0.0.1:8000'}
            assert (await c.post('/api/v1/local-model/test',headers=headers,json={'api_key':'ignored','url':'http://evil'})).status_code==422
            r=await c.post('/api/v1/local-model/test',headers=headers)
            assert r.status_code==200 and r.json()['ok']
            assert r.json()['provider']=='mock'


@pytest.mark.asyncio
async def test_local_inference_stays_loopback_and_ignores_proxy(monkeypatch,tmp_path):
    from app.agents.provider import draft
    cfg=get_settings()
    for key,value in {'ai_provider':'local','local_model_name':'fixture-model',
                      'local_model_base_url':'http://127.0.0.1:11434/v1','local_model_api_key':'',
                      'llm_audit_path':str(tmp_path/'usage.jsonl')}.items():monkeypatch.setattr(cfg,key,value)
    def handle(request):
        assert request.url.host=='127.0.0.1'
        assert 'authorization' not in request.headers
        assert json.loads(request.content)['model']=='fixture-model'
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"title":"连接成功","message":"积分查询","reasoning":"固定需求"}'}}],
                                       'usage':{'total_tokens':30}})
    original=httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(handle),**kwargs)
    monkeypatch.setattr('app.agents.provider.httpx.AsyncClient',client)
    assert (await draft('本机推理测试','new',1))['title']=='连接成功'
    monkeypatch.setattr(cfg,'local_model_base_url','http://evil.example/v1')
    with pytest.raises(ValueError,match='loopback'):await draft('不能发往外部','new',1)


@pytest.mark.asyncio
async def test_local_probe_hides_upstream_errors(monkeypatch):
    async def fail(*args):raise ValueError('secret-upstream-key-and-body')
    monkeypatch.setattr('app.api.local_model.draft',fail)
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as c:
            r=await c.post('/api/v1/local-model/test',headers={'X-Local-Model-Test':'1'})
            assert r.status_code==502 and 'secret-upstream' not in r.text
