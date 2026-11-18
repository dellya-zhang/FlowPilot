"""Protocol-level tests use mock HTTP responses, not paid model credentials."""
import json
import httpx
import pytest
from app.agents.provider import complete_tools,draft
from app.core.config import get_settings
from app.services.retail import TOOLS

@pytest.mark.asyncio
async def test_native_provider_receipts(monkeypatch,tmp_path):
    cfg=get_settings()
    for k,v in {"ai_provider":"deepseek","deepseek_api_key":"dummy","llm_audit_path":str(tmp_path/"audit.jsonl"),"llm_max_calls":5,"llm_budget_cny":10}.items():monkeypatch.setattr(cfg,k,v)
    def handler(req):
        body=json.loads(req.content)
        assert body["thinking"]=={"type":"disabled"}
        assert req.headers["Authorization"]=="Bearer dummy"
        if "tools" in body:
            assert "response_format" not in body and body["tool_choice"]=="auto"
            return httpx.Response(200,json={"id":"tool-fixture","choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":None,"tool_calls":[{"id":"a","type":"function","function":{"name":"list_orders","arguments":"{}"}}]}}],"usage":{"total_tokens":80}})
        return httpx.Response(200,json={"id":"draft-fixture","choices":[{"finish_reason":"stop","message":{"content":json.dumps({"title":"会员权益","message":"欢迎查询积分","reasoning":"权益已知","outcome":"draft"},ensure_ascii=False)}}],"usage":{"total_tokens":50}})
    original=httpx.AsyncClient
    monkeypatch.setattr("app.agents.provider.httpx.AsyncClient",lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=await complete_tools([{"role":"user","content":"查订单"}],TOOLS)
    assert result["response_id"]=="tool-fixture"
    result=await draft("会员权益介绍","new",1)
    assert result["model_receipt"]["provider_response_id"]=="draft-fixture"
    audit=[json.loads(row) for row in (tmp_path/"audit.jsonl").read_text().splitlines()]
    assert [r["provider_response_id"] for r in audit if r["event"]=="settled"]==["tool-fixture","draft-fixture"]
