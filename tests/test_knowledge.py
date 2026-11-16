import json
import pytest
import httpx
from app.services.knowledge import split_document, search, answer, corpus
from app.core.config import get_settings
from app.main import app, lifespan


def test_chunk_overlap_and_stable_ids():
    chunks = split_document("test.md", "## 政策\n"+"甲乙丙丁"*200)
    assert len(chunks) > 1
    assert all(len(c.text) <= 280 for c in chunks)
    assert chunks[0].text[-40:] == chunks[1].text[:40]
    assert chunks == split_document("test.md", "## 政策\n"+"甲乙丙丁"*200)
    assert chunks[0].id != split_document("other.md", "## 政策\n"+"甲乙丙丁"*200)[0].id


@pytest.mark.parametrize("query,heading",[("退款条件是什么","退款政策"),("会员有什么权益","会员权益"),
    ("物流配送时效","物流政策"),("模型密钥保存在什么地方","个人数据与模型"),("全量名单排重","名单交付")])
def test_retrieval_cases(query, heading):
    assert search(query)[0]["heading"] == heading


@pytest.mark.asyncio
async def test_abstention_and_grounding():
    result = await answer("火星天气预报")
    assert result["abstained"] and not result["citations"]
    result = await answer("退款政策")
    assert result["mode"] == "extractive"
    assert "人工审核" in result["answer"]
    for i, hit in enumerate(result["citations"], 1):
        assert f"[{i}] {hit['text']}" in result["answer"]
        assert hit["chunk_id"] in {c.id for c in corpus()}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [False, True])
async def test_model_selection_and_budget(monkeypatch, tmp_path, bad):
    cfg = get_settings()
    for key,value in {"ai_provider":"deepseek","deepseek_api_key":"dummy", "llm_max_calls":2,
            "llm_budget_cny":10,"llm_audit_path":str(tmp_path/"audit.jsonl")}.items():
        monkeypatch.setattr(cfg,key,value)
    def handle(req):
        payload = json.loads(req.content)
        evidence = json.loads(payload["messages"][1]["content"])["evidence"]
        assert "退款" in evidence[0]["text"]
        ids = ["made-up"] if bad else [evidence[0]["chunk_id"]]
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{
            "content":json.dumps({"chunk_ids":ids})}}],"usage":{"total_tokens":50}})
    original = httpx.AsyncClient
    monkeypatch.setattr("app.agents.provider.httpx.AsyncClient",
        lambda **kw:original(transport=httpx.MockTransport(handle), **kw))
    if bad:
        with pytest.raises(ValueError,match="unknown citation"):
            await answer("退款政策", True)
    else:
        result = await answer("退款政策", True)
        assert result["mode"] == "model_selected_extractive"
        assert len(result["citations"]) == 1
    audit = [json.loads(line) for line in (tmp_path/"audit.jsonl").read_text().splitlines()]
    assert [row["event"] for row in audit] == ["reserved","settled"]


@pytest.mark.asyncio
async def test_api_limits_and_readonly():
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
            sid=(await client.post("/api/v1/support/sessions")).json()["session_id"]
            await client.post(f"/api/v1/support/sessions/{sid}/messages",json={"content":"我要退款"})
            result=await client.post("/api/v1/knowledge/answer",json={"question":"退款政策"})
            assert result.status_code == 200
            assert result.json()["citations"]
            # Knowledge answers cannot confirm or cancel pending business actions.
            confirmed=await client.post(f"/api/v1/support/sessions/{sid}/confirm?approved=false")
            assert confirmed.status_code == 200
            assert (await client.post("/api/v1/knowledge/answer",json={"question":"x"*501})).status_code == 422
            assert (await client.post("/api/v1/knowledge/answer",json={"question":"退款政策","url":"http://evil"})).status_code == 422


@pytest.mark.asyncio
async def test_model_can_abstain(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_provider", "deepseek")
    async def empty_selection(*args):
        return {"chunk_ids": []}
    monkeypatch.setattr("app.services.knowledge.complete_json", empty_selection)
    result = await answer("退款政策", True)
    assert result["abstained"] and not result["citations"]


@pytest.mark.asyncio
async def test_model_failure_releases_capacity(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("do not expose internal endpoint or key")
    async with lifespan(app):
        monkeypatch.setattr("app.api.knowledge.answer", broken)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
            before = app.state.model_slots._value
            result = await client.post("/api/v1/knowledge/answer",json={"question":"退款政策","use_model":True})
            assert result.status_code == 502
            assert "do not expose" not in result.text
            assert app.state.model_slots._value == before


@pytest.mark.asyncio
async def test_model_queue_is_bounded():
    import asyncio
    async with lifespan(app):
        slots = app.state.model_slots
        for _ in range(get_settings().model_concurrency):
            await slots.acquire()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
                result = await client.post("/api/v1/knowledge/answer",json={"question":"退款政策","use_model":True})
                assert result.status_code == 429
                assert slots._value == 0
        finally:
            for _ in range(get_settings().model_concurrency):
                slots.release()


@pytest.mark.asyncio
async def test_frontend_scripts_have_executable_mime():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
        for path in ["/app.js", "/demo.js"]:
            result = await client.get(path)
            assert result.status_code == 200
            assert result.headers["content-type"].startswith("text/javascript")
            assert result.headers["x-content-type-options"] == "nosniff"
