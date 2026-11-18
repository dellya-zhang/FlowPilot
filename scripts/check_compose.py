"""Real Compose services: ClickHouse -> PostgreSQL consent -> LangGraph -> MinIO."""
import csv,io,time,hashlib
import httpx
with httpx.Client(base_url="http://127.0.0.1:8000/api/v1",timeout=30) as c:
    for _ in range(180):
        try:
            if c.get("/health").is_success:break
        except httpx.HTTPError:pass
        time.sleep(1)
    else:raise RuntimeError("Compose service unavailable")
    def query(method,path,**kw):
        response=c.request(method,path,**kw);response.raise_for_status();return response.json()
    cid=query("POST","/campaigns",json={"brief":"会员召回测试","audience":"dormant","budget":5000,
        "planning":{"known_benefits":"积分查询","min_days_since_purchase":30,"min_lifetime_spend_cny":500}})["id"]
    path="/campaigns/"+cid
    query("POST",path+"/generate")
    for _ in range(60):
        state=query("GET",path)["status"]
        if state!="GENERATING":break
        time.sleep(1)
    assert state=="WAITING_APPROVAL",state
    x=query("GET",path+"/candidates")[0]
    assert 0<x["plan_data"]["segment"]["treatment_size"]<167
    query("POST",path+"/approve",json={"candidate_id":x["id"],"expected_revision":1})
    artifact=query("POST",path+"/full")
    result=c.get("/artifacts/"+artifact["id"]+"/download");result.raise_for_status()
    assert hashlib.sha256(result.content).hexdigest()==artifact["sha256"]
    rows=list(csv.DictReader(io.StringIO(result.content.decode("utf-8-sig"))))
    assert len(rows)==artifact["row_count"] and all(row["segment"]=="dormant" for row in rows)
    print({"campaign":cid,"row_count":len(rows),"sha256":artifact["sha256"]})
