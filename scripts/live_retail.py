"""Opt-in real model acceptance. Run against the local backend only."""
import argparse
import time
import httpx
from pathlib import Path
import json
p=argparse.ArgumentParser()
p.add_argument("--confirm-paid",action="store_true")
p.add_argument("--port",type=int,default=8000)
args=p.parse_args()
if not args.confirm_paid:p.error("This makes paid model requests. Pass --confirm-paid to proceed.")
from app.core.config import get_settings
cfg=get_settings()
headers={"Authorization":"Bearer "+cfg.access_token} if cfg.access_token else {}
with httpx.Client(base_url=f"http://127.0.0.1:{args.port}/api/v1",headers=headers,timeout=120) as c:
    def req(method,path,**kwargs):
        r=c.request(method,path,**kwargs);r.raise_for_status();return r.json()
    runtime=req("GET","/retail/runtime")
    assert runtime["provider"]=="deepseek" and runtime["configured"],"Configure DeepSeek locally first"
    cid=req("POST","/campaigns",json={"brief":"为近30天未消费的会员介绍已知积分查询服务；仅使用已确认权益","audience":"dormant","budget":5000,
        "planning":{"known_benefits":"积分查询、专属客服；不提供额外优惠","min_days_since_purchase":30,"min_lifetime_spend_cny":500}})["id"]
    req("POST",f"/campaigns/{cid}/generate")
    for _ in range(120):
        state=req("GET",f"/campaigns/{cid}")["status"]
        if state!="GENERATING":break
        time.sleep(1)
    candidates=req("GET",f"/campaigns/{cid}/candidates")
    assert state=="WAITING_APPROVAL" and candidates and all(x["plan_data"]["content_source"]=="deepseek" for x in candidates),state
    assert all(x["plan_data"].get("generation_receipt",{}).get("provider_response_id") for x in candidates),"Missing provider response IDs in candidates"
    sid=req("POST","/support/sessions?member_id=1")["session_id"]
    result=req("POST",f"/support/sessions/{sid}/messages",json={"content":"请查询我的订单与会员积分","use_model":True})
    assert result["decision"]["mode"]=="model_tools",result["decision"]
    trace=result["decision"]["trace"]
    assert any(item.get("provider_response_id") for item in trace),"No provider response ID"
    print(json.dumps({"campaign_id":cid,"candidate_count":len(candidates),"candidate_sources":[x["plan_data"]["content_source"] for x in candidates],"generation_receipts":[x["plan_data"]["generation_receipt"] for x in candidates],"support_trace":trace,"runtime":req("GET","/retail/runtime")},ensure_ascii=False,indent=2))
