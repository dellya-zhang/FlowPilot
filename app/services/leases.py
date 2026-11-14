"""SQL leases and owner fencing for same-host workers sharing a database."""
import time
import uuid
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from app.db.base import SessionLocal
from app.models.entities import ExecutionLease
from app.core.config import get_settings


class LostLease(RuntimeError):
    pass


async def claim(campaign_id):
    owner=uuid.uuid4().hex;now=time.time()
    async with SessionLocal() as db:
        r=await db.execute(update(ExecutionLease).where(ExecutionLease.campaign_id==campaign_id,
            ExecutionLease.expires_at<=now).values(owner=owner,expires_at=now+get_settings().lease_seconds))
        if r.rowcount:
            await db.commit();return owner
        if await db.get(ExecutionLease,campaign_id):
            await db.rollback();return None
        db.add(ExecutionLease(campaign_id=campaign_id,owner=owner,expires_at=now+get_settings().lease_seconds))
        try:await db.commit()
        except IntegrityError:
            await db.rollback();return None
        return owner


async def fence(db,campaign_id,owner):
    r=await db.execute(update(ExecutionLease).where(ExecutionLease.campaign_id==campaign_id,
        ExecutionLease.owner==owner,ExecutionLease.expires_at>time.time()).values(expires_at=ExecutionLease.expires_at))
    if r.rowcount!=1:
        await db.rollback()
        raise LostLease('Execution ownership lost')


async def renew(campaign_id,owner):
    now=time.time()
    async with SessionLocal() as db:
        r=await db.execute(update(ExecutionLease).where(ExecutionLease.campaign_id==campaign_id,
            ExecutionLease.owner==owner,ExecutionLease.expires_at>now).values(expires_at=now+get_settings().lease_seconds))
        await db.commit()
        return r.rowcount==1


async def release(campaign_id,owner):
    async with SessionLocal() as db:
        await db.execute(update(ExecutionLease).where(ExecutionLease.campaign_id==campaign_id,
            ExecutionLease.owner==owner).values(expires_at=0))
        await db.commit()
