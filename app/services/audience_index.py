"""Optional ClickHouse projection for synthetic retail audience selection.
SQL remains authoritative for consent and the frozen campaign snapshot.
Configured ClickHouse errors fail closed; no silent engine substitution.
"""
import json
import httpx
from sqlalchemy import select
from fastapi import HTTPException
from app.core.config import get_settings
from app.models.entities import Customer,RetailProfile

async def execute(query,params=None):
    cfg=get_settings()
    async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
        response=await client.post(cfg.clickhouse_url.rstrip("/")+"/",params={"database":cfg.clickhouse_database,**(params or {})},
            auth=(cfg.clickhouse_user,cfg.clickhouse_password),content=query.encode("utf-8"))
        response.raise_for_status()
        if len(response.content)>2_000_000:raise ValueError("ClickHouse result exceeds local limit")
        return response.text

async def seed_index(db):
    if not get_settings().clickhouse_url:return
    await execute("""CREATE TABLE IF NOT EXISTS retail_audience (
        customer_id UInt32, segment LowCardinality(String), region LowCardinality(String),
        days_since_purchase UInt32, lifetime_spend_cents UInt64
        ) ENGINE=ReplacingMergeTree ORDER BY customer_id""")
    rows=(await db.execute(select(Customer,RetailProfile).join(RetailProfile,Customer.id==RetailProfile.customer_id))).all()
    body="\n".join(json.dumps({"customer_id":c.id,"segment":c.segment,"region":c.region,
        "days_since_purchase":p.days_since_purchase,"lifetime_spend_cents":p.lifetime_spend_cents}) for c,p in rows)
    if body:await execute("INSERT INTO retail_audience FORMAT JSONEachRow\n"+body)

async def select_ids(audience,data):
    cfg=get_settings()
    if not cfg.clickhouse_url:return None
    fields=[]
    params={}
    if audience!="all":
        fields.append("segment = {segment:String}");params["param_segment"]=audience
    if data and data.get("region","all")!="all":
        fields.append("region = {region:String}");params["param_region"]=data["region"]
    if data and data.get("min_days_since_purchase",0):
        fields.append("days_since_purchase >= {days:UInt32}");params["param_days"]=str(data["min_days_since_purchase"])
    if data and data.get("min_lifetime_spend_cny",0):
        fields.append("lifetime_spend_cents >= {spend:UInt64}");params["param_spend"]=str(data["min_lifetime_spend_cny"]*100)
    query="SELECT customer_id FROM retail_audience FINAL"
    if fields:query+=" WHERE "+" AND ".join(fields)
    query+=" ORDER BY customer_id LIMIT 10001 FORMAT JSONEachRow"
    try:
        rows=[json.loads(x)["customer_id"] for x in (await execute(query,params)).splitlines() if x.strip()]
        if len(rows)>10000:raise ValueError("Audience exceeds demo limit")
        return set(rows)
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        raise HTTPException(503,"Configured audience index unavailable") from exc
