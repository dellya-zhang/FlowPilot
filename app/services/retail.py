"""Deterministic fictional retail data and session-scoped read tools.
Never expose a caller/model-supplied customer_id in a tool schema.
"""
from sqlalchemy import select
from app.models.entities import Customer, RetailProfile, RetailOrder, RetailSession, RetailTicket
from app.services.knowledge import search
from pydantic import BaseModel, ConfigDict, Field

async def seed_retail(db):
    existing=set((await db.scalars(select(RetailProfile.customer_id))).all())
    customers=(await db.scalars(select(Customer).order_by(Customer.id))).all()
    for c in customers:
        if c.id in existing:continue
        i=c.id
        profile=RetailProfile(customer_id=i,tier=("普通","银卡","金卡")[i%3],
            points=100+i*17,days_since_purchase=(45+i%70 if c.segment=="dormant" else i%25),
            lifetime_spend_cents=15000+i*1730)
        db.add(profile)
        for suffix,item,amount,status,logistics in [
            ("A","燕麦拿铁",2800,"DELIVERED","已签收"),
            ("B","手冲咖啡礼盒",10900,"SHIPPED","配送途中")]:
            db.add(RetailOrder(id=f"FP-{i:04d}-{suffix}",customer_id=i,item=item,
                amount_cents=amount,status=status,logistics=logistics))
    await db.commit()

class Empty(BaseModel):
    model_config=ConfigDict(extra="forbid")
class OrderArgs(Empty):
    order_id: str = Field(min_length=1,max_length=32)
class PolicyArgs(Empty):
    query: str = Field(min_length=2,max_length=200)

def schema(name, description, args):
    return {"type":"function","function":{"name":name,"description":description,
        "parameters":args.model_json_schema()}}

REGISTRY={
    "query_member":(Empty,schema("query_member","查询当前合成会员等级、积分和消费画像",Empty)),
    "list_orders":(Empty,schema("list_orders","列出当前合成会员的订单号、商品、价格和状态",Empty)),
    "query_logistics":(OrderArgs,schema("query_logistics","查询当前会员指定订单的物流",OrderArgs)),
    "check_refund_eligibility":(OrderArgs,schema("check_refund_eligibility","检查是否可提交人工退款审核；不执行退款",OrderArgs)),
    "search_policy":(PolicyArgs,schema("search_policy","检索零售会员政策的真实原文",PolicyArgs)),
}
TOOLS=[v[1] for v in REGISTRY.values()]

async def run_tool(db, customer_id, name, raw):
    if name not in REGISTRY:raise ValueError("Unknown read tool")
    args=REGISTRY[name][0].model_validate(raw)
    if name=="query_member":
        p=await db.get(RetailProfile,customer_id)
        return {"tier":p.tier,"points":p.points,"days_since_purchase":p.days_since_purchase,
                "lifetime_spend_cents":p.lifetime_spend_cents} if p else {"error":"member_not_found"}
    if name=="list_orders":
        rows=(await db.scalars(select(RetailOrder).where(RetailOrder.customer_id==customer_id)
                              .order_by(RetailOrder.id).limit(10))).all()
        return {"orders":[{"order_id":o.id,"item":o.item,"amount_cents":o.amount_cents,"status":o.status} for o in rows]}
    if name=="search_policy":
        return {"citations":[h for h in search(args.query,top_k=3) if h["heading"] in ("退款政策","会员权益","物流政策")]}
    order=await db.get(RetailOrder,args.order_id.upper())
    if not order or order.customer_id!=customer_id:
        return {"error":"order_not_found_for_current_member"}
    if name=="query_logistics":
        return {"order_id":order.id,"logistics":order.logistics}
    return {"order_id":order.id,"eligible_for_review":order.status in ("DELIVERED","SHIPPED"),
            "note":"仅允许人工审核，不代表批准或退款到账"}

async def retail_identity(db,session_id):
    row=await db.get(RetailSession,session_id)
    return row.customer_id if row else None

async def submit_refund(db, session_id, customer_id, order_id):
    eligibility=await run_tool(db,customer_id,"check_refund_eligibility",{"order_id":order_id})
    if not eligibility.get("eligible_for_review"):return None
    old=await db.scalar(select(RetailTicket).where(RetailTicket.customer_id==customer_id,
        RetailTicket.order_id==order_id,RetailTicket.kind=="refund",RetailTicket.status=="PENDING_REVIEW").limit(1))
    if old:return old
    ticket=RetailTicket(session_id=session_id,customer_id=customer_id,order_id=order_id,kind="refund")
    db.add(ticket);await db.flush()
    return ticket
