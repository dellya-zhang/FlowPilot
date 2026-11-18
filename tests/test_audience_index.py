import pytest
from app.core.config import get_settings
from app.services import audience_index
from fastapi import HTTPException
import httpx

@pytest.mark.asyncio
async def test_parameterized_clickhouse_query(monkeypatch):
    monkeypatch.setattr(get_settings(),"clickhouse_url","http://clickhouse:8123")
    async def execute(query,params):
        assert "{segment:String}" in query and "{spend:UInt64}" in query
        assert params["param_segment"]=="dormant" and params["param_spend"]=="50000"
        assert "FINAL" in query
        return '{"customer_id":3}\n{"customer_id":6}\n'
    monkeypatch.setattr(audience_index,"execute",execute)
    assert await audience_index.select_ids("dormant",{"min_lifetime_spend_cny":500})=={3,6}
    async def failed(*args):raise httpx.ConnectError("unavailable")
    monkeypatch.setattr(audience_index,"execute",failed)
    with pytest.raises(HTTPException) as err:await audience_index.select_ids("all",None)
    assert err.value.status_code==503
