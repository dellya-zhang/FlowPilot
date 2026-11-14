import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import session_dep, SessionLocal
from app.models.entities import Campaign, Candidate, Event, Artifact, SupportSession, Experiment
from app.schemas.contracts import (CampaignCreate, CampaignView, CandidateView, Approval,
                                   ArtifactView, SupportMessageInput, SupportDecision)
from app.services.campaigns import generate_candidates, export_audience, require_campaign, emit, transition
from app.services.storage import get_verified_blob
from app.services.support import send_message, confirm_action
from app.services.limiter import limit
from app.services.support import history
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/campaigns", response_model=CampaignView, status_code=201)
async def create_campaign(data: CampaignCreate, db: AsyncSession = Depends(session_dep)):
    if data.budget > 100_000:
        raise HTTPException(422, "Budget exceeds demo policy; no model call was made")
    campaign = Campaign(**data.model_dump())
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


@router.get("/campaigns", response_model=list[CampaignView])
async def list_campaigns(db: AsyncSession = Depends(session_dep)):
    return list((await db.scalars(select(Campaign).order_by(Campaign.created_at.desc()).limit(100))).all())


@router.get("/campaigns/{campaign_id}", response_model=CampaignView)
async def get_campaign(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    return await require_campaign(db, campaign_id)


@asynccontextmanager
async def generation_slot(app):
    # Reserve before any DB await: len(tasks) alone races under request bursts.
    async with app.state.admission_lock:
        if len(app.state.tasks) + app.state.pending_admissions >= get_settings().max_active_campaigns:
            raise HTTPException(429, "Campaign capacity reached", headers={"Retry-After":"2"})
        app.state.pending_admissions += 1
    try:
        yield
    finally:
        async with app.state.admission_lock:
            app.state.pending_admissions -= 1


@router.post("/campaigns/{campaign_id}/generate", status_code=202)
async def start_generate(campaign_id: str, request: Request, db: AsyncSession = Depends(session_dep)):
    await limit(request, "generate", 10)
    async with generation_slot(request.app):
        campaign = await require_campaign(db, campaign_id)
        if campaign.status != "CREATED":
            raise HTTPException(409, f"Cannot generate in {campaign.status}")
        await transition(db, campaign_id, "CREATED", "GENERATING", event_name="generation.queued")
        task = asyncio.create_task(generate_candidates(campaign.id, request.app.state.model_slots))
        request.app.state.track_task(task)
        return {"campaign_id": campaign.id, "status": "GENERATING"}


@router.get("/campaigns/{campaign_id}/candidates", response_model=list[CandidateView])
async def candidates(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    return list((await db.scalars(select(Candidate).where(Candidate.campaign_id == campaign_id)
                                 .order_by(Candidate.variant))).all())


@router.get("/campaigns/{campaign_id}/events")
async def get_events(campaign_id: str, after: int = Query(default=0, ge=0, le=9223372036854775807), db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    events = (await db.scalars(select(Event).where(Event.campaign_id == campaign_id, Event.id > after)
                               .order_by(Event.id).limit(200))).all()
    return [{"id": e.id, "name": e.name, "payload": e.payload} for e in events]


@router.get("/campaigns/{campaign_id}/stream")
async def event_stream(campaign_id: str, request: Request, after: int = Query(default=0, ge=0, le=9223372036854775807),
                       db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    header = request.headers.get("last-event-id")
    if header:
        if not header.isascii() or not header.isdecimal() or len(header) > 19 or int(header) > 9223372036854775807:
            raise HTTPException(422, "Invalid Last-Event-ID")
        after = max(after, int(header))
    # The SSE response must not retain a DB connection for its lifetime.
    await db.rollback()

    async def events():
        cursor = after
        while not await request.is_disconnected():
            async with SessionLocal() as stream_db:
                rows = (await stream_db.scalars(select(Event).where(Event.campaign_id == campaign_id,
                        Event.id > cursor).order_by(Event.id).limit(100))).all()
            if rows:
                for event in rows:
                    cursor = event.id
                    yield f"id: {event.id}\nevent: {event.name}\ndata: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.6)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@router.post("/campaigns/{campaign_id}/approve", response_model=CampaignView)
async def approve(campaign_id: str, data: Approval, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if await db.scalar(select(Experiment.id).where(Experiment.campaign_id == campaign_id)):
        raise HTTPException(409, "Campaign belongs to an experiment; use promote")
    if campaign.status != "WAITING_APPROVAL":
        raise HTTPException(409, f"Cannot approve in {campaign.status}")
    candidate = await db.get(Candidate, data.candidate_id)
    if not candidate or candidate.campaign_id != campaign_id:
        raise HTTPException(422, "Candidate does not belong to campaign")
    await transition(db, campaign_id, "WAITING_APPROVAL", "PILOT_READY", selected_candidate_id=candidate.id,
                     event_name="campaign.approved", event_payload={"candidate_id":candidate.id})
    await db.refresh(campaign)
    return campaign


@router.post("/campaigns/{campaign_id}/pilot", response_model=ArtifactView)
async def pilot(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if campaign.status not in ("PILOT_READY", "PILOT_EXPORTED", "COMPLETED"):
        raise HTTPException(409, "Approve a candidate before pilot export")
    if await db.scalar(select(Experiment.id).where(Experiment.campaign_id == campaign_id)):
        raise HTTPException(409, "Experiment cohorts already define the pilot; no separate pilot export")
    artifact = await export_audience(db, campaign, "pilot")
    if campaign.status == "PILOT_READY":
        campaign.status = "PILOT_EXPORTED"
    await emit(db, campaign_id, "pilot.exported", {"artifact_id": artifact.id, "rows": artifact.row_count})
    return artifact


@router.post("/campaigns/{campaign_id}/finalize", response_model=ArtifactView)
async def finalize(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await db.execute(update(Campaign).where(Campaign.id == campaign_id).values(updated_at=Campaign.updated_at))
    campaign = await require_campaign(db, campaign_id)
    if campaign.status not in ("PILOT_EXPORTED", "COMPLETED"):
        raise HTTPException(409, "Pilot export required before full delivery")
    artifact = await export_audience(db, campaign, "full")
    campaign.status = "COMPLETED"
    await emit(db, campaign_id, "campaign.completed", {"artifact_id": artifact.id, "rows": artifact.row_count})
    return artifact


@router.get("/campaigns/{campaign_id}/artifacts", response_model=list[ArtifactView])
async def artifacts(campaign_id: str, db: AsyncSession = Depends(session_dep)):
    await require_campaign(db, campaign_id)
    return list((await db.scalars(select(Artifact).where(Artifact.campaign_id == campaign_id))).all())


@router.get("/artifacts/{artifact_id}/download")
async def download(artifact_id: str, db: AsyncSession = Depends(session_dep)):
    artifact = await db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    body = await asyncio.to_thread(get_verified_blob, artifact)
    return Response(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{artifact.kind}-{artifact.id}.csv"',
        "X-Content-SHA256": artifact.sha256})


@router.post("/support/sessions", status_code=201)
async def new_support_session(db: AsyncSession = Depends(session_dep)):
    session = SupportSession()
    db.add(session)
    await db.commit()
    return {"session_id": session.id, "status": session.status}


@router.post("/support/sessions/{session_id}/messages", response_model=SupportDecision)
async def support_message(session_id: str, data: SupportMessageInput, request: Request,
                          db: AsyncSession = Depends(session_dep)):
    await limit(request, "support", 30)
    return await send_message(db, session_id, data.content)


@router.post("/support/sessions/{session_id}/confirm", response_model=SupportDecision)
async def support_confirm(session_id: str, approved: bool, db: AsyncSession = Depends(session_dep)):
    return await confirm_action(db, session_id, approved)


@router.get("/support/sessions/{session_id}/messages")
async def support_history(session_id: str, db: AsyncSession = Depends(session_dep)):
    return await history(db, session_id)
