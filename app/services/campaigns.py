import asyncio
import csv
import hashlib
import io
import logging
import random
from fastapi import HTTPException
from sqlalchemy import select, delete, update
from app.agents.marketing import marketing_graph
from app.db.base import SessionLocal
from app.models.entities import Campaign, Candidate, Event, Customer, Artifact, Experiment
from app.services.rules import check_marketing
from app.services.storage import put_blob, get_verified_blob
from botocore.exceptions import BotoCoreError
from app.core.config import get_settings
from app.services.leases import claim, fence, renew, release, LostLease

log = logging.getLogger(__name__)


async def emit(db, campaign_id: str, name: str, payload: dict, owner=None):
    if owner:
        await fence(db, campaign_id, owner)
    db.add(Event(campaign_id=campaign_id, name=name, payload=payload))
    await db.commit()


async def generate_candidates(campaign_id: str, gate=None):
    gate = gate or asyncio.Semaphore(get_settings().model_concurrency)
    owner = await claim(campaign_id)
    if owner is None:
        return
    parent = asyncio.current_task()

    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(get_settings().lease_seconds / 3)
                if not await renew(campaign_id, owner):
                    parent.cancel()
                    return
        except Exception:
            parent.cancel()

    pulse = asyncio.create_task(heartbeat())
    try:
        async with SessionLocal() as db:
            campaign = await db.get(Campaign, campaign_id)
            if campaign is None or campaign.status != "GENERATING":
                return
            # Idempotent recovery: discard partial outputs from an interrupted run.
            await fence(db, campaign_id, owner)
            await db.execute(delete(Candidate).where(Candidate.campaign_id == campaign_id))
            await db.commit()
            await emit(db, campaign_id, "generation.started", {"count": 3, "owner": owner}, owner=owner)
            sem = asyncio.Semaphore(2)

            async def one(variant: int):
                async with sem, gate:
                    result = await asyncio.wait_for(marketing_graph.ainvoke({
                        "brief": campaign.brief, "audience": campaign.audience,
                        "budget": campaign.budget, "variant": variant,
                    }), timeout=get_settings().candidate_timeout_seconds)
                    issues = check_marketing(result["title"] + "\n" + result["message"], campaign.budget)
                    if issues:
                        raise ValueError(f"Policy blocked candidate: {issues}")
                    return variant, result

            async def safe_one(variant):
                try:
                    return await one(variant)
                except Exception as exc:
                    return exc

            successes = 0
            children = [asyncio.create_task(safe_one(v)) for v in (1, 2, 3)]
            try:
                for future in asyncio.as_completed(children):
                    item = await future
                    if isinstance(item, Exception):
                        await emit(db, campaign_id, "candidate.failed", {"error": type(item).__name__}, owner=owner)
                        continue
                    variant, data = item
                    candidate = Candidate(campaign_id=campaign_id, variant=variant, title=data["title"][:150],
                                          message=data["message"], segment=campaign.audience,
                                          reasoning=data["reasoning"], score=data["score"])
                    await fence(db, campaign_id, owner)
                    db.add(candidate)
                    await db.flush()
                    cid = candidate.id
                    await emit(db, campaign_id, "candidate.ready", {"variant": variant, "candidate_id": cid}, owner=owner)
                    successes += 1
            finally:
                for child in children:
                    if not child.done():
                        child.cancel()
                await asyncio.gather(*children, return_exceptions=True)
            campaign.status = "WAITING_APPROVAL" if successes else "FAILED"
            await emit(db, campaign_id, "generation.finished", {"status": campaign.status, "successes": successes}, owner=owner)
    except LostLease:
        log.warning("Stale execution discarded: %s", campaign_id)
    except Exception as exc:
        log.exception("Campaign generation failed: %s", campaign_id)
        try:
            async with SessionLocal() as db:
                await fence(db, campaign_id, owner)
                campaign = await db.get(Campaign, campaign_id)
                if campaign and campaign.status == "GENERATING":
                    campaign.status = "FAILED"
                    await emit(db, campaign_id, "generation.failed", {"error": type(exc).__name__}, owner=owner)
        except LostLease:
            pass
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        try:
            await release(campaign_id, owner)
        except Exception:
            log.warning("Lease release failed; expiry will permit recovery: %s", campaign_id)


def generate_csv(rows: list[Customer]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["customer_id", "segment", "region"])
    for row in rows:
        writer.writerow([row.id, row.segment, row.region])
    return stream.getvalue().encode("utf-8-sig")


async def export_audience(db, campaign: Campaign, kind: str) -> Artifact:
    existing = await db.scalar(select(Artifact).where(Artifact.campaign_id == campaign.id, Artifact.kind == kind))
    if existing:
        return existing
    customer_query = select(Customer).where(Customer.consent.is_(True)).order_by(Customer.id)
    if campaign.audience != "all":
        customer_query = customer_query.where(Customer.segment == campaign.audience)
    customers = list((await db.scalars(customer_query)).all())
    pilot = await db.scalar(select(Artifact).where(Artifact.campaign_id == campaign.id, Artifact.kind == "pilot"))
    if kind == "full" and pilot:
        import io as _io
        piloted = {int(r["customer_id"]) for r in csv.DictReader(_io.StringIO((await asyncio.to_thread(get_verified_blob, pilot)).decode("utf-8-sig")))}
        customers = [c for c in customers if c.id not in piloted]
    if kind == "full":
        exp = await db.scalar(select(Experiment).where(Experiment.campaign_id == campaign.id))
        if exp:
            exposed = {uid for users in exp.arms.values() for uid in users}
            customers = [c for c in customers if c.id not in exposed]
    if kind == "pilot":
        seed = int(hashlib.sha256(campaign.id.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        customers = rng.sample(customers, k=min(25, len(customers)))
        customers.sort(key=lambda c: c.id)
    blob = generate_csv(customers)
    digest = hashlib.sha256(blob).hexdigest()
    key = f"campaigns/{campaign.id}/{kind}-{digest[:12]}.csv"
    try:
        await asyncio.to_thread(put_blob, key, blob)
    except (OSError, BotoCoreError) as exc:
        raise HTTPException(503, "Artifact storage unavailable", headers={"Retry-After":"2"}) from exc
    artifact = Artifact(campaign_id=campaign.id, kind=kind, key=key, sha256=digest, row_count=len(customers))
    db.add(artifact)
    await db.flush()
    return artifact


async def require_campaign(db, campaign_id: str) -> Campaign:
    campaign = await db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return campaign


async def transition(db, campaign_id: str, expected: str, target: str, event_name=None, event_payload=None, **values):
    """Compare-and-set in SQL: competing requests cannot both advance a phase."""
    result = await db.execute(update(Campaign).where(
        Campaign.id == campaign_id, Campaign.status == expected
    ).values(status=target, **values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Campaign changed; refresh before retrying")
    if event_name:
        db.add(Event(campaign_id=campaign_id, name=event_name, payload=event_payload or {}))
    await db.commit()
