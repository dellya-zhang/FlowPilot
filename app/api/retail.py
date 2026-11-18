from fastapi import APIRouter, Depends
from sqlalchemy import select
from app.db.base import session_dep
from app.models.entities import RetailProfile, RetailTicket, RetailSession
from app.services.retail import run_tool
from app.core.config import get_settings
from app.api.local_model import profile, usage_summary
import asyncio

router=APIRouter(prefix="/api/v1/retail",tags=["Synthetic retail"])

@router.get("/members")
async def members(db=Depends(session_dep)):
    rows=(await db.scalars(select(RetailProfile).order_by(RetailProfile.customer_id).limit(50))).all()
    return [{"id":p.customer_id,"tier":p.tier,"points":p.points,"days_since_purchase":p.days_since_purchase,"lifetime_spend_cny":p.lifetime_spend_cents/100} for p in rows]

@router.get("/sessions/{sid}/tickets")
async def tickets(sid:str,db=Depends(session_dep)):
    identity=await db.get(RetailSession,sid)
    if not identity:return []
    rows=(await db.scalars(select(RetailTicket).where(RetailTicket.customer_id==identity.customer_id).order_by(RetailTicket.created_at.desc()).limit(50))).all()
    return [{"id":t.id,"order_id":t.order_id,"status":t.status,"kind":t.kind} for t in rows]

@router.get("/runtime")
async def runtime():
    result=profile()
    try:result.update(await asyncio.to_thread(usage_summary))
    except Exception:result["audit_available"]=False
    return result
